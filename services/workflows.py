import os
import re
import uuid
import json
import base64
import hashlib
import hmac
import math
import secrets
import smtplib
import ssl
import time
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from functools import wraps
from decimal import Decimal
from urllib.parse import urlsplit
from dotenv import dotenv_values
import mysql.connector
import requests
from PIL import Image, UnidentifiedImageError
from flask import request, redirect, url_for, session, flash, abort, current_app
from werkzeug.utils import secure_filename
from database import db, execute, query_all, query_one
from config import (
    ALLOWED_DELIVERY_CITY_CODES,
    ALLOWED_DELIVERY_CITY_CODES_CSV,
    ALLOWED_DELIVERY_CITY_NAMES,
    ALLOWED_DELIVERY_MUNICIPALITIES,
    ALLOWED_EXTENSIONS,
    BASE_DIR,
    LAGUNA_PROVINCE_CODE,
    LAGUNA_REGION_CODE,
    GEOCODER_URL,
    LOCAL_DELIVERY_CITY_CODE,
    OUTSIDE_LOCAL_DELIVERY_FEE,
    ORDER_STAGES,
    PAYMENT_PROOF_DIR,
    PAYMENT_PROOF_MAX_BYTES,
    PICKUP_ADDRESS,
    ROUTING_URL,
    SHOP_LOCATION_ADDRESS,
    SHOP_LOCATION_LATITUDE,
    SHOP_LOCATION_LONGITUDE,
    UPLOAD_DIR,
    VERIFICATION_IMAGE_MAX_BYTES,
    VERIFICATION_UPLOAD_DIR,
)
from .schema import (
    ensure_admin_operations_schema,
    ensure_chat_schema,
    ensure_custom_materials_schema,
    ensure_customer_cart_schema,
    ensure_delivery_map_schema,
    ensure_delivery_slots_schema,
    ensure_gcash_schema,
    ensure_gcash_settings_schema,
    ensure_notification_link_schema,
    ensure_order_messaging_schema,
    ensure_order_queue_schema,
)
_geocoder_lock = threading.Lock()


_last_geocoder_request = 0.0


_ph_location_data = None


_ph_barangays_by_city = None


def classify_order_stage(order):
    status = order.get("order_status") or order.get("status")
    if status == "Cancelled":
        return "cancelled"
    if status in {"Delivered", "Picked Up"}:
        return "delivered"
    if order.get("payment_method") == "GCash" and order.get("payment_status") != "Paid":
        return "to_pay"
    if status in {"Ready for Delivery", "Ready for Pickup", "Out for Delivery"}:
        return "to_receive"
    return "to_ship"


def filter_orders_by_stage(orders, requested_stage):
    stages = {key for key, _ in ORDER_STAGES}
    active_stage = requested_stage if requested_stage in stages else "all"
    counts = {key: 0 for key in stages}
    for order in orders:
        order["_stage"] = classify_order_stage(order)
        counts[order["_stage"]] += 1
    counts["all"] = len(orders)
    counts["open"] = sum(
        order["_stage"] not in {"delivered", "cancelled"} for order in orders
    )
    if active_stage == "all":
        filtered = orders
    elif active_stage == "open":
        filtered = [
            order for order in orders
            if order["_stage"] not in {"delivered", "cancelled"}
        ]
    else:
        filtered = [order for order in orders if order["_stage"] == active_stage]
    return filtered, counts, active_stage


def log_admin_activity(action, target_type, target_id, details=""):
    ensure_admin_operations_schema()
    user = current_user()
    execute(
        """INSERT INTO admin_activity_logs
           (admin_id,action,target_type,target_id,details) VALUES (%s,%s,%s,%s,%s)""",
        (user["id"] if user else None, action, target_type,
         str(target_id) if target_id is not None else None, details[:1000]),
    )


def calculate_voucher_discount(voucher, subtotal, already_used):
    if not voucher or not voucher["is_active"]:
        raise ValueError("That promo code is invalid or inactive.")
    if voucher["expires_at"] and voucher["expires_at"] <= datetime.now():
        raise ValueError("That promo code has expired.")
    if voucher["max_uses"] is not None and voucher["used_count"] >= voucher["max_uses"]:
        raise ValueError("That promo code has reached its redemption limit.")
    if already_used:
        raise ValueError("You have already used that promo code.")
    if subtotal < Decimal(str(voucher["minimum_subtotal"])):
        raise ValueError("Your bouquet subtotal does not meet this promo code's minimum.")
    value = Decimal(str(voucher["discount_value"]))
    discount = (
        subtotal * value / Decimal("100")
        if voucher["discount_type"] == "Percent"
        else value
    ).quantize(Decimal("0.01"))
    discount = min(discount, subtotal)
    if discount <= 0:
        raise ValueError("That promo code does not apply a valid discount.")
    return discount


def delivery_fee_for_address(address, city_code=None, fulfillment_method="Delivery"):
    if fulfillment_method == "Pickup":
        return Decimal("0.00")

    is_local_delivery = False
    if city_code:
        city = philippine_location_data()["cities"].get(str(city_code))
        province = (
            philippine_location_data()["provinces"].get(city.get("provinceCode"))
            if city else None
        )
        is_local_delivery = bool(
            city
            and province
            and city["name"].strip().casefold() == "cavinti"
            and province["name"].strip().casefold() == "laguna"
        )
    elif address:
        is_local_delivery = bool(
            re.search(r"(?:^|,\s*)Cavinti\s*,\s*Laguna(?:,|$)", address, re.IGNORECASE)
        )

    return Decimal("0.00") if is_local_delivery else OUTSIDE_LOCAL_DELIVERY_FEE


def notify_customers_about_voucher(name, code, discount_type, discount_value, expires_at, link_url):
    ensure_notification_link_schema()
    discount_label = (
        f"{Decimal(str(discount_value)).normalize()}% off"
        if discount_type == "Percent"
        else f"₱{Decimal(str(discount_value)):.2f} off"
    )
    expiration = (
        f" It expires {expires_at.strftime('%b %d, %Y at %I:%M %p')}."
        if expires_at else ""
    )
    execute(
        """INSERT INTO notifications (user_id,title,message,link_url)
           SELECT id,%s,%s,%s FROM users
           WHERE role='customer' AND verification_status='Approved'""",
        (
            "New voucher available!",
            f"{name}: {discount_label} with code {code}. Claim it now.{expiration}",
            link_url,
        ),
    )


def load_customer_cart(user_id):
    ensure_customer_cart_schema()
    rows = query_all(
        "SELECT product_id,quantity FROM customer_cart_items WHERE user_id=%s",
        (user_id,),
    )
    cart = {}
    for row in rows:
        product_id, quantity = row["product_id"], row["quantity"]
        if int(product_id) > 0 and int(quantity) > 0:
            cart[str(product_id)] = int(quantity)
    return cart


