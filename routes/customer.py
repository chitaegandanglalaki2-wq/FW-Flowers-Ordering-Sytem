import base64
import hashlib
import re
import secrets
import smtplib
from datetime import datetime
from decimal import Decimal, InvalidOperation

import mysql.connector
import requests
from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import generate_password_hash

from config import (
    ALLOWED_DELIVERY_CITY_CODES,
    ALLOWED_DELIVERY_CITY_CODES_CSV,
    ALLOWED_DELIVERY_MUNICIPALITIES,
    CUSTOM_ARRANGEMENTS,
    CUSTOM_BOUQUET_COLORS,
    CUSTOM_FLOWERS,
    CUSTOM_TIERS,
    DELIVERY_DISTANCE_MAX_KM,
    LAGUNA_PROVINCE_CODE,
    LOCAL_DELIVERY_CITY_CODE,
    OUTSIDE_LOCAL_DELIVERY_FEE,
    ORDER_STAGES,
    SIMULATED_DELIVERY_SPEED,
    SIZE_MULTIPLIER,
)
from database import db, execute, query_all, query_one
from models import Order, Product, User, Voucher
from services.common import (
    calculate_voucher_discount,
    delivery_address_is_in_laguna,
    delivery_address_is_in_service_area,
    delivery_fee_for_address,
    configured_shop_location,
    create_password_reset_token,
    establish_authenticated_session,
    current_user,
    ensure_chat_schema,
    ensure_custom_materials_schema,
    ensure_custom_payment_schema,
    ensure_customer_delivery_confirmation_schema,
    ensure_customer_profile_schema,
    ensure_customer_verification_schema,
    ensure_delivery_distance_schema,
    ensure_delivery_map_schema,
    ensure_delivery_slots_schema,
    ensure_feedback_product_schema,
    ensure_fulfillment_schema,
    ensure_gcash_schema,
    ensure_notification_link_schema,
    ensure_order_messaging_schema,
    ensure_order_queue_schema,
    ensure_password_reset_schema,
    ensure_promo_schema,
    ensure_wishlist_schema,
    estimate_delivery_distance_km,
    filter_orders_by_stage,
    flash_customer_receipt_result,
    gcash_payment_details,
    geocode_delivery_map_address,
    get_delivery_route,
    great_circle_distance_km,
    get_or_create_chat_conversation,
    load_customer_cart,
    login_required,
    notify,
    notify_admins,
    queue_email_otp,
    payment_proof_path,
    philippine_barangays_by_city,
    philippine_location_data,
    record_customer_receipt,
    remove_verification_images,
    reschedule_customer_order,
    reset_token_is_valid,
    resolve_delivery_address,
    role_required,
    save_customer_cart,
    save_drawing,
    save_password_reset,
    save_upload,
    save_verification_image,
    shop_location_details,
    send_order_email,
    verify_customer_email_token,
    verify_email_otp_challenge,
    trusted_device_exists,
    trust_device,
    update_admin_order_status,
    upload_order_payment_proof,
)
from routes.registry import route


@route("/api/philippines/cities/<city_code>/barangays")
def philippines_barangays(city_code):
    if city_code not in philippine_location_data()["cities"]:
        abort(404)
    return jsonify(philippine_barangays_by_city().get(city_code, []))


@route("/shop-location")
def shop_location():
    return render_template(
        "shop_location.html",
        shop_location=shop_location_details(),
    )


@route("/api/shop-location")
def shop_location_data():
    try:
        location = configured_shop_location()
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning("Could not locate the shop for the pickup map: %s", exc)
        return jsonify(
            error="The shop could not be located. Please check the shop address in settings."
        ), 503
    latitude, longitude = location["coordinates"]
    return jsonify(
        latitude=latitude,
        longitude=longitude,
        shop_name=location["shop_name"],
        address=location["address"],
        approximate=location["approximate"],
    )


@route("/")
def home():
    products = Product.all()
    available_vouchers = []
    user = current_user()
    if user and user.get("role") == "customer":
        ensure_promo_schema()
        available_vouchers = query_all(
            """SELECT v.* FROM promo_vouchers v
               WHERE v.is_active=1
                 AND (v.expires_at IS NULL OR v.expires_at>NOW())
                 AND (v.max_uses IS NULL OR v.used_count<v.max_uses)
                 AND NOT EXISTS (
                   SELECT 1 FROM voucher_claims c
                   WHERE c.voucher_id=v.id AND c.user_id=%s
                 )
                 AND (v.max_claims IS NULL OR
                      (SELECT COUNT(*) FROM voucher_claims c
                       WHERE c.voucher_id=v.id)<v.max_claims)
               ORDER BY v.created_at DESC LIMIT 5""",
            (user["id"],),
        )
    return render_template(
        "home.html", products=products, available_vouchers=available_vouchers
    )


@route("/terms")
def terms():
    return render_template("terms.html")


@route("/privacy")
def privacy():
    return render_template("privacy.html")


@route("/vouchers")
@role_required("customer")
def customer_vouchers():
    ensure_promo_schema()
    user_id = current_user()["id"]
    available_vouchers = query_all(
        """SELECT v.*,
                  (SELECT COUNT(*) FROM voucher_claims c WHERE c.voucher_id=v.id) AS claimed_count
           FROM promo_vouchers v
           WHERE v.is_active=1
             AND (v.expires_at IS NULL OR v.expires_at>NOW())
             AND (v.max_uses IS NULL OR v.used_count<v.max_uses)
             AND NOT EXISTS (
               SELECT 1 FROM voucher_claims c
               WHERE c.voucher_id=v.id AND c.user_id=%s
             )
             AND (v.max_claims IS NULL OR
                  (SELECT COUNT(*) FROM voucher_claims c
                   WHERE c.voucher_id=v.id)<v.max_claims)
           ORDER BY v.created_at DESC""",
        (user_id,),
    )
    claimed_vouchers = query_all(
        """SELECT v.*,c.claimed_at,r.redeemed_at,r.order_id,r.discount_amount AS redeemed_discount
           FROM voucher_claims c
           JOIN promo_vouchers v ON v.id=c.voucher_id
           LEFT JOIN voucher_redemptions r
             ON r.voucher_id=c.voucher_id AND r.user_id=c.user_id
           WHERE c.user_id=%s
           ORDER BY c.claimed_at DESC""",
        (user_id,),
    )
    return render_template(
        "customer_vouchers.html",
        available_vouchers=available_vouchers,
        claimed_vouchers=claimed_vouchers,
        now=datetime.now(),
    )