def save_customer_cart(user_id, cart):
    ensure_customer_cart_schema()
    items = []
    for product_id, quantity in cart.items():
        try:
            product_id, quantity = int(product_id), int(quantity)
        except (TypeError, ValueError):
            current_app.logger.warning("Ignoring invalid saved cart item for user_id=%s", user_id)
            continue
        if product_id > 0 and quantity > 0:
            items.append((user_id, product_id, quantity))

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM customer_cart_items WHERE user_id=%s", (user_id,))
        if items:
            cur.executemany(
                """INSERT INTO customer_cart_items (user_id,product_id,quantity)
                   VALUES (%s,%s,%s)""",
                items,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def merge_customer_carts(saved_cart, guest_cart):
    merged = dict(saved_cart)
    if isinstance(guest_cart, dict):
        for product_id, quantity in guest_cart.items():
            try:
                product_id, quantity = str(int(product_id)), int(quantity)
            except (TypeError, ValueError):
                current_app.logger.warning("Ignoring invalid guest cart item during sign-in")
                continue
            if int(product_id) > 0 and quantity > 0:
                merged[product_id] = merged.get(product_id, 0) + quantity
    return merged


def password_reset_email_settings():
    return smtp_email_settings()


def smtp_email_settings():
    file_settings = dotenv_values(BASE_DIR / ".env")

    def setting(name):
        return os.getenv(name, "").strip() or str(file_settings.get(name) or "").strip()

    host = setting("SMTP_HOST")
    sender = setting("SMTP_FROM")
    username = setting("SMTP_USERNAME")
    password = setting("SMTP_PASSWORD")
    if not host or not sender:
        raise ValueError("Email delivery is not configured. Contact the site administrator.")
    if bool(username) != bool(password):
        raise ValueError("Both SMTP_USERNAME and SMTP_PASSWORD must be configured.")
    if host.lower() == "smtp.gmail.com" and not username:
        raise ValueError("Gmail SMTP requires SMTP_USERNAME and a Gmail App Password.")
    if host.lower() == "smtp.gmail.com":
        password = password.replace(" ", "")
    try:
        port = int(setting("SMTP_PORT") or "587")
    except ValueError as exc:
        raise ValueError("SMTP_PORT must be a valid port number.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65,535.")
    return host, port, sender, username, password


def _send_branded_html_email(recipient, subject, template_name, context, settings):
    host, port, sender, username, password = settings
    html_content = current_app.jinja_env.get_template(template_name).render(
        **context
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(html_content, subtype="html", charset="utf-8")

    logo_path = BASE_DIR / "static" / "facebook-page.jpg"
    if logo_path.is_file():
        message.add_related(
            logo_path.read_bytes(),
            maintype="image",
            subtype="jpeg",
            cid="<fuzzywire-logo>",
            filename="fuzzywire-logo.jpg",
        )

    if port == 465:
        with smtplib.SMTP_SSL(
            host, port, timeout=15, context=ssl.create_default_context()
        ) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def create_customer_email_verification_token(user_id):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE customer_email_verification_tokens
               SET used_at=UTC_TIMESTAMP()
               WHERE user_id=%s AND used_at IS NULL""",
            (user_id,),
        )
        cur.execute(
            """INSERT INTO customer_email_verification_tokens
               (user_id,token_hash,expires_at)
               VALUES (%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR))""",
            (user_id, token_hash),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return raw_token


def customer_email_verification_url(token):
    base_url = current_app.config.get("BASE_URL", "").strip().rstrip("/")
    parsed_url = urlsplit(base_url)
    try:
        port = parsed_url.port
    except ValueError as exc:
        raise ValueError("BASE_URL must use a valid network port.") from exc
    if (
        parsed_url.scheme not in {"https", "http"}
        or not parsed_url.hostname
        or (port is not None and not 1 <= port <= 65535)
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("BASE_URL must be a valid public HTTP or HTTPS site URL.")
    if parsed_url.scheme != "https" and parsed_url.hostname not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("Set BASE_URL to the HTTPS address of the live website.")
    return f"{base_url}{url_for('verify_email', token=token)}"


def verify_customer_email_token(token_hash):
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id,user_id FROM customer_email_verification_tokens
               WHERE token_hash=%s AND used_at IS NULL
                 AND expires_at>UTC_TIMESTAMP()
               FOR UPDATE""",
            (token_hash,),
        )
        token = cur.fetchone()
        if not token:
            conn.rollback()
            return False
        cur.execute(
            """UPDATE customer_email_verification_tokens
               SET used_at=UTC_TIMESTAMP()
               WHERE id=%s AND used_at IS NULL""",
            (token["id"],),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        cur.execute(
            """UPDATE users SET email_verified=1,
               account_status=IF(account_status='Deactivated','Deactivated',
                 IF(verification_status='Rejected','Rejected',
                   IF(verification_status='Approved','Active','Pending')))
               WHERE id=%s AND role='customer'""",
            (token["user_id"],),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def send_customer_verification_email(recipient, customer_name, verification_url):
    _send_branded_html_email(
        recipient,
        "Verify your FuzzyWire Bouquet account",
        "emails/customer_verification.html",
        {
            "customer_name": customer_name,
            "verification_url": verification_url,
            "has_logo": (BASE_DIR / "static" / "facebook-page.jpg").is_file(),
        },
        smtp_email_settings(),
    )


def _email_otp_digest(user_id, purpose, code):
    secret_key = current_app.config["SECRET_KEY"]
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    payload = f"{user_id}:{purpose}:{code}".encode("utf-8")
    return hmac.new(secret_key, payload, hashlib.sha256).hexdigest()


def create_email_otp_challenge(user_id, purpose):
    if purpose not in {"registration", "password_reset", "new_device"}:
        raise ValueError("Unsupported email verification purpose.")
    recent = query_one(
        """SELECT id FROM email_otp_challenges
           WHERE user_id=%s AND purpose=%s
             AND used_at IS NULL
             AND created_at>DATE_SUB(UTC_TIMESTAMP(), INTERVAL 60 SECOND)
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, purpose),
    )
    if recent:
        raise ValueError("Please wait one minute before requesting another code.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _email_otp_digest(user_id, purpose, code)
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE email_otp_challenges SET used_at=UTC_TIMESTAMP()
               WHERE user_id=%s AND purpose=%s AND used_at IS NULL""",
            (user_id, purpose),
        )
        cur.execute(
            """INSERT INTO email_otp_challenges
               (user_id,purpose,code_hash,expires_at)
               VALUES (%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL 10 MINUTE))""",
            (user_id, purpose, code_hash),
        )
        challenge_id = cur.lastrowid
        conn.commit()
        return challenge_id, code
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_email_otp_challenge(challenge_id, user_id, purpose, code):
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id,code_hash,attempts,expires_at,verified_at
               FROM email_otp_challenges
               WHERE id=%s AND user_id=%s AND purpose=%s AND used_at IS NULL
               FOR UPDATE""",
            (challenge_id, user_id, purpose),
        )
        challenge = cur.fetchone()
        if (
            not challenge or challenge["attempts"] >= 5
            or challenge["verified_at"] is not None
            or challenge["expires_at"] <= datetime.now(timezone.utc).replace(tzinfo=None)
        ):
            conn.rollback()
            return False
        if not re.fullmatch(r"\d{6}", code or "") or not hmac.compare_digest(
            challenge["code_hash"], _email_otp_digest(user_id, purpose, code)
        ):
            cur.execute(
                "UPDATE email_otp_challenges SET attempts=attempts+1 WHERE id=%s",
                (challenge_id,),
            )
            conn.commit()
            return False
        if purpose == "password_reset":
            cur.execute(
                """UPDATE email_otp_challenges SET verified_at=UTC_TIMESTAMP()
                   WHERE id=%s AND verified_at IS NULL""",
                (challenge_id,),
            )
        else:
            cur.execute(
                """UPDATE email_otp_challenges SET verified_at=UTC_TIMESTAMP(),
                   used_at=UTC_TIMESTAMP() WHERE id=%s AND used_at IS NULL""",
                (challenge_id,),
            )
        verified = cur.rowcount == 1
        conn.commit()
        return verified
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def send_customer_email_otp(recipient, customer_name, code, purpose):
    descriptions = {
        "registration": "finish verifying your FuzzyWire Bouquet account",
        "password_reset": "verify your password reset request",
        "new_device": "verify this new sign-in device",
    }
    if purpose not in descriptions:
        raise ValueError("Unsupported email verification purpose.")
    _send_branded_html_email(
        recipient,
        "Your FuzzyWire Bouquet verification code",
        "emails/verification_code.html",
        {
            "customer_name": customer_name,
            "code": code,
            "purpose_description": descriptions[purpose],
            "has_logo": (BASE_DIR / "static" / "facebook-page.jpg").is_file(),
        },
        smtp_email_settings(),
    )


def queue_email_otp(user, purpose):
    challenge_id, code = create_email_otp_challenge(user["id"], purpose)
    try:
        send_customer_email_otp(
            user["email"], user.get("full_name", "there"), code, purpose
        )
    except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
        execute(
            """UPDATE email_otp_challenges SET used_at=UTC_TIMESTAMP()
               WHERE id=%s AND used_at IS NULL""",
            (challenge_id,),
        )
        raise
    session["pending_otp"] = {
        "challenge_id": challenge_id,
        "user_id": user["id"],
        "purpose": purpose,
    }


def trusted_device_exists(user_id, device_token):
    if not device_token or len(device_token) > 128:
        return False
    device_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
    device = query_one(
        """SELECT id FROM trusted_login_devices
           WHERE user_id=%s AND device_hash=%s""",
        (user_id, device_hash),
    )
    if device:
        execute(
            "UPDATE trusted_login_devices SET last_seen_at=UTC_TIMESTAMP() WHERE id=%s",
            (device["id"],),
        )
    return device is not None


def trust_device(user_id, device_token):
    device_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
    execute(
        """INSERT INTO trusted_login_devices (user_id,device_hash)
           VALUES (%s,%s) ON DUPLICATE KEY UPDATE
           user_id=VALUES(user_id),last_seen_at=UTC_TIMESTAMP()""",
        (user_id, device_hash),
    )


def establish_authenticated_session(user, guest_cart, device_token=None):
    session.clear()
    session["user"] = user
    session["auth_user_id"] = user["id"]
    session.permanent = True
    csrf_token()
    if user.get("role") == "customer" and user.get("account_status", "Active") == "Active":
        saved_cart = load_customer_cart(user["id"])
        cart = merge_customer_carts(saved_cart, guest_cart)
        save_customer_cart(user["id"], cart)
        session["cart"] = cart
    if user.get("role") == "admin":
        target = "admin_dashboard"
    elif (
        user.get("account_status", "Active") != "Active"
        or user.get("verification_status", "Approved") != "Approved"
    ):
        target = "customer_account_status"
    else:
        target = "home"
    response = redirect(url_for(target))
    if device_token:
        response.set_cookie(
            "fuzzywire_device",
            device_token,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
            path="/",
        )
    current_app.logger.info(
        "Sign-in successful for user_id=%s, role=%s", user["id"], user["role"]
    )
    return response


def send_password_reset_email(recipient, reset_url):
    _send_branded_html_email(
        recipient,
        "Reset your FuzzyWire Bouquet password",
        "emails/password_reset.html",
        {
            "reset_url": reset_url,
            "has_logo": (BASE_DIR / "static" / "facebook-page.jpg").is_file(),
        },
        password_reset_email_settings(),
    )


def send_order_email(recipient, subject, body):
    if not recipient:
        return False
    try:
        host, port, sender, username, password = password_reset_email_settings()
    except ValueError as exc:
        current_app.logger.warning("Order email not sent because SMTP is not configured: %s", exc)
        return False
    try:
        _send_branded_html_email(
            recipient,
            subject,
            "emails/order_update.html",
            {
                "subject": subject,
                "body_lines": body.splitlines(),
                "has_logo": (BASE_DIR / "static" / "facebook-page.jpg").is_file(),
            },
            (host, port, sender, username, password),
        )
        return True
    except (OSError, smtplib.SMTPException):
        current_app.logger.exception("Could not send order email to %s", recipient)
        return False


def gcash_payment_details():
    ensure_gcash_settings_schema()
    setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_number'"
    )
    if not setting:
        raise RuntimeError("The configured GCash number is missing from app settings.")
    qr_setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_qr_filename'"
    )
    name_setting = None
    try:
        name_setting = query_one(
            "SELECT setting_value FROM app_settings WHERE setting_key='gcash_name'"
        )
    except (StopIteration, Exception):
        name_setting = None

    name = (
        name_setting["setting_value"].strip()
        if name_setting and name_setting.get("setting_value") and name_setting["setting_value"].strip()
        else os.getenv("GCASH_ACCOUNT_NAME", "FuzzyWire Bouquet").strip()
    )
    return {
        "name": name,
        "number": setting["setting_value"],
        "qr_filename": qr_setting["setting_value"] if qr_setting else None,
    }


def update_gcash_name(name, admin_id):
    ensure_gcash_settings_schema()
    cleaned = (name or "").strip()
    if not cleaned:
        cleaned = "FuzzyWire Bouquet"
    if len(cleaned) > 120:
        raise ValueError("GCash account name must be 120 characters or fewer.")
    execute(
        """INSERT INTO app_settings (setting_key,setting_value,updated_by)
           VALUES ('gcash_name',%s,%s)
           ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),
                                   updated_by=VALUES(updated_by)""",
        (cleaned, admin_id),
    )
    return cleaned


def normalize_gcash_number(value):
    number = re.sub(r"[\s()-]", "", (value or "").strip())
    if number.startswith("+63"):
        number = "0" + number[3:]
    elif number.startswith("63"):
        number = "0" + number[2:]
    if not re.fullmatch(r"09\d{9}", number):
        raise ValueError("Enter a valid Philippine GCash mobile number, such as 09XXXXXXXXX.")
    return number


def update_gcash_number(value, admin_id):
    number = normalize_gcash_number(value)
    ensure_gcash_settings_schema()
    execute(
        """INSERT INTO app_settings (setting_key,setting_value,updated_by)
           VALUES ('gcash_number',%s,%s)
           ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),
                                   updated_by=VALUES(updated_by)""",
        (number, admin_id),
    )
    return number


def update_gcash_qr(upload, admin_id):
    if not upload or not upload.filename:
        return None
    extension = Path(upload.filename).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload a PNG, JPG, JPEG, or WEBP GCash QR image.")
    content = upload.stream.read(PAYMENT_PROOF_MAX_BYTES + 1)
    if len(content) > PAYMENT_PROOF_MAX_BYTES:
        raise ValueError("The GCash QR image must be 5 MB or smaller.")
    expected_formats = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_formats[extension]:
                raise ValueError("The uploaded file does not match its image format.")
            if image.width * image.height > 25_000_000:
                raise ValueError("The GCash QR image must be 25 megapixels or smaller.")
            image.verify()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The uploaded GCash QR is not a valid image.") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError("The GCash QR image dimensions are too large.") from exc

    ensure_gcash_settings_schema()
    previous_setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_qr_filename'"
    )
    filename = f"gcash-qr-{uuid.uuid4().hex}.{extension}"
    target = UPLOAD_DIR / filename
    target.write_bytes(content)
    try:
        execute(
            """INSERT INTO app_settings (setting_key,setting_value,updated_by)
               VALUES ('gcash_qr_filename',%s,%s)
               ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),
                                       updated_by=VALUES(updated_by)""",
            (filename, admin_id),
        )
    except mysql.connector.Error:
        target.unlink(missing_ok=True)
        raise

    old_filename = Path(previous_setting["setting_value"]).name if previous_setting else ""
    if old_filename.startswith("gcash-qr-") and old_filename != filename:
        old_path = (UPLOAD_DIR / old_filename).resolve()
        if old_path.parent == UPLOAD_DIR.resolve() and old_path.is_file():
            old_path.unlink()
    return filename


def remove_gcash_qr(filename):
    if not filename:
        return
    safe_filename = Path(filename).name
    if not safe_filename.startswith("gcash-qr-"):
        return
    path = (UPLOAD_DIR / safe_filename).resolve()
    if path.parent == UPLOAD_DIR.resolve() and path.is_file():
        path.unlink()


def save_payment_proof(upload):
    if not upload or not upload.filename:
        raise ValueError("Choose a payment screenshot or photo to upload.")
    extension = Path(upload.filename).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload a PNG, JPG, JPEG, or WEBP image.")
    content = upload.stream.read(PAYMENT_PROOF_MAX_BYTES + 1)
    if len(content) > PAYMENT_PROOF_MAX_BYTES:
        raise ValueError("Payment proof images must be 5 MB or smaller.")
    expected_formats = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_formats[extension]:
                raise ValueError("The uploaded file does not match its image format.")
            if image.width * image.height > 25_000_000:
                raise ValueError("Payment proof images must be 25 megapixels or smaller.")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The uploaded file is not a valid image.") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError("Payment proof image dimensions are too large.") from exc
    filename = f"payment-proof-{uuid.uuid4().hex}.{extension}"
    target = PAYMENT_PROOF_DIR / filename
    target.write_bytes(content)
    return filename


def payment_proof_path(order_type, order_id):
    table = {"Order": "orders", "Custom": "custom_orders"}.get(order_type)
    if not table:
        abort(404)
    ensure_gcash_schema()
    order = query_one(
        f"""SELECT user_id,payment_proof_filename FROM `{table}` WHERE id=%s""",
        (order_id,),
    )
    if not order or not order["payment_proof_filename"]:
        abort(404)
    if current_user().get("role") != "admin" and order["user_id"] != current_user()["id"]:
        abort(404)
    filename = Path(order["payment_proof_filename"]).name
    path = (PAYMENT_PROOF_DIR / filename).resolve()
    if path.parent != PAYMENT_PROOF_DIR.resolve() or not path.is_file():
        abort(404)
    return path


def upload_order_payment_proof(order_type, order_id, user_id, upload):
    table = {"Order": "orders", "Custom": "custom_orders"}.get(order_type)
    if not table:
        abort(404)
    ensure_gcash_schema()
    filename = save_payment_proof(upload)
    conn = db()
    old_filename = None
    try:
        cur = conn.cursor(dictionary=True)
        status_column = "order_status" if table == "orders" else "status"
        cur.execute(
            f"""SELECT payment_method,payment_status,payment_proof_filename,`{status_column}` AS order_status
                FROM `{table}` WHERE id=%s AND user_id=%s FOR UPDATE""",
            (order_id, user_id),
        )
        order = cur.fetchone()
        if not order:
            conn.rollback()
            (PAYMENT_PROOF_DIR / filename).unlink(missing_ok=True)
            abort(404)
        if (order["payment_method"] != "GCash"
                or order["payment_status"] in {"Paid", "Refunded"}
                or order["order_status"] != "Pending"):
            conn.rollback()
            (PAYMENT_PROOF_DIR / filename).unlink(missing_ok=True)
            raise ValueError("Payment proof can only be uploaded for a Pending, unpaid GCash order.")
        old_filename = order["payment_proof_filename"]
        cur.execute(
            f"""UPDATE `{table}` SET payment_proof_filename=%s,
                       payment_proof_uploaded_at=CURRENT_TIMESTAMP,
                       payment_status='Pending',payment_reviewed_at=NULL,
                       payment_reviewed_by=NULL,payment_review_note=NULL
                WHERE id=%s AND user_id=%s""",
            (filename, order_id, user_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        (PAYMENT_PROOF_DIR / filename).unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    if old_filename and old_filename != filename:
        old_path = (PAYMENT_PROOF_DIR / Path(old_filename).name).resolve()
        if old_path.parent == PAYMENT_PROOF_DIR.resolve():
            old_path.unlink(missing_ok=True)
    if order_type == "Order":
        link = url_for("order_detail", order_id=order_id)
        label = f"order #{order_id}"
    else:
        link = url_for("customer_custom_detail", custom_id=order_id)
        label = f"custom bouquet request #C{order_id}"
    notify_admins(
        "GCash proof submitted",
        f"Payment proof was uploaded for {label}. Review it before confirming the order.",
        link,
    )


def review_order_payment(order_type, order_id, decision, admin_id, note):
    table = {"Order": "orders", "Custom": "custom_orders"}.get(order_type)
    if not table or decision not in {"approve", "reject"}:
        abort(404)
    ensure_gcash_schema()
    note = (note or "").strip()
    if decision == "reject" and not note:
        raise ValueError("Provide a reason when rejecting a GCash payment.")
    if len(note) > 1000:
        raise ValueError("The payment review note must be 1,000 characters or fewer.")
    status_column = "order_status" if table == "orders" else "status"
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT user_id,payment_method,payment_status,payment_proof_filename,
                       `{status_column}` AS order_status
                FROM `{table}` WHERE id=%s FOR UPDATE""",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            conn.rollback()
            return None
        if order["payment_method"] != "GCash":
            conn.rollback()
            raise ValueError("Only GCash orders can be reviewed here.")
        if order["order_status"] != "Pending":
            conn.rollback()
            raise ValueError("Only Pending orders can have a GCash payment reviewed.")
        if order["payment_status"] != "Pending" or not order["payment_proof_filename"]:
            conn.rollback()
            raise ValueError("A newly uploaded GCash proof is required before review.")
        new_status = "Paid" if decision == "approve" else "Failed"
        cur.execute(
            f"""UPDATE `{table}` SET payment_status=%s,
                       payment_reviewed_at=CURRENT_TIMESTAMP,payment_reviewed_by=%s,
                       payment_review_note=%s
                WHERE id=%s AND payment_status='Pending'
                  AND payment_proof_filename IS NOT NULL""",
            (new_status, admin_id, note or None, order_id),
        )
        conn.commit()
        return order["user_id"], new_status
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_password_reset_token(user_id):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE password_reset_tokens SET used_at=UTC_TIMESTAMP()
               WHERE user_id=%s AND used_at IS NULL""",
            (user_id,),
        )
        cur.execute(
            """INSERT INTO password_reset_tokens (user_id,token_hash,expires_at)
               VALUES (%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL 30 MINUTE))""",
            (user_id, token_hash),
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        conn.close()
    return raw_token, token_hash


def reset_token_is_valid(token_hash):
    return query_one(
        """SELECT id FROM password_reset_tokens
           WHERE token_hash=%s AND used_at IS NULL
             AND expires_at > UTC_TIMESTAMP()""",
        (token_hash,),
    ) is not None


def save_password_reset(token_hash, new_password_hash):
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id,user_id FROM password_reset_tokens
               WHERE token_hash=%s AND used_at IS NULL
                 AND expires_at > UTC_TIMESTAMP()
               FOR UPDATE""",
            (token_hash,),
        )
        token = cur.fetchone()
        if not token:
            conn.rollback()
            return False
        cur.execute(
            "UPDATE users SET password_hash=%s WHERE id=%s",
            (new_password_hash, token["user_id"]),
        )
        cur.execute(
            """UPDATE password_reset_tokens SET used_at=UTC_TIMESTAMP()
               WHERE user_id=%s AND used_at IS NULL""",
            (token["user_id"],),
        )
        conn.commit()
        return True
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_admin_order_status(
    table, status_column, order_id, user_id, status, cancellation_reason, confirmation_message
):
    if (table, status_column) not in {("orders", "order_status"), ("custom_orders", "status")}:
        raise ValueError("Unsupported order status table.")
    ensure_gcash_schema()
    ensure_order_messaging_schema()
    ensure_order_queue_schema()
    if table == "custom_orders":
        ensure_custom_materials_schema()
    if status == "Confirmed":
        ensure_chat_schema()

    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT q.queue_id,q.order_type,q.order_id
               FROM order_queue q
               WHERE (q.order_type='Order' AND EXISTS (
                        SELECT 1 FROM orders o WHERE o.id=q.order_id
                          AND o.order_status NOT IN ('Delivered','Picked Up','Cancelled')
                     ))
                  OR (q.order_type='Custom' AND EXISTS (
                        SELECT 1 FROM custom_orders c WHERE c.id=q.order_id
                          AND c.status NOT IN ('Delivered','Picked Up','Cancelled')
                     ))
               ORDER BY q.queue_id ASC LIMIT 1 FOR UPDATE"""
        )
        queue_head = cur.fetchone()
        cur.execute(
            f"""SELECT id,`{status_column}` AS status,cancellation_reason,
                       payment_method,payment_status,payment_proof_filename,
                       payment_reviewed_by,confirmation_chat_sent
                       {',fulfillment_method' if table == 'orders' else ',fulfillment_method,materials_reserved'}
                FROM `{table}` WHERE id=%s FOR UPDATE""",
            (order_id,),
        )
        current = cur.fetchone()
        if not current:
            raise RuntimeError("The order could not be found while updating its status.")
        cur.execute(
            """SELECT queue_id FROM order_queue
               WHERE order_type=%s AND order_id=%s FOR UPDATE""",
            ("Order" if table == "orders" else "Custom", order_id),
        )
        current_queue = cur.fetchone()
        if not current_queue:
            raise RuntimeError("The order is missing from the processing list.")

        if current["status"] in {"Delivered", "Picked Up", "Cancelled"} and status != current["status"]:
            conn.rollback()
            return (
                current["status"],
                False,
                False,
                "Delivered, picked-up, or cancelled orders are final and cannot be reopened.",
            )

        if status != current["status"] and status != "Cancelled":
            if status == "Confirmed" and current["payment_method"] == "GCash" \
                    and (
                        current["payment_status"] != "Paid"
                        or not current["payment_proof_filename"]
                        or not current["payment_reviewed_by"]
                    ):
                conn.rollback()
                return (
                    current["status"],
                    False,
                    False,
                    "Verify the customer's GCash proof as Paid before confirming this order.",
                )
            if queue_head and queue_head["queue_id"] != current_queue["queue_id"]:
                return_message = "This order cannot be advanced yet."
                conn.rollback()
                return current["status"], False, False, return_message

            next_status = {
                "Pending": "Confirmed",
                "Confirmed": "Preparing",
                "Preparing": "Ready for Delivery",
                "Out for Delivery": "Delivered",
            }.get(current["status"])
            fulfillment_method = current["fulfillment_method"]
            if current["status"] == "Preparing":
                next_status = "Ready for Pickup" if fulfillment_method == "Pickup" else "Ready for Delivery"
            elif current["status"] == "Ready for Pickup":
                next_status = "Picked Up"
            elif current["status"] == "Ready for Delivery":
                next_status = "Out for Delivery"
            if status != next_status:
                conn.rollback()
                return (
                    current["status"],
                    False,
                    False,
                    "Order statuses must advance one step at a time in the fulfillment sequence.",
                )

        reserved_update = None
        if table == "custom_orders" and status == "Confirmed" and not current["materials_reserved"]:
            cur.execute(
                """SELECT material_id,quantity FROM custom_order_materials
                   WHERE custom_order_id=%s ORDER BY material_id,id FOR UPDATE""",
                (order_id,),
            )
            required_stock = {}
            for material in cur.fetchall():
                if material["material_id"] is None:
                    conn.rollback()
                    return (
                        current["status"], False, False,
                        "A requested material is no longer available in inventory; revise the design before confirming.",
                    )
                material_id = material["material_id"]
                required_stock[material_id] = (
                    required_stock.get(material_id, Decimal("0"))
                    + Decimal(str(material["quantity"]))
                )
            for material_id, quantity in sorted(required_stock.items()):
                cur.execute(
                    "SELECT name,stock,is_active FROM custom_materials WHERE id=%s FOR UPDATE",
                    (material_id,),
                )
                material = cur.fetchone()
                if (not material or not material["is_active"]
                        or Decimal(str(material["stock"])) < quantity):
                    conn.rollback()
                    name = material["name"] if material else f"#{material_id}"
                    return (
                        current["status"], False, False,
                        f"Material {name} is inactive or has insufficient stock; update the design or inventory before confirming.",
                    )
            for material_id, quantity in sorted(required_stock.items()):
                cur.execute(
                    """UPDATE custom_materials SET stock=stock-%s
                       WHERE id=%s AND stock >= %s""",
                    (quantity, material_id, quantity),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return (
                        current["status"], False, False,
                        "Material stock changed during confirmation. Refresh and try again.",
                    )
            reserved_update = 1
        elif table == "custom_orders" and status == "Cancelled" and current["materials_reserved"]:
            cur.execute(
                """SELECT material_id,quantity FROM custom_order_materials
                   WHERE custom_order_id=%s AND material_id IS NOT NULL
                   ORDER BY material_id,id FOR UPDATE""",
                (order_id,),
            )
            for material in cur.fetchall():
                cur.execute(
                    "UPDATE custom_materials SET stock=stock+%s WHERE id=%s",
                    (material["quantity"], material["material_id"]),
                )
            reserved_update = 0

        saved_reason = (
            cancellation_reason if status == "Cancelled"
            else current["cancellation_reason"]
        )
        send_confirmation = (
            status == "Confirmed" and bool(user_id)
            and not current["confirmation_chat_sent"]
        )
        changed = (
            current["status"] != status
            or current["cancellation_reason"] != saved_reason
            or send_confirmation
            or reserved_update is not None
        )
        if not changed:
            conn.rollback()
            return current["status"], False, False, None

        if send_confirmation:
            cur.execute(
                """INSERT INTO chat_conversations (user_id) VALUES (%s)
                   ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)""",
                (user_id,),
            )
            cur.execute(
                "SELECT id FROM chat_conversations WHERE user_id=%s",
                (user_id,),
            )
            conversation = cur.fetchone()
            if not conversation:
                raise RuntimeError("Could not create or load the customer chat conversation.")
            reserve_sql = ",materials_reserved=%s" if reserved_update is not None else ""
            reserve_values = (reserved_update,) if reserved_update is not None else ()
            cur.execute(
                f"""UPDATE `{table}` SET `{status_column}`=%s,
                           cancellation_reason=%s,confirmation_chat_sent=1{reserve_sql}
                    WHERE id=%s""",
                (status, saved_reason) + reserve_values + (order_id,),
            )
            cur.execute(
                """INSERT INTO chat_messages (conversation_id,sender_role,body)
                   VALUES (%s,'admin',%s)""",
                (conversation["id"], confirmation_message),
            )
            cur.execute(
                "UPDATE chat_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (conversation["id"],),
            )
        else:
            reserve_sql = ",materials_reserved=%s" if reserved_update is not None else ""
            reserve_values = (reserved_update,) if reserved_update is not None else ()
            cur.execute(
                f"""UPDATE `{table}` SET `{status_column}`=%s,cancellation_reason=%s{reserve_sql}
                    WHERE id=%s""",
                (status, saved_reason) + reserve_values + (order_id,),
            )
        conn.commit()
        return current["status"], True, send_confirmation, None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_order_confirmation_message(
    order_id, items, total, payment_description, fulfillment_method, delivery_address
):
    if payment_description == "GCash":
        payment_description = "GCash (payment proof verified by the shop)"
    lines = [f"Order #{order_id} is confirmed!", "", "Order items:"]
    for item in items:
        line = f"- {item['item_name']} x {item['quantity']}"
        if len("\n".join(lines + [line, "", f"Total: ₱{total:.2f}"])) > 1800:
            lines.append("- Additional items are listed in My Orders.")
            break
        lines.append(line)
    lines.extend([
        "",
        f"Total: ₱{total:.2f}",
        (
            f"Pickup location: {delivery_address}"
            if fulfillment_method == "Pickup"
            else "Fulfillment: Delivery"
        ),
        f"Payment: {payment_description}",
        "We’ll send order updates here. Thank you for shopping with FuzzyWire!",
    ])
    return "\n".join(lines)


def build_custom_confirmation_message(order):
    payment_description = (
        "GCash (payment proof verified by the shop)"
        if order["payment_method"] == "GCash"
        else order["payment_method"]
    )
    return "\n".join([
        f"Custom bouquet request #C{order['id']} is confirmed!",
        "",
        f"Design: {order['flower_type']}, {order['flower_color']}, "
        f"{order['bouquet_size']}, {order['wrapping_style']}",
        f"Quantity: {order['quantity']}",
        f"Total: ₱{order['total_price']:.2f}",
        f"Fulfillment: {order['fulfillment_method']} — {order.get('delivery_address') or PICKUP_ADDRESS}",
        f"Payment: {payment_description}",
        "We’ll send order updates here. Thank you for shopping with FuzzyWire!",
    ])


def philippine_location_data():
    global _ph_location_data
    if _ph_location_data is None:
        data_dir = BASE_DIR / "static" / "data"
        with (data_dir / "regions.json").open(encoding="utf-8") as source:
            regions = json.load(source)
        with (data_dir / "provinces.json").open(encoding="utf-8") as source:
            provinces = json.load(source)
        with (data_dir / "cities.json").open(encoding="utf-8") as source:
            cities = json.load(source)
        _ph_location_data = {
            "regions": {item["code"]: item for item in regions},
            "provinces": {item["code"]: item for item in provinces},
            "cities": {item["code"]: item for item in cities},
        }
    return _ph_location_data


def philippine_barangays_by_city():
    global _ph_barangays_by_city
    if _ph_barangays_by_city is None:
        data_dir = BASE_DIR / "static" / "data"
        with (data_dir / "barangays.json").open(encoding="utf-8") as source:
            barangays = json.load(source)
        grouped = {}
        for barangay in barangays:
            grouped.setdefault(barangay["cityCode"], []).append(barangay)
        _ph_barangays_by_city = grouped
    return _ph_barangays_by_city


def delivery_address_is_in_laguna(province_code, city_code):
    if str(province_code or "") != LAGUNA_PROVINCE_CODE:
        return False
    data = philippine_location_data()
    province = data["provinces"].get(LAGUNA_PROVINCE_CODE)
    city = data["cities"].get(str(city_code or ""))
    return bool(
        province
        and province["regionCode"] == LAGUNA_REGION_CODE
        and city
        and city.get("provinceCode") == LAGUNA_PROVINCE_CODE
    )


def delivery_address_is_in_service_area(province_code, city_code):
    if not delivery_address_is_in_laguna(province_code, city_code):
        return False
    return str(city_code or "").strip() in ALLOWED_DELIVERY_CITY_CODES


def is_within_laguna_map_bounds(latitude, longitude):
    return 13.85 <= latitude <= 14.65 and 120.75 <= longitude <= 121.85


def resolve_delivery_address(form, required=False, require_service_area=False):
    data = philippine_location_data()
    region_code = form.get("delivery_region_code", "").strip()
    province_code = form.get("delivery_province_code", "").strip()
    city_code = form.get("delivery_city_code", "").strip()
    barangay_code = form.get("delivery_barangay_code", "").strip()
    barangay_name = form.get("delivery_barangay_name", "").strip()
    street_address = form.get("delivery_street_address", "").strip()
    postal_code = form.get("delivery_postal_code", "").strip()
    postal_code_other = form.get("delivery_postal_code_other", "").strip()

    values = (region_code, province_code, city_code, barangay_code, street_address,
              barangay_name, postal_code, postal_code_other)
    if not any(values):
        if required:
            raise ValueError("Please choose your region, city/municipality, barangay, and enter your house/street address.")
        return "", {
            "delivery_region_code": None,
            "delivery_province_code": None,
            "delivery_city_code": None,
            "delivery_barangay_code": None,
            "delivery_barangay": None,
            "delivery_postal_code": None,
            "delivery_street_address": None,
        }
    if not all((region_code, city_code, street_address)):
        raise ValueError("Choose a region, city/municipality, barangay, and enter your house/street address.")
    if len(street_address) > 500:
        raise ValueError("House/street address must be at most 500 characters.")

    region = data["regions"].get(region_code)
    province = data["provinces"].get(province_code) if province_code else None
    city = data["cities"].get(city_code)
    if not region or not city:
        raise ValueError("Choose a valid Philippine region and city/municipality.")
    if province_code:
        if not province or province["regionCode"] != region_code or city.get("provinceCode") != province_code:
            raise ValueError("The selected province does not match the city/municipality.")
        if province_code != LAGUNA_PROVINCE_CODE:
            raise ValueError("Delivery and saved addresses are currently limited to Laguna.")
        province_name = province["name"].strip()
    else:
        if city.get("provinceCode"):
            raise ValueError("Choose a province for the selected city/municipality.")
        if city.get("regionCode") != region_code:
            raise ValueError("The selected city/municipality does not match the region.")
        province_name = ""

    if require_service_area and str(city_code or "").strip() not in ALLOWED_DELIVERY_CITY_CODES:
        raise ValueError("Delivery is currently available only in Cavinti, Lumban, Kalayaan, Pagsanjan, Santa Cruz, and Pila, Laguna.")

    city_barangays = philippine_barangays_by_city().get(city_code, [])
    barangay = next(
        (item for item in city_barangays
         if item["code"] == barangay_code),
        None,
    )
    if city_barangays and not barangay:
        raise ValueError("Choose a barangay within the selected city/municipality.")
    if not city_barangays and (not barangay_name or len(barangay_name) > 120):
        raise ValueError("Enter a valid barangay name for this city/municipality.")
    if not city_barangays and barangay_code:
        raise ValueError("This city/municipality needs a barangay name entered manually.")
    if barangay and barangay_name:
        raise ValueError("Choose a barangay from the list or enter its name, not both.")
    resolved_barangay_name = barangay["name"] if barangay else barangay_name

    if postal_code == "other":
        postal_code = postal_code_other
    elif postal_code_other:
        raise ValueError("Select “Other” before entering a different postal code.")
    if postal_code and not re.fullmatch(r"\d{4}", postal_code):
        raise ValueError("Postal code must contain exactly 4 digits.")
    known_postal_code = (city.get("zipCode") or "").strip()
    if postal_code and known_postal_code and postal_code != known_postal_code and not postal_code_other:
        raise ValueError("Choose the postal code listed for this city/municipality or select Other.")

    pieces = [street_address, resolved_barangay_name.strip(), city["name"].strip()]
    if province_name:
        pieces.append(province_name)
    pieces.append(region["name"].strip())
    if postal_code:
        pieces.append(postal_code)
    address = ", ".join(pieces)
    if len(address) > 2000:
        raise ValueError("The complete delivery address is too long.")
    return address, {
        "delivery_region_code": region_code,
        "delivery_province_code": province_code or None,
        "delivery_city_code": city_code,
        "delivery_barangay_code": barangay["code"] if barangay else None,
        "delivery_barangay": resolved_barangay_name,
        "delivery_postal_code": postal_code or None,
        "delivery_street_address": street_address,
    }


def geocode_address(address):
    normalized = " ".join((address or "").split())
    if not normalized or len(normalized) > 2000:
        raise ValueError("A valid delivery address is required to show the map.")
    if "philippines" not in normalized.casefold():
        normalized = f"{normalized}, Philippines"
    if len(normalized) > 2000:
        raise ValueError("The complete delivery address is too long to locate.")
    ensure_delivery_map_schema()
    address_hash = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()
    cached = query_one(
        """SELECT latitude,longitude FROM delivery_map_geocodes
           WHERE address_hash=%s""",
        (address_hash,),
    )
    if cached:
        coordinates = float(cached["latitude"]), float(cached["longitude"])
        if not is_within_laguna_map_bounds(*coordinates):
            raise ValueError("The address could not be located within the Laguna service area.")
        return coordinates

    global _last_geocoder_request
    with _geocoder_lock:
        cached = query_one(
            """SELECT latitude,longitude FROM delivery_map_geocodes
               WHERE address_hash=%s""",
            (address_hash,),
        )
        if cached:
            coordinates = float(cached["latitude"]), float(cached["longitude"])
            if not is_within_laguna_map_bounds(*coordinates):
                raise ValueError("The address could not be located within the Laguna service area.")
            return coordinates
        wait_seconds = 1.1 - (time.monotonic() - _last_geocoder_request)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        response = requests.get(
            GEOCODER_URL,
            params={
                "q": normalized,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "ph",
                "viewbox": "120.75,14.65,121.85,13.85",
                "bounded": 1,
            },
            headers={"User-Agent": os.getenv(
                "GEOCODER_USER_AGENT", "FuzzyWireBouquet/1.0"
            )},
            timeout=15,
        )
        _last_geocoder_request = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list) or not results:
            raise ValueError("The address could not be located on the map.")
        latitude = float(results[0]["lat"])
        longitude = float(results[0]["lon"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("The geocoder returned an invalid map location.")
        if not is_within_laguna_map_bounds(latitude, longitude):
            raise ValueError("The address could not be located within the Laguna service area.")
        try:
            execute(
                """INSERT INTO delivery_map_geocodes
                   (address_hash,address_text,latitude,longitude)
                   VALUES (%s,%s,%s,%s)""",
                (address_hash, normalized, latitude, longitude),
            )
        except mysql.connector.IntegrityError:
            cached = query_one(
                """SELECT latitude,longitude FROM delivery_map_geocodes
                   WHERE address_hash=%s""",
                (address_hash,),
            )
            if not cached:
                raise
            coordinates = float(cached["latitude"]), float(cached["longitude"])
            if not is_within_laguna_map_bounds(*coordinates):
                raise ValueError("The address could not be located within the Laguna service area.")
            return coordinates
        return latitude, longitude


LAGUNA_TOWN_COORDINATES = {
    "alaminos": (14.0631, 121.2464),
    "bay": (14.1818, 121.2842),
    "biñan": (14.3384, 121.0858),
    "binan": (14.3384, 121.0858),
    "cabuyao": (14.2786, 121.1245),
    "calamba": (14.2117, 121.1656),
    "calauan": (14.1481, 121.3164),
    "cavinti": (14.2464, 121.5074),
    "famy": (14.4372, 121.4503),
    "kalayaan": (14.3547, 121.5583),
    "liliw": (14.1328, 121.4339),
    "los baños": (14.1706, 121.2433),
    "los banos": (14.1706, 121.2433),
    "luisiana": (14.1897, 121.5167),
    "lumban": (14.2981, 121.4589),
    "mabitac": (14.4267, 121.4286),
    "magdalena": (14.1989, 121.4281),
    "majayjay": (14.1436, 121.5147),
    "nagcarlan": (14.1378, 121.4158),
    "paete": (14.3644, 121.5261),
    "pagsanjan": (14.2736, 121.4528),
    "pakil": (14.3817, 121.4792),
    "pangil": (14.4039, 121.4828),
    "pila": (14.2344, 121.3642),
    "rizal": (14.1114, 121.3961),
    "san pablo": (14.0683, 121.3256),
    "san pedro": (14.3592, 121.0506),
    "santa cruz": (14.2789, 121.4156),
    "sta. cruz": (14.2789, 121.4156),
    "sta cruz": (14.2789, 121.4156),
    "santa maria": (14.4711, 121.4253),
    "sta. maria": (14.4711, 121.4253),
    "sta maria": (14.4711, 121.4253),
    "santa rosa": (14.3122, 121.1114),
    "sta. rosa": (14.3122, 121.1114),
    "sta rosa": (14.3122, 121.1114),
    "siniloan": (14.4194, 121.4489),
    "victoria": (14.2292, 121.3278),
}


def geocode_delivery_map_address(address):
    normalized = (address or "").strip()
    if not normalized:
        return (14.2464, 121.5074), True
    try:
        return geocode_address(normalized), False
    except (ValueError, requests.RequestException):
        pass

    address_parts = {
        part.strip().casefold()
        for part in normalized.split(",")
        if part.strip()
    }
    addr_lower = normalized.casefold()
    laguna_cities = [
        city for city in philippine_location_data().get("cities", {}).values()
        if city.get("provinceCode") == LAGUNA_PROVINCE_CODE
    ]
    for city in laguna_cities:
        cname = city["name"].strip()
        if cname.casefold() in address_parts or cname.casefold() in addr_lower:
            try:
                coords = geocode_address(f"{cname}, Laguna, Philippines")
                return coords, True
            except (ValueError, requests.RequestException):
                if cname.casefold() in LAGUNA_TOWN_COORDINATES:
                    return LAGUNA_TOWN_COORDINATES[cname.casefold()], True

    for town, coords in sorted(LAGUNA_TOWN_COORDINATES.items(), key=lambda x: len(x[0]), reverse=True):
        if town in addr_lower:
            return coords, True

    return (14.2789, 121.4156), True


def configured_shop_coordinates():
    return configured_shop_location()["coordinates"]


def _location_text(value, label, maximum_length):
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise ValueError(f"Enter the shop {label.lower()}.")
    if len(cleaned) > maximum_length:
        raise ValueError(f"The shop {label.lower()} must be {maximum_length} characters or fewer.")
    return cleaned


def update_shop_location_settings(
    province_code, municipality_code, barangay, shop_name, complete_address, admin_id,
    map_latitude=None, map_longitude=None, map_approximate=False,
):
    province_code = (province_code or "").strip()
    municipality_code = (municipality_code or "").strip()
    if not delivery_address_is_in_laguna(province_code, municipality_code):
        raise ValueError("Choose a municipality in Laguna.")

    barangay = _location_text(barangay, "barangay", 120)
    shop_name = _location_text(shop_name, "name", 120)
    complete_address = _location_text(complete_address, "complete address", 255)
    if not map_latitude or not map_longitude:
        raise ValueError("Wait for the shop marker to load before saving.")
    try:
        map_latitude = float(map_latitude)
        map_longitude = float(map_longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("The shop map marker is invalid. Move it and try again.") from exc
    if (
        not math.isfinite(map_latitude)
        or not math.isfinite(map_longitude)
        or not is_within_laguna_map_bounds(map_latitude, map_longitude)
    ):
        raise ValueError("Move the shop marker to a location within Laguna.")
    settings = (
        ("shop_location_province_code", province_code),
        ("shop_location_municipality_code", municipality_code),
        ("shop_location_barangay", barangay),
        ("shop_location_name", shop_name),
        ("shop_location_address", complete_address),
    )
    if map_latitude and map_longitude:
        settings += (
            ("shop_location_map_pin", f"{map_latitude:.7f},{map_longitude:.7f}"),
            ("shop_location_map_pin_approximate", "1" if map_approximate else "0"),
        )
    ensure_gcash_settings_schema()
    values = []
    for key, value in settings:
        values.extend((key, value, admin_id))
    row_placeholders = ",".join(["(%s,%s,%s)"] * len(settings))
    execute(
        f"""INSERT INTO app_settings (setting_key,setting_value,updated_by)
           VALUES {row_placeholders}
           ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),
                                   updated_by=VALUES(updated_by)""",
        tuple(values),
    )
    return shop_location_details()


def shop_location_details():
    ensure_gcash_settings_schema()
    setting_keys = (
        "shop_location_province_code",
        "shop_location_municipality_code",
        "shop_location_barangay",
        "shop_location_name",
        "shop_location_address",
        "shop_location_map_pin",
        "shop_location_map_pin_approximate",
    )
    placeholders = ",".join(["%s"] * len(setting_keys))
    saved_settings = query_all(
        f"SELECT setting_key,setting_value FROM app_settings "
        f"WHERE setting_key IN ({placeholders})",
        setting_keys,
    )
    values = {row["setting_key"]: row["setting_value"] for row in saved_settings}
    province_code = values.get("shop_location_province_code", LAGUNA_PROVINCE_CODE)
    municipality_code = values.get("shop_location_municipality_code", LOCAL_DELIVERY_CITY_CODE)
    province = philippine_location_data()["provinces"].get(province_code)
    municipality = philippine_location_data()["cities"].get(municipality_code)
    if not province or not municipality or municipality.get("provinceCode") != province_code:
        province_code = LAGUNA_PROVINCE_CODE
        municipality_code = LOCAL_DELIVERY_CITY_CODE
        province = philippine_location_data()["provinces"][province_code]
        municipality = philippine_location_data()["cities"][municipality_code]

    shop_name = values.get("shop_location_name") or os.getenv(
        "SHOP_NAME", "FuzzyWire Bouquet"
    ).strip()
    barangay = values.get("shop_location_barangay", "").strip()
    complete_address = values.get("shop_location_address", SHOP_LOCATION_ADDRESS).strip()
    province_name = province["name"].strip()
    municipality_name = municipality["name"].strip()
    display_parts = [complete_address, barangay, municipality_name, province_name]
    normalized_parts = []
    seen = set()
    for part in display_parts:
        for segment in (part or "").split(","):
            segment = segment.strip()
            if segment and segment.casefold() not in seen:
                normalized_parts.append(segment)
                seen.add(segment.casefold())
    geocode_address_text = ", ".join(normalized_parts + ["Philippines"])
    pin = values.get("shop_location_map_pin")
    coords = None
    if pin:
        try:
            parts = pin.split(",")
            coords = (float(parts[0]), float(parts[1]))
        except Exception:
            coords = None
    if not coords:
        coords = (14.2464, 121.5074)
    return {
        "province_code": province_code,
        "province": province_name,
        "municipality_code": municipality_code,
        "municipality": municipality_name,
        "barangay": barangay,
        "shop_name": shop_name,
        "complete_address": complete_address,
        "address": ", ".join(normalized_parts),
        "geocode_address": geocode_address_text,
        "latitude": coords[0],
        "longitude": coords[1],
        "coordinates": coords,
        "is_configured": all(
            values.get(key)
            for key in (
                "shop_location_municipality_code",
                "shop_location_barangay",
                "shop_location_name",
                "shop_location_address",
            )
        ),
    }


def configured_shop_location():
    details = shop_location_details()
    if details["is_configured"]:
        saved_pin = query_one(
            "SELECT setting_value FROM app_settings "
            "WHERE setting_key='shop_location_map_pin'"
        )
        if saved_pin:
            try:
                latitude, longitude = (
                    float(value) for value in saved_pin["setting_value"].split(",", 1)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "The saved shop marker is invalid. Move it again in admin settings."
                ) from exc
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not is_within_laguna_map_bounds(latitude, longitude)
            ):
                raise ValueError("The saved shop marker must be within Laguna.")
            approximate_setting = query_one(
                "SELECT setting_value FROM app_settings "
                "WHERE setting_key='shop_location_map_pin_approximate'"
            )
            return {
                **details,
                "coordinates": (latitude, longitude),
                "latitude": latitude,
                "longitude": longitude,
                "approximate": bool(
                    approximate_setting and approximate_setting["setting_value"] == "1"
                ),
            }
        try:
            coordinates, approximate = geocode_shop_location_address(
                details["province_code"],
                details["municipality_code"],
                details["barangay"],
                details["complete_address"],
            )
            return {
                **details,
                "coordinates": coordinates,
                "latitude": coordinates[0],
                "longitude": coordinates[1],
                "approximate": approximate,
            }
        except requests.RequestException:
            raise

    saved_location = query_one(
        "SELECT setting_value FROM app_settings "
        "WHERE setting_key='shop_location_coordinates'"
    )
    if saved_location:
        try:
            latitude, longitude = (
                float(value) for value in saved_location["setting_value"].split(",", 1)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "The saved shop map pin is invalid. Update the shop address in admin settings."
            ) from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError(
                "The saved shop map pin is invalid. Update the shop address in admin settings."
            )
        if not is_within_laguna_map_bounds(latitude, longitude):
            raise ValueError("The saved shop map pin must be within Laguna.")
        return {
            **details,
            "coordinates": (latitude, longitude),
            "latitude": latitude,
            "longitude": longitude,
            "approximate": False,
        }

    if bool(SHOP_LOCATION_LATITUDE) != bool(SHOP_LOCATION_LONGITUDE):
        raise ValueError(
            "Set both SHOP_LOCATION_LATITUDE and SHOP_LOCATION_LONGITUDE."
        )
    if SHOP_LOCATION_LATITUDE:
        try:
            latitude = float(SHOP_LOCATION_LATITUDE)
            longitude = float(SHOP_LOCATION_LONGITUDE)
        except ValueError as exc:
            raise ValueError("The shop map coordinates must be valid numbers.") from exc
        if not is_within_laguna_map_bounds(latitude, longitude):
            raise ValueError("The shop map pin must be within Laguna.")
        return {
            **details,
            "coordinates": (latitude, longitude),
            "latitude": latitude,
            "longitude": longitude,
            "approximate": False,
        }
    shop_address = SHOP_LOCATION_ADDRESS
    if "laguna" not in shop_address.casefold():
        shop_address = f"{shop_address}, Cavinti, Laguna"
    try:
        coordinates = geocode_address(shop_address)
        return {
            **details,
            "coordinates": coordinates,
            "latitude": coordinates[0],
            "longitude": coordinates[1],
            "approximate": False,
        }
    except (ValueError, requests.RequestException):
        return {
            **details,
            "coordinates": (14.2464, 121.5074),
            "latitude": 14.2464,
            "longitude": 121.5074,
            "approximate": True,
        }


def geocode_shop_location_address(
    province_code, municipality_code, barangay, complete_address
):
    if not delivery_address_is_in_laguna(province_code, municipality_code):
        raise ValueError("Choose a municipality in Laguna.")
    location_data = philippine_location_data()
    province = location_data["provinces"][province_code]["name"].strip()
    municipality = location_data["cities"][municipality_code]["name"].strip()
    address_parts = [
        " ".join((complete_address or "").split()),
        " ".join((barangay or "").split()),
        municipality,
        province,
        "Philippines",
    ]
    normalized_parts = []
    seen = set()
    for part in address_parts:
        for segment in part.split(","):
            segment = segment.strip()
            if segment and segment.casefold() not in seen:
                normalized_parts.append(segment)
                seen.add(segment.casefold())
    try:
        return geocode_address(", ".join(normalized_parts)), False
    except ValueError:
        coordinates = geocode_address(f"{municipality}, {province}, Philippines")
        return coordinates, True


def great_circle_distance_km(start, destination):
    start_lat, start_lon = start
    destination_lat, destination_lon = destination

    def radians(value):
        return value * math.pi / 180

    d_lat = radians(destination_lat - start_lat)
    d_lon = radians(destination_lon - start_lon)
    lat1 = radians(start_lat)
    lat2 = radians(destination_lat)
    return 6371 * 2 * math.asin(
        min(
            1,
            math.sqrt(
                math.sin(d_lat / 2) ** 2
                + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
            ),
        )
    )


def estimate_delivery_distance_km(address):
    start = configured_shop_coordinates()
    destination = geocode_address(address)
    start_lat, start_lon = start
    destination_lat, destination_lon = destination
    haversine = great_circle_distance_km(start, destination)
    try:
        response = requests.get(
            f"{ROUTING_URL}/route/v1/driving/"
            f"{start_lon},{start_lat};{destination_lon},{destination_lat}",
            params={"overview": "false", "steps": "false"},
            timeout=15,
        )
        response.raise_for_status()
        routes = response.json().get("routes")
        if routes and routes[0].get("distance"):
            return (Decimal(str(routes[0]["distance"])) / Decimal("1000")).quantize(
                Decimal("0.01")
            )
    except (requests.RequestException, ValueError, KeyError, TypeError):
        current_app.logger.warning("Road-distance lookup failed; using geocoded straight-line distance.")
    return Decimal(str(haversine)).quantize(Decimal("0.01"))


def get_delivery_route(order_id, start, destination, order_type="Order"):
    route_table = {
        "Order": ("delivery_map_routes", "order_id"),
        "Custom": ("custom_delivery_map_routes", "custom_order_id"),
    }.get(order_type)
    if not route_table:
        raise ValueError("Unsupported delivery route type.")
    table_name, id_column = route_table
    start_lat, start_lon = start
    destination_lat, destination_lon = destination
    route_hash = hashlib.sha256(
        (
            f"{ROUTING_URL}|{start_lat:.7f},{start_lon:.7f}|"
            f"{destination_lat:.7f},{destination_lon:.7f}"
        ).encode("utf-8")
    ).hexdigest()
    cached = query_one(
        f"""SELECT route_hash,geometry_json,distance_meters,duration_seconds
           FROM `{table_name}` WHERE `{id_column}`=%s""",
        (order_id,),
    )
    if cached and cached["route_hash"] == route_hash:
        return (
            json.loads(cached["geometry_json"]),
            float(cached["distance_meters"]),
            int(cached["duration_seconds"]),
        )
    response = requests.get(
        f"{ROUTING_URL}/route/v1/driving/"
        f"{start_lon},{start_lat};{destination_lon},{destination_lat}",
        params={"overview": "full", "geometries": "geojson", "steps": "false"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    routes = payload.get("routes")
    if payload.get("code") != "Ok" or not routes:
        raise ValueError("A road route could not be found for this delivery.")
    route = routes[0]
    geometry = route.get("geometry", {}).get("coordinates")
    if not isinstance(geometry, list) or len(geometry) < 2:
        raise ValueError("The routing service returned an invalid route.")
    distance_meters = float(route["distance"])
    duration_seconds = max(1, int(route["duration"]))
    execute(
        f"""INSERT INTO `{table_name}`
           (`{id_column}`,route_hash,geometry_json,distance_meters,duration_seconds)
           VALUES (%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE route_hash=VALUES(route_hash),
             geometry_json=VALUES(geometry_json),distance_meters=VALUES(distance_meters),
             duration_seconds=VALUES(duration_seconds),created_at=CURRENT_TIMESTAMP""",
        (
            order_id,
            route_hash,
            json.dumps(geometry, separators=(",", ":")),
            distance_meters,
            duration_seconds,
        ),
    )
    return geometry, distance_meters, duration_seconds


def get_or_create_chat_conversation(user_id):
    conversation = query_one(
        "SELECT id FROM chat_conversations WHERE user_id=%s",
        (user_id,),
    )
    if conversation:
        return conversation["id"]
    execute(
        """INSERT INTO chat_conversations (user_id) VALUES (%s)
           ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)""",
        (user_id,),
    )
    conversation = query_one(
        "SELECT id FROM chat_conversations WHERE user_id=%s",
        (user_id,),
    )
    if not conversation:
        raise RuntimeError("Could not create or load the customer chat conversation.")
    return conversation["id"]


def current_user():
    return session.get("user")


def clear_unverified_customer_session():
    user = current_user()
    if (
        user and user.get("role") == "customer"
        and user.get("account_status", "Active") == "Deactivated"
    ):
        session.clear()
        flash("Your account is deactivated. Contact the shop for assistance.", "danger")
        return True
    return False


def login_required(fn):
    return role_required("customer")(fn)


def role_required(role, *additional_roles):
    allowed_roles = {role, *additional_roles}

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not current_user():
                flash("Please log in first.", "warning")
                return redirect(url_for("admin_login" if allowed_roles == {"admin"} else "login"))
            user_role = current_user().get("role")
            if user_role not in allowed_roles:
                abort(403)
            if user_role == "customer":
                if clear_unverified_customer_session():
                    return redirect(url_for("login"))
                status_only_endpoints = {
                    "customer_account_status",
                    "customer_verification_resubmit",
                    "logout",
                }
                user = current_user()
                if (
                    request.endpoint not in status_only_endpoints
                    and (
                        user.get("account_status", "Active") != "Active"
                        or user.get("verification_status", "Approved") != "Approved"
                        or not user.get("email_verified", True)
                    )
                ):
                    return redirect(url_for("customer_account_status"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = uuid.uuid4().hex
    return session["_csrf"]


def allowed_image(file):
    return bool(file and file.filename and "." in file.filename and
                file.filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS)


def save_upload(file):
    if not file or not file.filename:
        return None
    if not allowed_image(file):
        raise ValueError("Use PNG, JPG, JPEG, or WEBP image files only.")
    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(UPLOAD_DIR / filename)
    return filename


def save_verification_image(upload, field_label):
    if not upload or not upload.filename:
        raise ValueError(f"Upload the {field_label} image to continue.")
    extension = Path(upload.filename).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"The {field_label} must be a PNG, JPG, JPEG, or WEBP image.")
    content = upload.stream.read(VERIFICATION_IMAGE_MAX_BYTES + 1)
    if len(content) > VERIFICATION_IMAGE_MAX_BYTES:
        raise ValueError(f"The {field_label} image must be 5 MB or smaller.")
    signatures = {
        "png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": content.startswith(b"\xff\xd8\xff"),
        "jpeg": content.startswith(b"\xff\xd8\xff"),
        "webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
    }
    if not signatures[extension]:
        raise ValueError(f"The {field_label} file does not match its image format.")
    filename = f"verification-{uuid.uuid4().hex}.{extension}"
    (VERIFICATION_UPLOAD_DIR / filename).write_bytes(content)
    return filename


def remove_verification_images(*filenames):
    for filename in filenames:
        if not filename:
            continue
        path = (VERIFICATION_UPLOAD_DIR / Path(filename).name).resolve()
        if path.parent == VERIFICATION_UPLOAD_DIR.resolve() and path.is_file():
            path.unlink()


def save_drawing(data_url):
    if not data_url:
        return None
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("Invalid drawing image.")
    raw = base64.b64decode(data_url[len(prefix):], validate=True)
    if len(raw) > 5_000_000:
        raise ValueError("Drawing is too large.")
    filename = f"drawing_{uuid.uuid4().hex}.png"
    (UPLOAD_DIR / filename).write_bytes(raw)
    return filename


def notify(user_id, title, message, link_url):
    ensure_notification_link_schema()
    execute("INSERT INTO notifications (user_id,title,message,link_url) VALUES (%s,%s,%s,%s)",
            (user_id, title, message, link_url))


def notify_admins(title, message, link_url):
    ensure_notification_link_schema()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO notifications (user_id,title,message,link_url)
               SELECT id,%s,%s,%s FROM users WHERE role='admin'""",
            (title, message, link_url),
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        current_app.logger.exception("Could not notify admins.")
        raise
    finally:
        conn.close()


def reschedule_customer_order(table, order_id, user_id, slot_id):
    if table not in {"orders", "custom_orders"}:
        raise ValueError("Unsupported order type.")
    if slot_id and not str(slot_id).isdecimal():
        return "invalid_slot"
    ensure_delivery_slots_schema()
    ensure_gcash_schema()
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT id,user_id,`{'order_status' if table == 'orders' else 'status'}` AS status,
                       fulfillment_method FROM `{table}` WHERE id=%s AND user_id=%s FOR UPDATE""",
            (order_id, user_id),
        )
        order = cur.fetchone()
        if not order:
            return "missing"
        if order["status"] != "Pending":
            return "not_pending"
        slot = None
        if slot_id:
            slot_id = int(slot_id)
            cur.execute("SELECT * FROM delivery_slots WHERE id=%s FOR UPDATE", (slot_id,))
            slot = cur.fetchone()
            if (not slot or not slot["is_active"]
                    or slot["fulfillment_method"] != order["fulfillment_method"]
                    or slot["starts_at"] <= datetime.now()):
                return "invalid_slot"
            cur.execute(
                """SELECT
                     (SELECT COUNT(*) FROM orders
                      WHERE delivery_slot_id=%s AND order_status NOT IN ('Cancelled','Delivered','Picked Up')
                        AND NOT (%s='orders' AND id=%s))
                     +
                     (SELECT COUNT(*) FROM custom_orders
                      WHERE delivery_slot_id=%s AND status NOT IN ('Cancelled','Delivered','Picked Up')
                        AND NOT (%s='custom_orders' AND id=%s))
                     AS reserved""",
                (slot_id, table, order_id, slot_id, table, order_id),
            )
            if cur.fetchone()["reserved"] >= slot["capacity"]:
                return "full"
        cur.execute(
            f"UPDATE `{table}` SET delivery_slot_id=%s WHERE id=%s AND user_id=%s",
            (slot_id or None, order_id, user_id),
        )
        conn.commit()
        return "updated"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_customer_receipt(table, status_column, order_id, user_id):
    if (table, status_column) not in {("orders", "order_status"), ("custom_orders", "status")}:
        raise ValueError("Unsupported delivery confirmation table.")
    if table == "custom_orders":
        ensure_gcash_schema()
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        payment_column = (
            ", payment_method, payment_status,fulfillment_method"
            if table == "orders"
            else ", payment_method, payment_status,fulfillment_method"
        )
        cur.execute(
            f"SELECT `{status_column}`, customer_received_at{payment_column} FROM `{table}` WHERE id=%s AND user_id=%s",
            (order_id, user_id),
        )
        order = cur.fetchone()
        if not order:
            return "missing"
        expected_status = (
            "Picked Up" if order.get("fulfillment_method") == "Pickup"
            else "Delivered"
        )
        if order[status_column] != expected_status:
            return "not_delivered"
        should_mark_paid = order.get("payment_method") in {
            "Cash on Delivery", "Cash at Pickup",
        }
        if order["customer_received_at"]:
            if should_mark_paid and order["payment_status"] != "Paid":
                cur.execute(
                    f"UPDATE `{table}` SET payment_status='Paid' WHERE id=%s AND user_id=%s",
                    (order_id, user_id),
                )
                conn.commit()
            return "already_confirmed_paid" if should_mark_paid else "already_confirmed"
        payment_update = (
            ", payment_status=CASE WHEN payment_method IN ('Cash on Delivery','Cash at Pickup') THEN 'Paid' ELSE payment_status END"
        )
        cur.execute(
            f"""UPDATE `{table}` SET customer_received_at=CURRENT_TIMESTAMP{payment_update}
                WHERE id=%s AND user_id=%s AND `{status_column}`=%s
                  AND customer_received_at IS NULL""",
            (order_id, user_id, expected_status),
        )
        if cur.rowcount:
            conn.commit()
            return "confirmed_paid" if should_mark_paid else "confirmed"
        conn.rollback()
        return "already_confirmed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def flash_customer_receipt_result(result):
    if result == "confirmed_paid":
        flash("Thanks for confirming receipt. The order is now marked Paid.", "success")
    elif result == "confirmed":
        flash("Thanks for confirming receipt. Your GCash payment remains subject to shop review.", "success")
    elif result == "already_confirmed_paid":
        flash("You already confirmed receipt. The order is marked Paid.", "info")
    elif result == "already_confirmed":
        flash("You already confirmed receiving this order.", "info")
    else:
        flash("You can confirm receipt after the order is marked Delivered.", "warning")


def parse_voucher_form(form):
    name = form.get("name", "").strip()
    code = form.get("code", "").strip().upper()
    discount_type = form.get("discount_type", "")
    value = Decimal(form.get("discount_value", ""))
    minimum = Decimal(form.get("minimum_subtotal", "0"))
    max_claims_text = form.get("max_claims", "").strip()
    max_claims = int(max_claims_text) if max_claims_text else None
    expires_text = form.get("expires_at", "").strip()
    expires_at = datetime.strptime(expires_text, "%Y-%m-%dT%H:%M") if expires_text else None
    if not name or len(name) > 120:
        raise ValueError("Enter a voucher name of at most 120 characters.")
    if not re.fullmatch(r"[A-Z0-9_-]{3,40}", code):
        raise ValueError("Use 3–40 letters, numbers, hyphens, or underscores for the code.")
    if discount_type not in {"Percent", "Fixed"} or not value.is_finite() or not minimum.is_finite():
        raise ValueError("Enter valid discount details.")
    if minimum < 0 or (max_claims is not None and max_claims < 1):
        raise ValueError("Minimum order amount must be zero or more and available quantity must be positive.")
    if max_claims is not None and max_claims > 2147483647:
        raise ValueError("Available quantity is too large.")
    max_amount = Decimal("99999999.99")
    if value > max_amount or minimum > max_amount:
        raise ValueError("Discount and minimum order amounts cannot exceed ₱99,999,999.99.")
    if value.quantize(Decimal("0.01")) != value or minimum.quantize(Decimal("0.01")) != minimum:
        raise ValueError("Enter voucher amounts with no more than two decimal places.")
    if discount_type == "Percent" and not Decimal("0") < value <= Decimal("100"):
        raise ValueError("Percent discounts must be greater than 0 and no more than 100.")
    if discount_type == "Fixed" and value <= 0:
        raise ValueError("Fixed discounts must be greater than zero.")
    if expires_at and expires_at <= datetime.now():
        raise ValueError("Choose a future expiration date and time.")
    return name, code, discount_type, value, minimum, max_claims, expires_at