@route("/vouchers/<int:voucher_id>/claim", methods=["POST"])
@role_required("customer")
def customer_voucher_claim(voucher_id):
    try:
        Voucher.claim(voucher_id, current_user()["id"])
        flash("Voucher claimed! You can use its code at checkout.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("customer_vouchers"))


@route("/shop")
def shop():
    products = Product.all()
    wishlist_ids = set()
    if current_user() and current_user().get("role") == "customer":
        ensure_wishlist_schema()
        wishlist_ids = {
            row["product_id"] for row in query_all(
                "SELECT product_id FROM wishlists WHERE user_id=%s",
                (current_user()["id"],),
            )
        }
    reviews = query_all("""SELECT f.product_id,f.rating,f.comment,f.created_at
                           FROM feedback f
                           WHERE f.product_id IS NOT NULL
                           ORDER BY f.created_at DESC""")
    reviews_by_product = {}
    for review in reviews:
        reviews_by_product.setdefault(review["product_id"], []).append(review)
    return render_template("shop.html", products=products, reviews_by_product=reviews_by_product,
                           wishlist_ids=wishlist_ids)


@route("/shop/<int:product_id>")
def product_detail(product_id):
    ensure_feedback_product_schema()
    product = Product.get(product_id)
    if not product or not product["is_active"]:
        abort(404)
    reviews = query_all(
        """SELECT f.rating,f.comment,f.created_at
           FROM feedback f WHERE f.product_id=%s
           ORDER BY f.created_at DESC LIMIT 6""",
        (product_id,),
    )
    rating = query_one(
        "SELECT COALESCE(AVG(rating),0) AS avg_rating,COUNT(*) AS rating_count FROM feedback WHERE product_id=%s",
        (product_id,),
    )
    product.update(rating)
    is_wishlisted = False
    if current_user() and current_user().get("role") == "customer":
        ensure_wishlist_schema()
        is_wishlisted = bool(query_one(
            "SELECT product_id FROM wishlists WHERE user_id=%s AND product_id=%s",
            (current_user()["id"], product_id),
        ))
    return render_template("product_detail.html", product=product, reviews=reviews,
                           is_wishlisted=is_wishlisted)


@route("/wishlist")
@role_required("customer")
def wishlist():
    ensure_wishlist_schema()
    saved_ids = {
        row["product_id"] for row in query_all(
            "SELECT product_id FROM wishlists WHERE user_id=%s ORDER BY created_at DESC",
            (current_user()["id"],),
        )
    }
    products = [product for product in Product.all(active_only=False) if product["id"] in saved_ids]
    return render_template("wishlist.html", products=products)


@route("/wishlist/add/<int:product_id>", methods=["POST"])
@role_required("customer")
def wishlist_add(product_id):
    product = Product.get(product_id)
    if not product or not product["is_active"]:
        abort(404)
    ensure_wishlist_schema()
    execute(
        "INSERT IGNORE INTO wishlists (user_id,product_id) VALUES (%s,%s)",
        (current_user()["id"], product_id),
    )
    flash(f"{product['name']} saved to your wishlist.", "success")
    destination = request.form.get("return_to")
    if destination == "product":
        return redirect(url_for("product_detail", product_id=product_id))
    if destination == "wishlist":
        return redirect(url_for("wishlist"))
    return redirect(url_for("shop"))


@route("/wishlist/remove/<int:product_id>", methods=["POST"])
@role_required("customer")
def wishlist_remove(product_id):
    ensure_wishlist_schema()
    execute(
        "DELETE FROM wishlists WHERE user_id=%s AND product_id=%s",
        (current_user()["id"], product_id),
    )
    return redirect(url_for("wishlist"))


def set_dummy_email_otp(purpose):
    session["pending_otp"] = {
        "challenge_id": 0,
        "user_id": 0,
        "purpose": purpose,
        "dummy": True,
    }


def keep_active_email_otp(user_id, purpose):
    challenge = query_one(
        """SELECT id FROM email_otp_challenges
           WHERE user_id=%s AND purpose=%s AND used_at IS NULL
             AND expires_at>UTC_TIMESTAMP()
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, purpose),
    )
    if challenge:
        session["pending_otp"] = {
            "challenge_id": challenge["id"],
            "user_id": user_id,
            "purpose": purpose,
        }
    else:
        session["pending_otp"] = {
            "challenge_id": 0,
            "user_id": user_id,
            "purpose": purpose,
            "retry": True,
        }


def validate_password_strength(password: str) -> tuple[bool, str]:
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number (0-9)."
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must contain at least one special character (e.g. !@#$%^&*)."
    return True, ""


@route("/register", methods=["GET", "POST"])
def register():
    ensure_customer_verification_schema()
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        terms_accepted = request.form.get("terms_accepted") == "on"
        pwd_valid, pwd_error = validate_password_strength(password)
        if not terms_accepted:
            flash("Please agree to the terms and conditions before registering.", "warning")
        elif not name or not email:
            flash("Enter your name and email address.", "danger")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "danger")
        elif not pwd_valid:
            flash(pwd_error, "danger")
        elif password != confirm_password:
            flash("Passwords do not match. Please confirm your password again.", "danger")
        elif query_one("SELECT id FROM users WHERE email=%s", (email,)):
            flash("That email address is already registered.", "danger")
        else:
            verification_images = {}
            account_created = False
            try:
                delivery_address, address_fields = resolve_delivery_address(
                    request.form, required=False
                )
                verification_images["front"] = save_verification_image(
                    request.files.get("verification_id_front"), "front of your valid ID"
                )
                verification_images["back"] = save_verification_image(
                    request.files.get("verification_id_back"), "back of your valid ID"
                )
                verification_images["selfie"] = save_verification_image(
                    request.files.get("verification_selfie"), "selfie holding your valid ID"
                )
                user_id = User.create(
                    name,
                    email,
                    password,
                    delivery_address=delivery_address or None,
                    address_fields=address_fields,
                    verification_images=verification_images,
                )
                account_created = True
                notify(
                    user_id,
                    "Registration submitted for verification",
                    "Your account was created. Verify your email address and wait for admin approval before signing in.",
                    url_for("login"),
                )
                notify_admins(
                    "New customer verification request",
                    f"{name} submitted front/back ID photos and a selfie for account verification.",
                    url_for("admin_verifications"),
                )
                user = {
                    "id": user_id,
                    "full_name": name,
                    "email": email,
                }
                try:
                    queue_email_otp(user, "registration")
                except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                    current_app.logger.exception(
                        "Could not send registration OTP to user_id=%s", user_id
                    )
                    flash(
                        "Your registration was saved, but we could not send the email code. "
                        "Use the resend option after email delivery is available.",
                        "warning",
                    )
                    return redirect(url_for("resend_verification_email"))
                flash(
                    "Registration saved. Enter the code sent to your Gmail; admin approval is also required.",
                    "success",
                )
                return redirect(url_for("verify_otp"))
            except mysql.connector.IntegrityError:
                if not account_created:
                    remove_verification_images(*verification_images.values())
                flash("That email address is already registered.", "danger")
            except ValueError as exc:
                if not account_created:
                    remove_verification_images(*verification_images.values())
                flash(str(exc), "danger")
            except Exception:
                if not account_created:
                    remove_verification_images(*verification_images.values())
                raise
    return render_template(
        "register.html",
        register_address_values=request.form if request.method == "POST" else {},
        laguna_province_code=LAGUNA_PROVINCE_CODE,
    )


@route("/login", methods=["GET", "POST"])
def login():
    ensure_customer_verification_schema()
    if current_user() and current_user().get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    if current_user() and current_user().get("role") == "customer":
        return redirect(
            url_for("customer_account_status")
            if current_user().get("account_status", "Active") != "Active"
            or current_user().get("verification_status", "Approved") != "Approved"
            or not current_user().get("email_verified", True)
            else url_for("home")
        )
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.authenticate(email, password)

        if user and user["role"] == "admin":
            flash("Incorrect email or password. Please check your details and try again.", "danger")
        elif user and user["role"] == "customer" and user.get("account_status") == "Deactivated":
            flash("This account has been deactivated. Contact the shop for assistance.", "danger")
        elif user and user["role"] == "customer" and not user.get("email_verified", True):
            try:
                queue_email_otp(user, "registration")
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not send registration OTP to user_id=%s", user["id"]
                )
                flash("We could not send a verification code. Please try again later.", "danger")
            else:
                flash("Enter the email code to verify your account before continuing.", "warning")
                return redirect(url_for("verify_otp"))
        elif user and user["role"] == "customer":
            device_token = request.cookies.get("fuzzywire_device", "")
            if trusted_device_exists(user["id"], device_token):
                return establish_authenticated_session(user, session.get("cart", {}))
            try:
                queue_email_otp(user, "new_device")
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not send new-device OTP to user_id=%s", user["id"]
                )
                flash("We could not send a verification code. Please try again later.", "danger")
            else:
                flash("We sent a code to your Gmail. Verify this new device to continue.", "info")
                return redirect(url_for("verify_otp"))
        else:
            flash("Incorrect email or password. Please check your details and try again.", "danger")
    return render_template("login.html")


@route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    ensure_customer_verification_schema()
    pending = session.get("pending_otp")
    if not isinstance(pending, dict):
        flash("Request a new verification code to continue.", "warning")
        return redirect(url_for("login"))
    if request.method == "POST":
        if request.form.get("action") == "resend":
            if pending.get("dummy"):
                flash("If an account uses this address, a new code has been sent.", "success")
                return render_template("verify_otp.html", purpose=pending.get("purpose"))
            user = User.by_id(pending.get("user_id"))
            if not user:
                set_dummy_email_otp(pending.get("purpose"))
                flash("If an account uses this address, a new code has been sent.", "success")
                return render_template("verify_otp.html", purpose=pending.get("purpose"))
            try:
                queue_email_otp(user, pending.get("purpose"))
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not resend OTP to user_id=%s", user["id"]
                )
                keep_active_email_otp(user["id"], pending.get("purpose"))
                flash("If an account uses this address, a new code has been sent.", "success")
            else:
                flash("A new code has been sent. Earlier codes are no longer valid.", "success")
            return render_template("verify_otp.html", purpose=pending.get("purpose"))

        code = request.form.get("otp", "").strip()
        if pending.get("dummy") or not verify_email_otp_challenge(
            pending.get("challenge_id"),
            pending.get("user_id"),
            pending.get("purpose"),
            code,
        ):
            flash("That code is incorrect, expired, or already used. Try again or resend it.", "danger")
            return render_template("verify_otp.html", purpose=pending.get("purpose"))

        purpose = pending.get("purpose")
        user_id = pending.get("user_id")
        if purpose == "registration":
            session.pop("pending_otp", None)
            execute(
                """UPDATE users SET email_verified=1,
                   account_status=CASE
                     WHEN account_status='Deactivated' THEN 'Deactivated'
                     WHEN verification_status='Rejected' THEN 'Rejected'
                     WHEN verification_status='Approved' THEN 'Active'
                     ELSE 'Pending' END
                   WHERE id=%s AND role='customer' AND account_status<>'Deactivated'""",
                (user_id,),
            )
            flash(
                "Your email is verified. Your account is pending admin ID approval.",
                "success",
            )
            return redirect(url_for("login"))
        if purpose == "password_reset":
            ensure_password_reset_schema()
            token, token_hash = create_password_reset_token(user_id)
            session.pop("pending_otp", None)
            session["reset_token_hash"] = token_hash
            return redirect(url_for("reset_password", token=token))
        if purpose == "new_device":
            user = User.by_id(user_id)
            if (
                not user
                or (
                    user.get("role") == "customer"
                    and user.get("account_status") == "Deactivated"
                )
            ):
                session.clear()
                flash("This account is deactivated. Contact the shop for assistance.", "danger")
                return redirect(url_for("login"))
            device_token = secrets.token_urlsafe(32)
            trust_device(user_id, device_token)
            session.pop("pending_otp", None)
            return establish_authenticated_session(
                user, session.get("cart", {}), device_token=device_token
            )
    return render_template("verify_otp.html", purpose=pending.get("purpose"))


@route("/verify-email/<token>")
def verify_email(token):
    ensure_customer_verification_schema()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return render_template("email_verification_result.html", verified=False)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if verify_customer_email_token(token_hash):
        flash(
            "Your email has been verified. Your account still needs admin approval before you can log in.",
            "success",
        )
        return redirect(url_for("login"))
    return render_template("email_verification_result.html", verified=False)


@route("/resend-verification-email", methods=["GET", "POST"])
def resend_verification_email():
    ensure_customer_verification_schema()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if len(email) > 160 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "danger")
            return render_template("resend_verification_email.html")
        user = query_one(
            """SELECT id,full_name,email FROM users
               WHERE LOWER(email)=%s AND role='customer' AND email_verified=0""",
            (email,),
        )
        if user:
            try:
                queue_email_otp(user, "registration")
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not resend registration OTP to user_id=%s", user["id"]
                )
                keep_active_email_otp(user["id"], "registration")
        else:
            set_dummy_email_otp("registration")
        flash("A verification code has been sent to the email address, if the account requires verification.", "success")
        return redirect(url_for("verify_otp"))
    return render_template("resend_verification_email.html")


@route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    ensure_customer_verification_schema()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if len(email) > 160 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "danger")
            return render_template("forgot_password.html")
        user = query_one(
            "SELECT id,full_name,email FROM users WHERE LOWER(email)=%s",
            (email,),
        )
        if user:
            try:
                queue_email_otp(user, "password_reset")
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not send password-reset OTP to user_id=%s", user["id"]
                )
                keep_active_email_otp(user["id"], "password_reset")
        else:
            set_dummy_email_otp("password_reset")
        flash("If an account uses that email, a password-reset code has been sent.", "success")
        return redirect(url_for("verify_otp"))
    return render_template("forgot_password.html")


@route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    ensure_password_reset_schema()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_valid = (
        session.get("reset_token_hash") == token_hash
        and reset_token_is_valid(token_hash)
    )
    if not token_valid:
        return render_template("reset_password.html", token=token, token_valid=False)

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        pwd_valid, pwd_error = validate_password_strength(password)
        if not pwd_valid:
            flash(pwd_error, "danger")
        elif len(password) > 128:
            flash("Password must not exceed 128 characters.", "danger")
        elif password != confirm_password:
            flash("The passwords do not match.", "danger")
        elif not save_password_reset(token_hash, generate_password_hash(password)):
            session.pop("reset_token_hash", None)
            return render_template("reset_password.html", token=token, token_valid=False)
        else:
            session.clear()
            flash("Your password has been reset. You can now log in.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password.html", token=token, token_valid=True)


@route("/account-status")
@role_required("customer")
def customer_account_status():
    return render_template("account_status.html", customer=current_user())


@route("/verification/resubmit", methods=["POST"])
@role_required("customer")
def customer_verification_resubmit():
    user = current_user()
    if user.get("verification_status") != "Rejected":
        flash("Your ID verification is not currently eligible for resubmission.", "warning")
        return redirect(url_for("customer_account_status"))
    verification_images = {}
    try:
        verification_images["front"] = save_verification_image(
            request.files.get("verification_id_front"), "front of your valid ID"
        )
        verification_images["back"] = save_verification_image(
            request.files.get("verification_id_back"), "back of your valid ID"
        )
        verification_images["selfie"] = save_verification_image(
            request.files.get("verification_selfie"), "selfie holding your valid ID"
        )
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute(
                """UPDATE users SET verification_status='Pending',
                   account_status='Pending',verification_rejection_reason=NULL,
                   verification_id_front_filename=%s,
                   verification_id_back_filename=%s,verification_selfie_filename=%s
                   WHERE id=%s AND role='customer' AND verification_status='Rejected'""",
                (
                    verification_images["front"],
                    verification_images["back"],
                    verification_images["selfie"],
                    user["id"],
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                remove_verification_images(*verification_images.values())
                flash("Your verification status changed. Refresh the page and try again.", "warning")
                return redirect(url_for("customer_account_status"))
            conn.commit()
        except Exception:
            conn.rollback()
            remove_verification_images(*verification_images.values())
            raise
        finally:
            conn.close()
    except ValueError as exc:
        remove_verification_images(*verification_images.values())
        flash(str(exc), "danger")
        return redirect(url_for("customer_account_status"))
    notify_admins(
        "Customer ID verification resubmitted",
        f"{user['full_name']} resubmitted ID photos and a selfie for review.",
        url_for("admin_verifications"),
    )
    flash("Your ID was resubmitted. The admin team has been notified.", "success")
    return redirect(url_for("customer_account_status"))


@route("/logout", methods=["POST"])
def logout():
    if not current_user():
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))
    session.clear()
    return redirect(url_for("home"))


@route("/profile", methods=["GET", "POST"])
@login_required
def customer_profile():
    if current_user().get("role") != "customer":
        abort(403)
    ensure_customer_profile_schema()
    user_id = current_user()["id"]
    submitted_profile = None
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        address_form = {
            key: request.form.get(key, "").strip()
            for key in (
                "delivery_region_code", "delivery_province_code", "delivery_city_code",
                "delivery_barangay_code", "delivery_barangay_name", "delivery_postal_code",
                "delivery_postal_code_other", "delivery_street_address",
            )
        }
        address_error = None
        try:
            delivery_address, address_fields = resolve_delivery_address(address_form)
        except ValueError as exc:
            delivery_address = ""
            address_fields = {}
            address_error = str(exc)
        profile_image = request.files.get("profile_image")
        submitted_profile = {
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "delivery_address": delivery_address,
            **address_form,
        }
        if (not full_name or len(full_name) > 120 or not email or len(email) > 160
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
                or len(phone) > 30 or address_error):
            if address_error:
                flash(address_error, "danger")
            else:
                flash("Enter a valid name and email. Phone must be at most 30 characters.", "danger")
        else:
            try:
                profile_image_name = save_upload(profile_image)
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                try:
                    if profile_image_name:
                        execute(
                            """UPDATE users SET full_name=%s,email=%s,phone=%s,
                                      delivery_address=%s,profile_image=%s,
                                      delivery_region_code=%s,delivery_province_code=%s,
                                      delivery_city_code=%s,delivery_barangay_code=%s,
                                      delivery_barangay=%s,delivery_postal_code=%s,
                                      delivery_street_address=%s
                               WHERE id=%s AND role='customer'""",
                            (full_name, email, phone or None, delivery_address or None,
                             profile_image_name,
                             address_fields.get("delivery_region_code"),
                             address_fields.get("delivery_province_code"),
                             address_fields.get("delivery_city_code"),
                             address_fields.get("delivery_barangay_code"),
                             address_fields.get("delivery_barangay"),
                             address_fields.get("delivery_postal_code"),
                             address_fields.get("delivery_street_address"), user_id),
                        )
                    else:
                        execute(
                            """UPDATE users SET full_name=%s,email=%s,phone=%s,
                                      delivery_address=%s,delivery_region_code=%s,
                                      delivery_province_code=%s,delivery_city_code=%s,
                                      delivery_barangay_code=%s,delivery_barangay=%s,
                                      delivery_postal_code=%s,
                                      delivery_street_address=%s
                               WHERE id=%s AND role='customer'""",
                            (full_name, email, phone or None, delivery_address or None,
                             address_fields.get("delivery_region_code"),
                             address_fields.get("delivery_province_code"),
                             address_fields.get("delivery_city_code"),
                             address_fields.get("delivery_barangay_code"),
                             address_fields.get("delivery_barangay"),
                             address_fields.get("delivery_postal_code"),
                             address_fields.get("delivery_street_address"), user_id),
                        )
                except mysql.connector.IntegrityError:
                    flash("That email is already used by another account.", "danger")
                else:
                    user = dict(current_user())
                    user.update({
                        "full_name": full_name,
                        "email": email,
                        "phone": phone or None,
                        "delivery_address": delivery_address or None,
                    })
                    if profile_image_name:
                        user["profile_image"] = profile_image_name
                    session["user"] = user
                    flash("Your profile has been updated.", "success")
                    return redirect(url_for("customer_profile"))
    profile = query_one(
        """SELECT full_name,email,phone,delivery_address,profile_image,
                  delivery_region_code,delivery_province_code,delivery_city_code,
                  delivery_barangay_code,delivery_barangay,delivery_postal_code,
                  delivery_street_address
           FROM users WHERE id=%s AND role='customer'""",
        (user_id,),
    )
    if not profile:
        abort(404)
    if submitted_profile:
        profile.update(submitted_profile)
    if not profile.get("delivery_street_address") and not profile.get("delivery_region_code"):
        profile["delivery_street_address"] = profile.get("delivery_address") or ""
    regular_stats = query_one(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(order_status='Delivered'),0) AS delivered,
                  COALESCE(SUM(order_status NOT IN ('Delivered','Cancelled')),0) AS pending
           FROM orders WHERE user_id=%s""",
        (user_id,),
    )
    custom_stats = query_one(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(status='Delivered'),0) AS delivered,
                  COALESCE(SUM(status NOT IN ('Delivered','Cancelled')),0) AS pending
           FROM custom_orders WHERE user_id=%s""",
        (user_id,),
    )
    order_stats = {
        key: regular_stats[key] + custom_stats[key]
        for key in ("total", "delivered", "pending")
    }
    return render_template(
        "profile.html",
        profile=profile,
        order_stats=order_stats,
        laguna_province_code=LAGUNA_PROVINCE_CODE,
    )


@route("/cart")
@login_required
def cart():
    if current_user().get("role") == "customer":
        cart_items = load_customer_cart(current_user()["id"])
        session["cart"] = cart_items
    else:
        cart_items = session.get("cart", {})
    items, subtotal = [], Decimal("0.00")
    for pid, qty_text in cart_items.items():
        product = Product.get(int(pid))
        if product:
            qty = int(qty_text)
            line = Decimal(str(product["price"])) * qty
            subtotal += line
            items.append({"product": product, "quantity": qty, "line_total": line})
    return render_template("cart.html", items=items, subtotal=subtotal)


@route("/cart/checkout-selected", methods=["POST"])
@login_required
def cart_checkout_selected():
    if current_user().get("role") != "customer":
        abort(403)
    cart_items = session.get("cart", {})
    selected_ids = request.form.getlist("product_ids")
    if not isinstance(cart_items, dict) or not selected_ids:
        flash("Select at least one bouquet to check out.", "warning")
        return redirect(url_for("cart"))

    selected_cart = {}
    for product_id in selected_ids:
        if product_id not in cart_items:
            continue
        try:
            numeric_product_id = int(product_id)
            quantity = int(cart_items[product_id])
        except (TypeError, ValueError):
            current_app.logger.warning("Ignoring invalid cart quantity for product_id=%s", product_id)
            continue
        if quantity < 1 or quantity > 50:
            flash("A selected bouquet has an invalid quantity. Please update your cart.", "danger")
            return redirect(url_for("cart"))
        product = Product.get(numeric_product_id)
        if not product or not product["is_active"] or quantity > product["stock"]:
            flash("A selected bouquet is unavailable or has insufficient stock. Please update your cart.", "danger")
            return redirect(url_for("cart"))
        selected_cart[product_id] = quantity
    if not selected_cart:
        flash("The selected bouquets are no longer in your cart. Please select items again.", "warning")
        return redirect(url_for("cart"))

    session.pop("buy_now_cart", None)
    session["selected_checkout_cart"] = selected_cart
    return redirect(url_for("checkout", selected=1))


@route("/cart/add/<int:product_id>", methods=["POST"])
def cart_add(product_id):
    product = Product.get(product_id)
    if not product or not product["is_active"]:
        abort(404)
    user = current_user()
    cart_items = (
        load_customer_cart(user["id"])
        if user and user.get("role") == "customer"
        else session.get("cart", {})
    )
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 50 or quantity > product["stock"]:
        flash("Please choose a valid quantity within the available stock.", "danger")
    else:
        cart_items[str(product_id)] = min(int(cart_items.get(str(product_id), 0)) + quantity, product["stock"])
        session["cart"] = cart_items
        if user and user.get("role") == "customer":
            save_customer_cart(user["id"], cart_items)
        flash("Added to cart.", "success")
    return redirect(request.referrer or url_for("shop"))


@route("/buy-now/<int:product_id>", methods=["POST"])
@login_required
def buy_now(product_id):
    if current_user().get("role") != "customer":
        abort(403)
    product = Product.get(product_id)
    if not product or not product["is_active"]:
        abort(404)
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 50 or quantity > product["stock"]:
        flash("Please choose a valid quantity within the available stock.", "danger")
        return redirect(url_for("product_detail", product_id=product_id))
    session.pop("selected_checkout_cart", None)
    session["buy_now_cart"] = {str(product_id): quantity}
    return redirect(url_for("checkout", buy_now=1))


@route("/cart/remove/<int:product_id>", methods=["POST"])
@login_required
def cart_remove(product_id):
    user = current_user()
    cart_items = (
        load_customer_cart(user["id"])
        if user and user.get("role") == "customer"
        else session.get("cart", {})
    )
    cart_items.pop(str(product_id), None)
    session["cart"] = cart_items
    if user and user.get("role") == "customer":
        save_customer_cart(user["id"], cart_items)
    return redirect(url_for("cart"))


@route("/cart/update/<int:product_id>", methods=["POST"])
@login_required
def cart_update_quantity(product_id):
    user = current_user()
    is_json = request.is_json or request.accept_mimetypes.best == "application/json"

    def invalid(message, status=400):
        if is_json:
            return jsonify(success=False, message=message), status
        flash(message, "danger")
        return redirect(url_for("cart"))

    try:
        quantity = int(request.form.get("quantity", ""))
    except (TypeError, ValueError):
        return invalid("Enter a valid product quantity.")
    if quantity < 1 or quantity > 50:
        return invalid("Quantity must be between 1 and 50.")

    product = Product.get(product_id)
    if not product or not product["is_active"]:
        return invalid("This product is no longer available.", 404)
    if quantity > product["stock"]:
        return invalid(f"Only {product['stock']} item(s) are currently in stock.")

    cart_items = (
        load_customer_cart(user["id"])
        if user.get("role") == "customer"
        else session.get("cart", {})
    )
    product_key = str(product_id)
    if product_key not in cart_items:
        return invalid("This product is no longer in your cart.", 404)

    cart_items[product_key] = quantity
    session["cart"] = cart_items
    if user.get("role") == "customer":
        save_customer_cart(user["id"], cart_items)

    line_total = Decimal(str(product["price"])) * quantity
    if is_json:
        return jsonify(
            success=True,
            quantity=quantity,
            line_total=f"{line_total:.2f}",
        )
    flash("Cart quantity updated.", "success")
    return redirect(url_for("cart"))


@route("/vouchers/preview")
@role_required("customer")
def voucher_preview():
    code = request.args.get("code", "").strip().upper()
    if not code or len(code) > 40:
        return jsonify({"valid": False, "message": "Enter a promo code."}), 400
    mode = request.args.get("mode", "")
    cart = (
        session.get("buy_now_cart", {}) if mode == "buy_now"
        else session.get("selected_checkout_cart", {}) if mode == "selected"
        else load_customer_cart(current_user()["id"])
    )
    subtotal = Decimal("0.00")
    for product_id, quantity_text in cart.items():
        try:
            product = Product.get(int(product_id))
            quantity = int(quantity_text)
        except (TypeError, ValueError):
            return jsonify({"valid": False, "message": "Your cart contains an invalid item."}), 400
        if not product or not product["is_active"] or quantity < 1 or quantity > product["stock"]:
            return jsonify({"valid": False, "message": "A bouquet in your cart is unavailable."}), 400
        subtotal += Decimal(str(product["price"])) * quantity
    ensure_promo_schema()
    voucher = query_one("SELECT * FROM promo_vouchers WHERE code=%s", (code,))
    has_claimed = bool(query_one(
        """SELECT id FROM voucher_claims
           WHERE voucher_id=%s AND user_id=%s LIMIT 1""",
        (voucher["id"], current_user()["id"]),
    )) if voucher else False
    if voucher and not has_claimed:
        return jsonify({
            "valid": False,
            "message": "Claim this voucher from the Vouchers page before applying it.",
        }), 400
    already_used = bool(query_one(
        """SELECT id FROM voucher_redemptions
           WHERE voucher_id=%s AND user_id=%s LIMIT 1""",
        (voucher["id"], current_user()["id"]),
    )) if voucher else False
    try:
        discount = calculate_voucher_discount(voucher, subtotal, already_used)
    except ValueError as exc:
        return jsonify({"valid": False, "message": str(exc)}), 400
    return jsonify({
        "valid": True,
        "code": code,
        "discount": f"{discount:.2f}",
        "subtotal": f"{subtotal:.2f}",
        "message": f"{code} applied: ₱{discount:.2f} off.",
    })


@route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    ensure_gcash_schema()
    ensure_delivery_distance_schema()
    ensure_fulfillment_schema()
    ensure_delivery_slots_schema()
    ensure_customer_profile_schema()
    profile = query_one(
        """SELECT full_name,email,phone,delivery_address,delivery_region_code,
                  delivery_province_code,delivery_city_code,delivery_barangay_code,
                  delivery_barangay,delivery_postal_code,delivery_street_address
           FROM users WHERE id=%s""",
        (current_user()["id"],),
    )
    if not profile.get("delivery_street_address") and not profile.get("delivery_region_code"):
        profile["delivery_street_address"] = profile.get("delivery_address") or ""
    has_usable_saved_address = (
        delivery_address_is_in_laguna(
            profile.get("delivery_province_code"), profile.get("delivery_city_code")
        )
        and delivery_address_is_in_service_area(
            profile.get("delivery_province_code"), profile.get("delivery_city_code")
        )
    )
    cart_items = load_customer_cart(current_user()["id"])
    session["cart"] = cart_items
    buy_now_cart = session.get("buy_now_cart")
    selected_checkout_cart = session.get("selected_checkout_cart")
    is_buy_now = (
        request.args.get("buy_now") == "1"
        and isinstance(buy_now_cart, dict)
        and bool(buy_now_cart)
    )
    is_selected_checkout = (
        not is_buy_now
        and request.args.get("selected") == "1"
        and isinstance(selected_checkout_cart, dict)
        and bool(selected_checkout_cart)
    )
    checkout_cart = (
        buy_now_cart if is_buy_now
        else selected_checkout_cart if is_selected_checkout
        else session.get("cart", {})
    )
    checkout_url = (
        url_for("checkout", buy_now=1) if is_buy_now
        else url_for("checkout", selected=1) if is_selected_checkout
        else url_for("checkout")
    )
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in
                ("customer_name", "phone", "delivery_address", "delivery_notes",
                 "gift_message", "payment_method", "fulfillment_method")}
        form["promo_code"] = request.form.get("promo_code", "").strip().upper()
        form["delivery_slot_id"] = request.form.get("delivery_slot_id", "").strip()
        session["checkout_promo_code"] = form["promo_code"]
        if len(form["gift_message"]) > 500:
            flash("Gift messages must be 500 characters or fewer.", "danger")
            return redirect(checkout_url)
        if not all(form[k] for k in ("customer_name", "phone")):
            flash("Please enter your name and contact number.", "danger")
            return redirect(checkout_url)
        if form["fulfillment_method"] not in ("Delivery", "Pickup"):
            flash("Choose delivery or pickup at the shop.", "danger")
            return redirect(checkout_url)
        if form["payment_method"] not in ("Cash", "GCash"):
            flash("Invalid payment method.", "danger")
            return redirect(checkout_url)
        if form["payment_method"] == "Cash":
            form["payment_method"] = (
                "Cash at Pickup" if form["fulfillment_method"] == "Pickup"
                else "Cash on Delivery"
            )
        if form["fulfillment_method"] == "Pickup":
            form["delivery_address"] = shop_location_details()["address"]
            form["delivery_notes"] = ""
            distance = Decimal("0.00")
            form["delivery_city_code"] = None
        else:
            address_choice = request.form.get("delivery_address_choice", "")
            if has_usable_saved_address and address_choice == "saved":
                form["delivery_address"] = profile["delivery_address"]
                form["delivery_city_code"] = profile.get("delivery_city_code")
                delivery_province_code = profile.get("delivery_province_code")
            else:
                if has_usable_saved_address and address_choice != "new":
                    flash("Choose your saved profile address or enter a new address for this order.", "danger")
                    return redirect(checkout_url)
                try:
                    form["delivery_address"], address_fields = resolve_delivery_address(
                        request.form, required=True, require_service_area=True
                    )
                    form["delivery_city_code"] = address_fields["delivery_city_code"]
                    delivery_province_code = address_fields["delivery_province_code"]
                except ValueError as exc:
                    flash(str(exc), "danger")
                    return redirect(checkout_url)
            if not delivery_address_is_in_laguna(
                delivery_province_code, form["delivery_city_code"]
            ):
                flash(
                    "Delivery is available only within Laguna. Choose a Laguna address or pick up at the shop.",
                    "danger",
                )
                return redirect(checkout_url)
            if request.form.get("use_manual_distance") == "on":
                try:
                    distance = Decimal(request.form.get("delivery_distance_km", "").strip())
                    if not distance.is_finite() or not Decimal("0") < distance <= DELIVERY_DISTANCE_MAX_KM:
                        raise ValueError
                except (InvalidOperation, ValueError):
                    flash("Enter a manual distance greater than 0 and no more than 9,999.99 km.", "danger")
                    return redirect(checkout_url)
            else:
                try:
                    distance = estimate_delivery_distance_km(form["delivery_address"])
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    distance = Decimal("0.00")
                    current_app.logger.warning(
                        "Could not estimate delivery distance; continuing with the validated Laguna address: %s",
                        exc,
                    )
        form["delivery_distance_km"] = distance
        try:
            order_id, total = Order.create(current_user(), checkout_cart, form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(checkout_url if is_buy_now or is_selected_checkout else url_for("cart"))
        session.pop("checkout_promo_code", None)
        if is_buy_now:
            session.pop("buy_now_cart", None)
        elif is_selected_checkout:
            remaining_cart = dict(cart_items)
            for product_id, selected_quantity in checkout_cart.items():
                try:
                    remaining_quantity = int(remaining_cart.get(product_id, 0)) - int(selected_quantity)
                except (TypeError, ValueError):
                    remaining_quantity = 0
                if remaining_quantity > 0:
                    remaining_cart[product_id] = remaining_quantity
                else:
                    remaining_cart.pop(product_id, None)
            session["cart"] = remaining_cart
            save_customer_cart(current_user()["id"], remaining_cart)
            session.pop("selected_checkout_cart", None)
        else:
            session["cart"] = {}
            save_customer_cart(current_user()["id"], {})
            session.pop("buy_now_cart", None)
            session.pop("selected_checkout_cart", None)
        send_order_email(
            profile.get("email"),
            f"FuzzyWire order #{order_id} placed",
            (
                f"Your order #{order_id} has been placed.\n"
                f"Total: PHP {total:.2f}\n"
                f"Payment: {form['payment_method']} ({'Pending admin review' if form['payment_method'] == 'GCash' else 'unpaid until receipt'})\n"
                "View your order in My Orders for updates."
            ),
        )
        flash(f"Order #{order_id} placed successfully.", "success")
        return redirect(url_for("order_detail", order_id=order_id))
    if not checkout_cart:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("shop"))
    cart_subtotal = Decimal("0.00")
    for product_id, quantity_text in checkout_cart.items():
        product = Product.get(int(product_id))
        if product:
            cart_subtotal += Decimal(str(product["price"])) * int(quantity_text)
    delivery_slots = query_all(
        """SELECT s.id,s.starts_at,s.fulfillment_method,s.capacity,
                  s.capacity-COUNT(o.id)-
                    (SELECT COUNT(*) FROM custom_orders c
                     WHERE c.delivery_slot_id=s.id AND c.status NOT IN ('Cancelled','Delivered','Picked Up'))
                    AS remaining
           FROM delivery_slots s
           LEFT JOIN orders o ON o.delivery_slot_id=s.id
             AND o.order_status NOT IN ('Cancelled','Delivered','Picked Up')
           WHERE s.is_active=1 AND s.starts_at>NOW()
           GROUP BY s.id,s.starts_at,s.fulfillment_method,s.capacity
           HAVING remaining>0
           ORDER BY s.starts_at,s.fulfillment_method"""
    )
    return render_template("checkout.html", cart_subtotal=cart_subtotal, profile=profile,
                           local_delivery_city_code=LOCAL_DELIVERY_CITY_CODE,
                           outside_local_delivery_fee=OUTSIDE_LOCAL_DELIVERY_FEE,
                           profile_is_cavinti=(
                               delivery_fee_for_address(
                                   profile.get("delivery_address"),
                                   profile.get("delivery_city_code"),
                               ) == Decimal("0.00")
                           ),
                           delivery_distance_max_km=DELIVERY_DISTANCE_MAX_KM,
                           laguna_province_code=LAGUNA_PROVINCE_CODE,
                           allowed_delivery_municipalities=ALLOWED_DELIVERY_MUNICIPALITIES,
                           allowed_delivery_city_codes_csv=ALLOWED_DELIVERY_CITY_CODES_CSV,
                           has_usable_saved_address=has_usable_saved_address,
                           shop_location=shop_location_details(), is_buy_now=is_buy_now,
                           is_selected_checkout=is_selected_checkout,
                           promo_code=session.get("checkout_promo_code", ""),
                           delivery_slots=delivery_slots,
                           gcash=gcash_payment_details())


@route("/orders")
@login_required
def my_orders():
    ensure_gcash_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_order_messaging_schema()
    ensure_fulfillment_schema()
    ensure_order_queue_schema()
    user_id = current_user()["id"]
    orders = query_all(
        """SELECT o.*, 'Bouquet' AS order_type,q.queue_id
           FROM orders o JOIN order_queue q
             ON q.order_type='Order' AND q.order_id=o.id
           WHERE o.user_id=%s""",
        (user_id,),
    )
    custom_orders = query_all(
        """SELECT c.*, 'Custom bouquet' AS order_type,
                  c.status AS order_status,c.total_price AS total,
                  q.queue_id
           FROM custom_orders c JOIN order_queue q
             ON q.order_type='Custom' AND q.order_id=c.id
           WHERE c.user_id=%s""",
        (user_id,),
    )
    all_orders = orders + custom_orders
    all_orders.sort(key=lambda order: order["created_at"], reverse=True)
    all_orders.sort(
        key=lambda order: (
            order.get("order_status") in {"Delivered", "Picked Up", "Cancelled"},
            order["queue_id"] if order.get("order_status") not in {"Delivered", "Picked Up", "Cancelled"} else 0,
        )
    )
    visible_orders, stage_counts, active_stage = filter_orders_by_stage(
        all_orders, request.args.get("stage", "all")
    )
    return render_template(
        "my_orders.html",
        orders=visible_orders,
        order_stages=ORDER_STAGES,
        order_stage_labels=dict(ORDER_STAGES),
        stage_counts=stage_counts,
        active_stage=active_stage,
        gcash=gcash_payment_details(),
    )


@route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    ensure_gcash_schema()
    ensure_delivery_distance_schema()
    ensure_fulfillment_schema()
    ensure_delivery_slots_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_order_messaging_schema()
    order = query_one("SELECT * FROM orders WHERE id=%s AND user_id=%s",
                      (order_id, current_user()["id"]))
    if not order:
        abort(404)
    items = query_all("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
    delivery = query_one("SELECT * FROM deliveries WHERE order_id=%s", (order_id,))
    scheduled_slot = query_one(
        "SELECT id,starts_at,fulfillment_method FROM delivery_slots WHERE id=%s",
        (order["delivery_slot_id"],),
    ) if order.get("delivery_slot_id") else None
    available_slots = query_all(
        """SELECT s.id,s.starts_at,s.fulfillment_method,s.capacity,
                  s.capacity-
                    (SELECT COUNT(*) FROM orders o
                     WHERE o.delivery_slot_id=s.id AND o.order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                    -
                    (SELECT COUNT(*) FROM custom_orders c
                     WHERE c.delivery_slot_id=s.id AND c.status NOT IN ('Cancelled','Delivered','Picked Up'))
                    AS remaining
           FROM delivery_slots s WHERE s.is_active=1 AND s.starts_at>NOW()
           ORDER BY s.starts_at,s.fulfillment_method"""
    )
    return render_template("order_detail.html", order=order, items=items, delivery=delivery,
                           scheduled_slot=scheduled_slot,
                           delivery_slots=available_slots,
                           gcash=gcash_payment_details())


@route("/payment-proof/<order_type>/<int:order_id>")
@role_required("customer", "admin")
def payment_proof_file(order_type, order_id):
    path = payment_proof_path(order_type, order_id)
    response = send_file(path, conditional=False, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@route("/orders/<int:order_id>/payment-proof", methods=["POST"])
@role_required("customer")
def upload_order_payment_proof_route(order_id):
    try:
        upload_order_payment_proof(
            "Order", order_id, current_user()["id"],
            request.files.get("payment_proof"),
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("order_detail", order_id=order_id))
    notify(
        current_user()["id"], "GCash proof submitted",
        f"Your proof for order #{order_id} is pending admin review.",
        url_for("order_detail", order_id=order_id),
    )
    flash("Payment proof uploaded. Your order remains Pending until the shop verifies it.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@route("/custom-orders/<int:custom_id>/payment-proof", methods=["POST"])
@role_required("customer")
def upload_custom_payment_proof_route(custom_id):
    try:
        upload_order_payment_proof(
            "Custom", custom_id, current_user()["id"],
            request.files.get("payment_proof"),
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("customer_custom_detail", custom_id=custom_id))
    notify(
        current_user()["id"], "GCash proof submitted",
        f"Your proof for custom bouquet request #C{custom_id} is pending admin review.",
        url_for("customer_custom_detail", custom_id=custom_id),
    )
    flash("Payment proof uploaded. Your request remains Pending until the shop verifies it.", "success")
    return redirect(url_for("customer_custom_detail", custom_id=custom_id))


@route("/orders/<int:order_id>/reorder", methods=["POST"])
@role_required("customer")
def reorder(order_id):
    order = query_one(
        "SELECT id FROM orders WHERE id=%s AND user_id=%s",
        (order_id, current_user()["id"]),
    )
    if not order:
        abort(404)
    previous_items = query_all(
        "SELECT product_id,quantity FROM order_items WHERE order_id=%s",
        (order_id,),
    )
    if not previous_items:
        flash("This order has no available bouquet items to reorder.", "warning")
        return redirect(url_for("my_orders"))
    cart_items = load_customer_cart(current_user()["id"])
    updated_cart = dict(cart_items)
    try:
        for item in previous_items:
            product = Product.get(item["product_id"]) if item["product_id"] else None
            if not product or not product["is_active"] or int(product["stock"]) < int(item["quantity"]):
                raise ValueError("A bouquet from that order is unavailable or has insufficient stock.")
            key = str(product["id"])
            total_quantity = int(updated_cart.get(key, 0)) + int(item["quantity"])
            if total_quantity > min(int(product["stock"]), 50):
                raise ValueError(f"Not enough stock for {product['name']} to add the full reorder.")
            updated_cart[key] = total_quantity
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("my_orders"))
    save_customer_cart(current_user()["id"], updated_cart)
    session["cart"] = updated_cart
    flash("The bouquets from your previous order were added to your cart.", "success")
    return redirect(url_for("cart"))


@route("/orders/<int:order_id>/cancel", methods=["POST"])
@role_required("customer")
def customer_cancel_order(order_id):
    ensure_gcash_schema()
    order = query_one(
        "SELECT id,user_id,order_status,customer_name,customer_email FROM orders WHERE id=%s AND user_id=%s",
        (order_id, current_user()["id"]),
    )
    if not order:
        abort(404)
    if order["order_status"] != "Pending":
        flash("Orders can only be cancelled while Pending.", "warning")
        return redirect(url_for("order_detail", order_id=order_id))
    _, changed, _, message = update_admin_order_status(
        "orders", "order_status", order_id, current_user()["id"], "Cancelled",
        "Cancelled by customer before processing.",
        f"Order #{order_id} was cancelled by the customer.",
    )
    if message:
        flash(message, "warning")
    elif changed:
        execute("UPDATE deliveries SET delivery_status='Cancelled' WHERE order_id=%s", (order_id,))
        notify_admins(
            "Customer cancelled an order",
            f"{order['customer_name']} cancelled order #{order_id} while Pending.",
            url_for("admin_order_detail", order_id=order_id),
        )
        send_order_email(
            order["customer_email"],
            f"FuzzyWire order #{order_id} cancelled",
            f"Your pending order #{order_id} was cancelled at your request.",
        )
        flash("Your pending order was cancelled.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@route("/custom-orders/<int:custom_id>/cancel", methods=["POST"])
@role_required("customer")
def customer_cancel_custom_order(custom_id):
    ensure_gcash_schema()
    order = query_one(
        """SELECT c.id,c.user_id,c.status,u.email FROM custom_orders c
           JOIN users u ON u.id=c.user_id
           WHERE c.id=%s AND c.user_id=%s""",
        (custom_id, current_user()["id"]),
    )
    if not order:
        abort(404)
    if order["status"] != "Pending":
        flash("Custom requests can only be cancelled while Pending.", "warning")
        return redirect(url_for("customer_custom_detail", custom_id=custom_id))
    _, changed, _, message = update_admin_order_status(
        "custom_orders", "status", custom_id, current_user()["id"], "Cancelled",
        "Cancelled by customer before processing.",
        f"Custom request #C{custom_id} was cancelled by the customer.",
    )
    if message:
        flash(message, "warning")
    elif changed:
        notify_admins(
            "Customer cancelled a custom request",
            f"Custom bouquet request #C{custom_id} was cancelled while Pending.",
            url_for("admin_custom_detail", custom_id=custom_id),
        )
        send_order_email(
            order["email"],
            f"FuzzyWire custom request #C{custom_id} cancelled",
            f"Your pending custom bouquet request #C{custom_id} was cancelled at your request.",
        )
        flash("Your pending custom request was cancelled.", "success")
    return redirect(url_for("customer_custom_detail", custom_id=custom_id))


@route("/orders/<int:order_id>/reschedule", methods=["POST"])
@role_required("customer")
def customer_reschedule_order(order_id):
    result = reschedule_customer_order(
        "orders", order_id, current_user()["id"],
        request.form.get("delivery_slot_id", "").strip(),
    )
    if result == "missing":
        abort(404)
    flash(
        {
            "updated": "Your order schedule was updated.",
            "not_pending": "Only Pending orders can be rescheduled.",
            "invalid_slot": "Choose a future slot matching this order's delivery or pickup method.",
            "full": "That slot is full. Choose another available time.",
        }[result],
        "success" if result == "updated" else "warning",
    )
    return redirect(url_for("order_detail", order_id=order_id))


@route("/custom-orders/<int:custom_id>/reschedule", methods=["POST"])
@role_required("customer")
def customer_reschedule_custom_order(custom_id):
    result = reschedule_customer_order(
        "custom_orders", custom_id, current_user()["id"],
        request.form.get("delivery_slot_id", "").strip(),
    )
    if result == "missing":
        abort(404)
    flash(
        {
            "updated": "Your custom order schedule was updated.",
            "not_pending": "Only Pending custom orders can be rescheduled.",
            "invalid_slot": "Choose a future slot matching this order's delivery or pickup method.",
            "full": "That slot is full. Choose another available time.",
        }[result],
        "success" if result == "updated" else "warning",
    )
    return redirect(url_for("customer_custom_detail", custom_id=custom_id))


@route("/orders/<int:order_id>/confirm-delivery", methods=["POST"])
@login_required
def customer_confirm_delivery(order_id):
    ensure_gcash_schema()
    ensure_customer_delivery_confirmation_schema()
    result = record_customer_receipt("orders", "order_status", order_id, current_user()["id"])
    if result == "missing":
        abort(404)
    flash_customer_receipt_result(result)
    return redirect(url_for("order_detail", order_id=order_id))


@route("/custom-orders/<int:custom_id>/confirm-delivery", methods=["POST"])
@login_required
def customer_confirm_custom_delivery(custom_id):
    ensure_gcash_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_custom_payment_schema()
    result = record_customer_receipt("custom_orders", "status", custom_id, current_user()["id"])
    if result == "missing":
        abort(404)
    flash_customer_receipt_result(result)
    return redirect(url_for("customer_custom_detail", custom_id=custom_id))


@route("/custom-order", methods=["GET", "POST"])
@login_required
def custom_order():
    if current_user().get("role") != "customer":
        abort(403)
    ensure_gcash_schema()
    ensure_custom_materials_schema()
    ensure_delivery_slots_schema()
    ensure_customer_profile_schema()
    profile = query_one(
        """SELECT full_name,phone,delivery_address,delivery_region_code,
                  delivery_province_code,delivery_city_code,delivery_barangay_code,
                  delivery_barangay,delivery_postal_code,delivery_street_address
           FROM users WHERE id=%s""",
        (current_user()["id"],),
    )
    has_usable_saved_address = (
        delivery_address_is_in_laguna(
            profile.get("delivery_province_code"), profile.get("delivery_city_code")
        )
        and delivery_address_is_in_service_area(
            profile.get("delivery_province_code"), profile.get("delivery_city_code")
        )
    )
    if request.method == "POST":
        flower_val = request.form.get("flower_type", "").strip()
        size = request.form.get("bouquet_size", "").strip()
        flower_color = request.form.get("flower_color", "").strip()
        wrap_val = request.form.get("wrapping_style", "").strip()
        try:
            qty = int(request.form.get("quantity", "1"))
            if not 1 <= qty <= 20:
                raise ValueError("Bouquet quantity must be between 1 and 20.")
            if size not in CUSTOM_TIERS and size not in SIZE_MULTIPLIER:
                raise ValueError("Please choose a valid bouquet size or flower amount.")
            if flower_color not in CUSTOM_BOUQUET_COLORS:
                raise ValueError("Choose a valid bouquet color.")
            material_rows = query_all(
                """SELECT id,name,category,unit,unit_price,stock,is_active
                   FROM custom_materials WHERE is_active=1"""
            )
            material_map = {str(row["id"]): row for row in material_rows}
            flower = material_map.get(flower_val)
            if flower:
                flower_name = flower["name"]
            elif flower_val in CUSTOM_FLOWERS:
                flower_name = flower_val
                matched_row = next((r for r in material_rows if r["category"] == "Flower" and flower_val.lower() in r["name"].lower()), None)
                if matched_row:
                    flower = matched_row
            else:
                flower_name = flower_val or "Tulips"

            wrap = material_map.get(wrap_val)
            if wrap:
                wrapping_name = wrap["name"]
            elif wrap_val in CUSTOM_ARRANGEMENTS:
                wrapping_name = wrap_val
            else:
                wrapping_name = wrap_val or "Standard Bouquet"

            selected_materials = []
            multiplier = Decimal(str(SIZE_MULTIPLIER.get(size, 1)))
            if size in CUSTOM_TIERS:
                tier_unit_price = CUSTOM_TIERS[size]
                base_line = (tier_unit_price * qty).quantize(Decimal("0.01"))
                if flower:
                    selected_materials.append((flower["id"], f"{flower_name} ({size})", flower["category"], Decimal(qty), tier_unit_price, base_line))
                price = base_line
            elif flower and wrap:
                flower_line = (Decimal(str(flower["unit_price"])) * multiplier * qty).quantize(Decimal("0.01"))
                selected_materials.append((flower["id"], flower["name"], flower["category"], multiplier * qty,
                                           Decimal(str(flower["unit_price"])), flower_line))
                wrap_price = Decimal(str(wrap["unit_price"]))
                wrap_line = (wrap_price * qty).quantize(Decimal("0.01"))
                selected_materials.append((wrap["id"], wrap["name"], wrap["category"], Decimal(qty),
                                           wrap_price, wrap_line))
                price = flower_line + wrap_line
            else:
                price = (Decimal("120.00") * qty).quantize(Decimal("0.01"))

            extra_ids = request.form.getlist("extra_material_id")
            if len(extra_ids) > 20 or len(set(extra_ids)) != len(extra_ids):
                raise ValueError("Choose no more than 20 unique extra materials.")
            for extra_id in extra_ids:
                extra = material_map.get(extra_id)
                if not extra or extra["category"] in ("Flower", "Wrapping"):
                    raise ValueError("Choose valid extra materials.")
                extra_quantity = int(request.form.get(f"extra_quantity_{extra_id}", "1"))
                if not 1 <= extra_quantity <= 20:
                    raise ValueError("Extra material quantities must be between 1 and 20.")
                extra_line = Decimal(str(extra["unit_price"])) * extra_quantity * qty
                selected_materials.append((extra["id"], extra["name"], extra["category"],
                                           Decimal(extra_quantity * qty),
                                           Decimal(str(extra["unit_price"])), extra_line))
                price += extra_line
            phone = request.form.get("phone", "").strip()
            fulfillment_method = request.form.get("fulfillment_method", "")
            payment_choice = request.form.get("payment_method", "")
            raw_slot = request.form.get("delivery_slot_id", "").strip()
            schedule_pref = None
            slot_id = None
            if raw_slot in ("Weekdays", "Weekends"):
                schedule_pref = raw_slot
            elif raw_slot:
                try:
                    slot_id = int(raw_slot)
                except (ValueError, TypeError):
                    slot_id = None
            if not phone or len(phone) > 30:
                raise ValueError("Enter a valid contact number.")
            if fulfillment_method not in {"Delivery", "Pickup"}:
                raise ValueError("Choose delivery or pickup.")
            if payment_choice not in {"Cash", "GCash"}:
                raise ValueError("Choose cash or GCash payment.")
            payment_method = (
                "GCash" if payment_choice == "GCash"
                else "Cash at Pickup" if fulfillment_method == "Pickup"
                else "Cash on Delivery"
            )
            if fulfillment_method == "Pickup":
                delivery_address = shop_location_details()["address"]
                distance = Decimal("0.00")
                delivery_city_code = None
            else:
                if has_usable_saved_address and request.form.get("delivery_address_choice") == "saved":
                    delivery_address = profile["delivery_address"]
                    delivery_province_code = profile.get("delivery_province_code")
                    delivery_city_code = profile.get("delivery_city_code")
                else:
                    if has_usable_saved_address and request.form.get("delivery_address_choice") != "new":
                        raise ValueError("Choose your saved profile address or enter a new address.")
                    delivery_address, address_fields = resolve_delivery_address(
                        request.form, required=True, require_service_area=True
                    )
                    delivery_province_code = address_fields["delivery_province_code"]
                    delivery_city_code = address_fields["delivery_city_code"]
                if not delivery_address_is_in_laguna(
                    delivery_province_code, delivery_city_code
                ):
                    raise ValueError(
                        "Delivery is available only within Laguna. Choose a Laguna address or pick up at the shop."
                    )
                if not delivery_address_is_in_service_area(
                    delivery_province_code, delivery_city_code
                ):
                    raise ValueError(
                        "Delivery is currently available only in Cavinti, Lumban, Kalayaan, Pagsanjan, Santa Cruz, and Pila, Laguna."
                    )
                if request.form.get("use_manual_distance") == "on":
                    try:
                        distance = Decimal(request.form.get("delivery_distance_km", "").strip())
                        if not distance.is_finite() or not Decimal("0") < distance <= DELIVERY_DISTANCE_MAX_KM:
                            raise ValueError
                    except (InvalidOperation, ValueError) as exc:
                        raise ValueError(
                            "Enter a valid manual distance for this delivery."
                        ) from exc
                else:
                    try:
                        distance = estimate_delivery_distance_km(delivery_address)
                    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                        distance = Decimal("0.00")
                        current_app.logger.warning(
                            "Could not estimate custom-order delivery distance; continuing with the validated Laguna address: %s",
                            exc,
                        )
            delivery_fee = delivery_fee_for_address(
                delivery_address,
                city_code=delivery_city_code,
                fulfillment_method=fulfillment_method,
            )
            total_price = price + delivery_fee
            reference = save_upload(request.files.get("reference_image"))
            drawing = save_drawing(request.form.get("drawing_data", ""))
            ensure_order_queue_schema()
            ensure_notification_link_schema()
            conn = db()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT GET_LOCK('fuzzywire_order_queue_create',10)")
                queue_lock = cursor.fetchone()
                if not queue_lock or queue_lock[0] != 1:
                    raise ValueError("The order queue is busy. Please submit your request again shortly.")
                if slot_id:
                    slot_cursor = conn.cursor(dictionary=True)
                    slot_cursor.execute(
                        "SELECT * FROM delivery_slots WHERE id=%s FOR UPDATE",
                        (slot_id,),
                    )
                    slot = slot_cursor.fetchone()
                    if (not slot or not slot["is_active"]
                            or slot["fulfillment_method"] != fulfillment_method
                            or slot["starts_at"] <= datetime.now()):
                        raise ValueError("Choose a valid future delivery or pickup time slot.")
                    slot_cursor.execute(
                        """SELECT
                             (SELECT COUNT(*) FROM orders
                              WHERE delivery_slot_id=%s AND order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                             +
                             (SELECT COUNT(*) FROM custom_orders
                              WHERE delivery_slot_id=%s AND status NOT IN ('Cancelled','Delivered','Picked Up'))
                             AS reserved""",
                        (slot_id, slot_id),
                    )
                    if slot_cursor.fetchone()["reserved"] >= slot["capacity"]:
                        raise ValueError("That delivery or pickup slot is full.")
                custom_notes = request.form.get("custom_message", "").strip()[:500]
                if schedule_pref and schedule_pref not in custom_notes:
                    custom_notes = f"Schedule: {schedule_pref}. {custom_notes}".strip()[:500]
                cursor.execute("""INSERT INTO custom_orders
                    (user_id,flower_type,flower_color,bouquet_size,size_multiplier,wrapping_style,
                     custom_message,reference_image,drawing_image,quantity,total_price,
                     payment_method,payment_status,phone,fulfillment_method,delivery_address,
                     delivery_distance_km,delivery_fee,delivery_slot_id,schedule_preference)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (current_user()["id"], flower_name, flower_color, size, multiplier, wrapping_name,
                     custom_notes, reference, drawing, qty,
                     total_price, payment_method, "Pending" if payment_method == "GCash" else "Unpaid",
                     phone, fulfillment_method, delivery_address, distance, delivery_fee, slot_id, schedule_pref))
                custom_id = cursor.lastrowid
                cursor.execute(
                    """INSERT INTO order_queue (order_type,order_id,created_at)
                       SELECT 'Custom',id,created_at FROM custom_orders WHERE id=%s""",
                    (custom_id,),
                )
                cursor.execute(
                    """INSERT INTO notifications (user_id,title,message,link_url)
                       SELECT id,'New order received',%s,%s FROM users WHERE role='admin'""",
                    (
                        f"A new custom bouquet request #C{custom_id} is pending.",
                        url_for("admin_custom_detail", custom_id=custom_id),
                    ),
                )
                if selected_materials:
                    cursor.executemany(
                        """INSERT INTO custom_order_materials
                           (custom_order_id,material_id,material_name,category,quantity,unit_price,line_total)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        [(custom_id, *material) for material in selected_materials],
                    )
                conn.commit()
            except mysql.connector.Error:
                conn.rollback()
                current_app.logger.exception("Could not create custom bouquet order")
                raise
            finally:
                conn.close()
            notify(
                current_user()["id"],
                "Custom order submitted",
                f"Custom bouquet request #{custom_id} is pending review.",
                url_for("customer_custom_detail", custom_id=custom_id),
            )
            send_order_email(
                current_user().get("email"),
                f"FuzzyWire custom request #C{custom_id} placed",
                (
                    f"Your custom bouquet request #C{custom_id} has been placed.\n"
                    f"Total: PHP {total_price:.2f}\nPayment: {payment_method}\n"
                    "Open My Orders to upload GCash proof or follow the request."
                ),
            )
            flash(f"Custom bouquet request #{custom_id} sent to the admin.", "success")
            return redirect(url_for("customer_custom_detail", custom_id=custom_id))
        except (ValueError, base64.binascii.Error) as exc:
            flash(str(exc), "danger")
    materials = query_all(
        """SELECT id,name,category,unit,unit_price,stock,low_stock_threshold
           FROM custom_materials WHERE is_active=1 ORDER BY category,name"""
    )
    delivery_slots = query_all(
        """SELECT s.id,s.starts_at,s.fulfillment_method,s.capacity,
                  s.capacity-
                    (SELECT COUNT(*) FROM orders o
                     WHERE o.delivery_slot_id=s.id AND o.order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                    -
                    (SELECT COUNT(*) FROM custom_orders c
                     WHERE c.delivery_slot_id=s.id AND c.status NOT IN ('Cancelled','Delivered','Picked Up'))
                    AS remaining
           FROM delivery_slots s WHERE s.is_active=1 AND s.starts_at>NOW()
           ORDER BY s.starts_at,s.fulfillment_method"""
    )
    return render_template(
        "custom_order.html",
        custom_flowers=CUSTOM_FLOWERS,
        custom_tiers=CUSTOM_TIERS,
        custom_arrangements=CUSTOM_ARRANGEMENTS,
        flower_materials=[row for row in materials if row["category"] == "Flower"],
        wrapping_materials=[row for row in materials if row["category"] == "Wrapping"],
        extra_materials=[row for row in materials if row["category"] not in ("Flower", "Wrapping")],
        sizes=SIZE_MULTIPLIER,
        profile=profile,
        shop_location=shop_location_details(),
        laguna_province_code=LAGUNA_PROVINCE_CODE,
        allowed_delivery_municipalities=ALLOWED_DELIVERY_MUNICIPALITIES,
        allowed_delivery_city_codes_csv=ALLOWED_DELIVERY_CITY_CODES_CSV,
        has_usable_saved_address=has_usable_saved_address,
        delivery_slots=delivery_slots,
        gcash=gcash_payment_details(),
        delivery_distance_max_km=DELIVERY_DISTANCE_MAX_KM,
        outside_local_delivery_fee=OUTSIDE_LOCAL_DELIVERY_FEE,
        bouquet_colors=CUSTOM_BOUQUET_COLORS,
    )


@route("/custom-orders/<int:custom_id>")
@role_required("customer")
def customer_custom_detail(custom_id):
    ensure_gcash_schema()
    ensure_custom_materials_schema()
    ensure_delivery_slots_schema()
    ensure_order_queue_schema()
    order = query_one(
        """SELECT c.*,u.full_name,u.email FROM custom_orders c
           JOIN users u ON u.id=c.user_id
           WHERE c.id=%s AND c.user_id=%s""",
        (custom_id, current_user()["id"]),
    )
    if not order:
        abort(404)
    materials = query_all(
        """SELECT material_name,category,quantity,unit_price,line_total
           FROM custom_order_materials WHERE custom_order_id=%s ORDER BY id""",
        (custom_id,),
    )
    scheduled_slot = query_one(
        "SELECT id,starts_at,fulfillment_method FROM delivery_slots WHERE id=%s",
        (order["delivery_slot_id"],),
    ) if order.get("delivery_slot_id") else None
    available_slots = query_all(
        """SELECT s.id,s.starts_at,s.fulfillment_method,s.capacity,
                  s.capacity-
                    (SELECT COUNT(*) FROM orders o
                     WHERE o.delivery_slot_id=s.id AND o.order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                    -
                    (SELECT COUNT(*) FROM custom_orders c2
                     WHERE c2.delivery_slot_id=s.id AND c2.status NOT IN ('Cancelled','Delivered','Picked Up'))
                    AS remaining
           FROM delivery_slots s WHERE s.is_active=1 AND s.starts_at>NOW()
           ORDER BY s.starts_at,s.fulfillment_method"""
    )
    return render_template(
        "custom_order_detail.html",
        order=order,
        materials=materials,
        scheduled_slot=scheduled_slot,
        delivery_slots=available_slots,
        gcash=gcash_payment_details(),
    )


@route("/track")
@login_required
def track():
    ensure_delivery_distance_schema()
    ensure_fulfillment_schema()
    ensure_order_queue_schema()
    orders = query_all(
        """SELECT o.*,d.delivery_status,d.estimated_delivery,
                  d.updated_at AS delivery_updated,q.queue_id
           FROM orders o
           LEFT JOIN deliveries d ON d.order_id=o.id
           JOIN order_queue q ON q.order_type='Order' AND q.order_id=o.id
           WHERE o.user_id=%s
           ORDER BY q.queue_id ASC""",
        (current_user()["id"],),
    )
    return render_template("track.html", orders=orders)


@route("/api/orders/<int:order_id>/delivery-map")
@role_required("customer", "admin")
def delivery_map_data(order_id):
    ensure_fulfillment_schema()
    ensure_gcash_schema()
    ensure_delivery_map_schema()
    order = query_one(
        """SELECT id,user_id,delivery_address,fulfillment_method,order_status,updated_at
           FROM orders WHERE id=%s""",
        (order_id,),
    )
    if not order:
        abort(404)
    if current_user()["role"] == "customer" and order["user_id"] != current_user()["id"]:
        abort(404)
    if order["fulfillment_method"] == "Pickup":
        try:
            shop_location = configured_shop_location()
            shop = shop_location["coordinates"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            current_app.logger.warning(
                "Could not load pickup map for order_id=%s: %s", order_id, exc
            )
            return jsonify(
                error="The shop location could not be loaded. Check the shop address in settings."
            ), 503
        return jsonify(
            order_id=order_id,
            order_status=order["order_status"],
            fulfillment_method="Pickup",
            destination_address=shop_location["address"],
            shop_name=shop_location["shop_name"],
            shop_address=shop_location["address"],
            shop_location_is_approximate=shop_location["approximate"],
            start={"latitude": shop[0], "longitude": shop[1]},
            destination={"latitude": shop[0], "longitude": shop[1]},
            route=[],
            route_distance_km=0,
            progress=0,
            eta_minutes=None,
            simulated=False,
        )
    if order["fulfillment_method"] != "Delivery" or not order["delivery_address"]:
        abort(404)
    try:
        shop_location = configured_shop_location()
        start = shop_location["coordinates"]
        destination, destination_is_approximate = geocode_delivery_map_address(
            order["delivery_address"]
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning(
            "Could not locate delivery map points for order_id=%s: %s",
            order_id,
            exc,
        )
        return jsonify(
            error="The delivery address could not be located inside Laguna. Check the address and try again."
        ), 503
    route_is_approximate = destination_is_approximate
    road_route_unavailable = False
    try:
        geometry, distance_meters, route_duration = get_delivery_route(
            order_id, start, destination
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning(
            "Road route unavailable for order_id=%s; showing approximate direct line: %s",
            order_id,
            exc,
        )
        geometry = [
            [start[1], start[0]],
            [destination[1], destination[0]],
        ]
        distance_meters = great_circle_distance_km(start, destination) * 1000
        route_duration = max(60, int(distance_meters / 1000 / 25 * 3600))
        route_is_approximate = True
        road_route_unavailable = True

    simulation_duration = max(60, route_duration // SIMULATED_DELIVERY_SPEED)
    status = order["order_status"]
    progress = 0.0
    eta_seconds = None
    if status in {"Delivered", "Picked Up"}:
        progress = 1.0
    elif status == "Out for Delivery":
        updated_at = order["updated_at"] or datetime.now()
        elapsed = max(0.0, (datetime.now() - updated_at).total_seconds())
        progress = min(1.0, elapsed / simulation_duration)
        eta_seconds = max(0, int(simulation_duration * (1.0 - progress)))
    return jsonify(
        order_id=order_id,
        order_status=status,
        fulfillment_method="Delivery",
        destination_address=order["delivery_address"],
        shop_name=shop_location["shop_name"],
        shop_address=shop_location["address"],
        shop_location_is_approximate=shop_location["approximate"],
        start={"latitude": start[0], "longitude": start[1]},
        destination={"latitude": destination[0], "longitude": destination[1]},
        route=geometry,
        route_distance_km=round(distance_meters / 1000, 2),
        route_is_approximate=route_is_approximate,
        destination_is_approximate=destination_is_approximate,
        road_route_unavailable=road_route_unavailable,
        progress=progress,
        eta_minutes=(eta_seconds + 59) // 60 if eta_seconds is not None else None,
        simulated_duration_seconds=simulation_duration,
        simulated=True,
    )


@route("/api/custom-orders/<int:custom_id>/delivery-map")
@role_required("customer", "admin")
def custom_delivery_map_data(custom_id):
    ensure_fulfillment_schema()
    ensure_gcash_schema()
    ensure_delivery_map_schema()
    order = query_one(
        """SELECT id,user_id,delivery_address,fulfillment_method,status,updated_at
           FROM custom_orders WHERE id=%s""",
        (custom_id,),
    )
    if not order:
        abort(404)
    if current_user()["role"] == "customer" and order["user_id"] != current_user()["id"]:
        abort(404)
    if order["fulfillment_method"] == "Pickup":
        try:
            shop_location = configured_shop_location()
            shop = shop_location["coordinates"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            current_app.logger.warning(
                "Could not load custom pickup map for custom_id=%s: %s",
                custom_id,
                exc,
            )
            return jsonify(
                error="The shop location could not be loaded. Check the shop address in settings."
            ), 503
        return jsonify(
            order_id=custom_id,
            order_status=order["status"],
            fulfillment_method="Pickup",
            destination_address=shop_location["address"],
            shop_name=shop_location["shop_name"],
            shop_address=shop_location["address"],
            shop_location_is_approximate=shop_location["approximate"],
            start={"latitude": shop[0], "longitude": shop[1]},
            destination={"latitude": shop[0], "longitude": shop[1]},
            route=[],
            route_distance_km=0,
            progress=0,
            eta_minutes=None,
            simulated=False,
        )
    if order["fulfillment_method"] != "Delivery" or not order["delivery_address"]:
        abort(404)
    try:
        shop_location = configured_shop_location()
        start = shop_location["coordinates"]
        destination, destination_is_approximate = geocode_delivery_map_address(
            order["delivery_address"]
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning(
            "Could not locate custom delivery map points for custom_id=%s: %s",
            custom_id,
            exc,
        )
        return jsonify(
            error="The delivery address could not be located inside Laguna. Check the address and try again."
        ), 503
    route_is_approximate = destination_is_approximate
    road_route_unavailable = False
    try:
        geometry, distance_meters, route_duration = get_delivery_route(
            custom_id, start, destination, order_type="Custom"
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning(
            "Road route unavailable for custom_id=%s; showing approximate direct line: %s",
            custom_id,
            exc,
        )
        geometry = [
            [start[1], start[0]],
            [destination[1], destination[0]],
        ]
        distance_meters = great_circle_distance_km(start, destination) * 1000
        route_duration = max(60, int(distance_meters / 1000 / 25 * 3600))
        route_is_approximate = True
        road_route_unavailable = True
    simulation_duration = max(60, route_duration // SIMULATED_DELIVERY_SPEED)
    status = order["status"]
    progress = 0.0
    eta_seconds = None
    if status in {"Delivered", "Picked Up"}:
        progress = 1.0
    elif status == "Out for Delivery":
        updated_at = order["updated_at"] or datetime.now()
        elapsed = max(0.0, (datetime.now() - updated_at).total_seconds())
        progress = min(1.0, elapsed / simulation_duration)
        eta_seconds = max(0, int(simulation_duration * (1.0 - progress)))
    return jsonify(
        order_id=custom_id,
        order_status=status,
        fulfillment_method="Delivery",
        destination_address=order["delivery_address"],
        shop_name=shop_location["shop_name"],
        shop_address=shop_location["address"],
        shop_location_is_approximate=shop_location["approximate"],
        start={"latitude": start[0], "longitude": start[1]},
        destination={"latitude": destination[0], "longitude": destination[1]},
        route=geometry,
        route_distance_km=round(distance_meters / 1000, 2),
        route_is_approximate=route_is_approximate,
        destination_is_approximate=destination_is_approximate,
        road_route_unavailable=road_route_unavailable,
        progress=progress,
        eta_minutes=(eta_seconds + 59) // 60 if eta_seconds is not None else None,
        simulated_duration_seconds=simulation_duration,
        simulated=True,
    )


@route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    ensure_feedback_product_schema()
    if request.method == "POST":
        try:
            rating = int(request.form.get("rating", "0"))
        except ValueError:
            rating = 0
        comment = request.form.get("comment", "").strip()
        try:
            order_id, product_id = (
                int(value) for value in request.form.get("purchased_item", "").split(":", 1)
            )
        except ValueError:
            order_id, product_id = 0, 0
        delivered_item = query_one(
            """SELECT oi.item_name FROM orders o
               JOIN order_items oi ON oi.order_id=o.id
               WHERE o.id=%s AND o.user_id=%s AND o.order_status='Delivered'
                 AND oi.product_id=%s""",
            (order_id, current_user()["id"], product_id),
        ) if order_id and product_id else None
        if rating not in range(1, 6) or len(comment) < 2 or not delivered_item:
            flash("Choose a delivered bouquet you purchased, a rating, and a comment.", "danger")
        else:
            existing = query_one(
                "SELECT id FROM feedback WHERE order_id=%s AND product_id=%s",
                (order_id, product_id),
            )
            if existing:
                flash("You have already reviewed this bouquet from that order.", "warning")
            else:
                try:
                    execute(
                        """INSERT INTO feedback
                           (user_id,order_id,product_id,product_name,rating,comment)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (
                            current_user()["id"], order_id, product_id,
                            delivered_item["item_name"], rating, comment[:2000],
                        ),
                    )
                except mysql.connector.IntegrityError:
                    flash("You have already reviewed this bouquet from that order.", "warning")
                else:
                    notify_admins(
                        "New customer feedback",
                        f"{current_user().get('name') or 'A customer'} reviewed {delivered_item['item_name']}.",
                        url_for("admin_feedback"),
                    )
                    flash("Thank you for reviewing your delivered bouquet!", "success")
                    return redirect(url_for("feedback"))
    delivered_items = query_all(
        """SELECT o.id AS order_id,oi.product_id,oi.item_name,oi.quantity,oi.unit_price,oi.line_total,
                  p.image_filename,p.flower_type,p.color,o.created_at AS order_created_at
           FROM orders o
           JOIN order_items oi ON oi.order_id=o.id
           LEFT JOIN products p ON p.id=oi.product_id
           WHERE o.user_id=%s AND o.order_status='Delivered' AND oi.product_id IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM feedback f
               WHERE f.order_id=o.id AND f.product_id=oi.product_id
             )
           ORDER BY o.created_at DESC,oi.id""",
        (current_user()["id"],),
    )
    my_feedback = query_all(
        """SELECT f.*,COALESCE(oi.item_name,f.product_name) AS item_name,
                  o.id AS order_number,p.image_filename
           FROM feedback f
           LEFT JOIN orders o ON o.id=f.order_id
           LEFT JOIN order_items oi ON oi.order_id=f.order_id AND oi.product_id=f.product_id
           LEFT JOIN products p ON p.id=f.product_id
           WHERE f.user_id=%s ORDER BY f.created_at DESC""",
        (current_user()["id"],),
    )
    preselected_item = request.args.get("item", "").strip()
    if not preselected_item and request.args.get("order_id"):
        target_order_id = str(request.args.get("order_id"))
        for item in delivered_items:
            if str(item["order_id"]) == target_order_id:
                preselected_item = f"{item['order_id']}:{item['product_id']}"
                break
    return render_template(
        "feedback.html",
        delivered_items=delivered_items,
        my_feedback=my_feedback,
        preselected_item=preselected_item,
    )


@route("/notifications")
@login_required
def notifications():
    if current_user().get("role") != "customer":
        abort(403)
    ensure_notification_link_schema()
    rows = query_all("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC",
                     (current_user()["id"],))
    for row in rows:
        if row["link_url"]:
            continue
        title = row["title"].lower()
        message = row["message"]
        if "message from fuzzywire" in title:
            row["link_url"] = url_for("customer_chat")
            continue
        match = re.search(r"(?:order|request)\s+#(\d+)", message, re.IGNORECASE)
        if not match:
            row["link_url"] = url_for("my_orders")
            continue
        notification_id = int(match.group(1))
        if "custom" in title or "custom bouquet" in message.lower():
            custom_order = query_one(
                "SELECT id FROM custom_orders WHERE id=%s AND user_id=%s",
                (notification_id, current_user()["id"]),
            )
            row["link_url"] = (
                url_for("customer_custom_detail", custom_id=notification_id)
                if custom_order else url_for("my_orders")
            )
        else:
            regular_order = query_one(
                "SELECT id FROM orders WHERE id=%s AND user_id=%s",
                (notification_id, current_user()["id"]),
            )
            row["link_url"] = (
                url_for("order_detail", order_id=notification_id)
                if regular_order else url_for("my_orders")
            )
    return render_template("notifications.html", notifications=rows)


@route("/notifications/<int:notification_id>/open")
@role_required("customer")
def open_customer_notification(notification_id):
    ensure_notification_link_schema()
    notification = query_one(
        "SELECT title,message,link_url FROM notifications WHERE id=%s AND user_id=%s",
        (notification_id, current_user()["id"]),
    )
    if not notification:
        abort(404)
    execute(
        "UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s",
        (notification_id, current_user()["id"]),
    )
    target = notification["link_url"]
    if not target:
        title = notification["title"].lower()
        match = re.search(r"(?:order|request)\s+#(\d+)", notification["message"], re.IGNORECASE)
        if "message from fuzzywire" in title:
            target = url_for("customer_chat")
        elif match and ("custom" in title or "custom bouquet" in notification["message"].lower()):
            target = url_for("customer_custom_detail", custom_id=int(match.group(1)))
        elif match:
            target = url_for("order_detail", order_id=int(match.group(1)))
        else:
            target = url_for("my_orders")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = url_for("my_orders")
    return redirect(target)


@route("/notifications/unread-count")
@login_required
def notification_unread_count():
    if current_user().get("role") != "customer":
        abort(403)
    result = query_one(
        "SELECT COUNT(*) AS count FROM notifications WHERE user_id=%s AND is_read=0",
        (current_user()["id"],),
    )
    return jsonify(count=result["count"])


@route("/chat")
@login_required
def customer_chat():
    if current_user().get("role") != "customer":
        abort(403)
    ensure_chat_schema()
    conversation_id = get_or_create_chat_conversation(current_user()["id"])
    return render_template("chat.html", conversation_id=conversation_id)


@route("/chat/api/conversation")
@login_required
def chat_api_conversation():
    if current_user().get("role") != "customer":
        abort(403)
    ensure_chat_schema()
    return jsonify(conversation_id=get_or_create_chat_conversation(current_user()["id"]))


@route("/chat/api/messages", methods=["GET", "POST"])
@role_required("customer", "admin")
def chat_api_messages():
    user = current_user()
    role = user.get("role")
    if role not in {"customer", "admin"}:
        abort(403)
    ensure_chat_schema()
    payload = {}
    if request.method == "POST":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Send a valid chat message."), 400

    if role == "customer":
        conversation_id = get_or_create_chat_conversation(user["id"])
        requested_id = request.args.get("conversation_id") if request.method == "GET" else payload.get("conversation_id")
        if requested_id is not None and str(requested_id) != str(conversation_id):
            return jsonify(error="Conversation not found."), 404
    else:
        raw_id = request.args.get("conversation_id") if request.method == "GET" else payload.get("conversation_id")
        try:
            conversation_id = int(raw_id)
        except (TypeError, ValueError):
            return jsonify(error="A valid conversation ID is required."), 400
        if not query_one("SELECT id FROM chat_conversations WHERE id=%s", (conversation_id,)):
            return jsonify(error="Conversation not found."), 404

    if request.method == "GET":
        rows = query_all(
            """SELECT id,sender_role,body,is_read,created_at
               FROM chat_messages WHERE conversation_id=%s
               ORDER BY id DESC LIMIT 100""",
            (conversation_id,),
        )
        rows.reverse()
        for row in rows:
            created_at_val = row.get("created_at")
            if isinstance(created_at_val, datetime):
                row["created_at"] = created_at_val.strftime("%Y-%m-%dT%H:%M:%S")
            elif isinstance(created_at_val, str):
                row["created_at"] = created_at_val
            else:
                row["created_at"] = ""
        execute(
            """UPDATE chat_messages SET is_read=1
               WHERE conversation_id=%s AND sender_role=%s AND is_read=0""",
            (conversation_id, "admin" if role == "customer" else "customer"),
        )
        return jsonify(messages=rows)

    body = payload.get("body")
    if not isinstance(body, str):
        return jsonify(error="Write a message before sending."), 400
    body = body.strip()
    if not body or len(body) > 2000:
        return jsonify(error="Messages must contain 1 to 2,000 characters."), 400

    execute(
        """INSERT INTO chat_messages (conversation_id,sender_role,body)
           VALUES (%s,%s,%s)""",
        (conversation_id, role, body),
    )
    execute("UPDATE chat_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (conversation_id,))
    if role == "admin":
        conversation = query_one(
            "SELECT user_id FROM chat_conversations WHERE id=%s",
            (conversation_id,),
        )
        notify(conversation["user_id"], "New message from FuzzyWire",
               "The shop replied to your message. Open Chat with us to read it.",
               url_for("customer_chat"))
    return jsonify(ok=True), 201
