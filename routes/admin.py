import csv
import io
import os
import base64
import smtplib
from datetime import datetime, timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation

import mysql.connector
import requests
from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from config import (
    LAGUNA_PROVINCE_CODE,
    ORDER_STAGES,
    ORDER_STATUSES,
    VERIFICATION_UPLOAD_DIR,
)
from database import db, execute, query_all, query_one
from models import Order, Product, User, Voucher
from services.common import (
    build_custom_confirmation_message,
    build_order_confirmation_message,
    current_user,
    geocode_shop_location_address,
    ensure_admin_operations_schema,
    ensure_chat_schema,
    ensure_custom_materials_schema,
    ensure_customer_delivery_confirmation_schema,
    ensure_customer_profile_schema,
    ensure_customer_verification_schema,
    ensure_delivery_distance_schema,
    ensure_delivery_slots_schema,
    ensure_feedback_product_schema,
    ensure_fulfillment_schema,
    ensure_gcash_schema,
    ensure_gcash_settings_schema,
    ensure_manual_order_schema,
    ensure_notification_link_schema,
    ensure_order_messaging_schema,
    ensure_order_queue_schema,
    ensure_promo_schema,
    establish_authenticated_session,
    filter_orders_by_stage,
    log_admin_activity,
    notify,
    queue_email_otp,
    notify_customers_about_voucher,
    normalize_gcash_number,
    parse_voucher_form,
    remove_verification_images,
    review_order_payment,
    role_required,
    remove_gcash_qr,
    save_upload,
    send_order_email,
    update_gcash_name,
    update_gcash_number,
    update_gcash_qr,
    philippine_location_data,
    philippine_barangays_by_city,
    shop_location_details,
    update_shop_location_settings,
    update_admin_order_status,
    trusted_device_exists,
)
from routes.registry import route


@route("/admin/login", methods=["GET", "POST"])
def admin_login():
    ensure_customer_verification_schema()
    if current_user() and current_user().get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    if current_user() and current_user().get("role") == "customer":
        abort(403)
    if request.method == "POST":
        user = User.authenticate(
            request.form.get("email", "").strip(),
            request.form.get("password", ""),
        )
        if user and user["role"] == "admin":
            device_token = request.cookies.get("fuzzywire_device", "")
            if trusted_device_exists(user["id"], device_token):
                return establish_authenticated_session(
                    user, session.get("cart", {})
                )
            try:
                queue_email_otp(user, "new_device")
            except (ValueError, OSError, smtplib.SMTPException, mysql.connector.Error):
                current_app.logger.exception(
                    "Could not send admin new-device OTP to user_id=%s", user["id"]
                )
                flash("We could not send a verification code. Please try again later.", "danger")
            else:
                flash("We sent a code to your Gmail. Verify this new device to continue.", "info")
                return redirect(url_for("verify_otp"))
        flash("Incorrect admin email or password.", "danger")
    return render_template("admin_login.html")


@route("/admin/payments/<order_type>/<int:order_id>/<decision>", methods=["POST"])
@role_required("admin")
def admin_review_payment(order_type, order_id, decision):
    note = request.form.get("review_note", "").strip()
    try:
        result = review_order_payment(
            order_type, order_id, decision, current_user()["id"], note
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        result = False
    if result is None:
        abort(404)
    if result:
        customer_id, new_status = result
        label = f"order #{order_id}" if order_type == "Order" else f"custom bouquet request #C{order_id}"
        title = "GCash payment approved" if new_status == "Paid" else "GCash payment proof rejected"
        message = (
            f"Your GCash payment for {label} was approved."
            if new_status == "Paid"
            else f"Your GCash proof for {label} was rejected. You can upload a new image and try again."
        )
        if note:
            message += f" Shop note: {note}"
        customer_order = query_one(
            "SELECT email FROM users WHERE id=%s", (customer_id,)
        )
        link = (
            url_for("order_detail", order_id=order_id)
            if order_type == "Order"
            else url_for("customer_custom_detail", custom_id=order_id)
        )
        notify(customer_id, title, message, link)
        send_order_email(
            customer_order["email"] if customer_order else None,
            f"FuzzyWire GCash payment {new_status.lower()}",
            message,
        )
        log_admin_activity(
            f"GCash payment {new_status.lower()}",
            order_type,
            order_id,
            note or "No review note",
        )
        flash(f"GCash payment marked {new_status}. Customer notified.", "success")
    destination = (
        url_for("admin_order_detail", order_id=order_id)
        if order_type == "Order"
        else url_for("admin_custom_detail", custom_id=order_id)
    )
    return redirect(destination)


@route("/admin/notifications")
@role_required("admin")
def admin_notifications():
    ensure_notification_link_schema()
    rows = query_all(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC,id DESC",
        (current_user()["id"],),
    )
    for row in rows:
        if row["link_url"]:
            continue
        title = row["title"].lower()
        if "feedback" in title:
            row["link_url"] = url_for("admin_feedback")
        elif "verification" in title or "customer" in title:
            row["link_url"] = url_for("admin_verifications")
        else:
            row["link_url"] = url_for("admin_orders")
    return render_template("admin/notifications.html", notifications=rows)


@route("/admin/notifications/<int:notification_id>/open")
@role_required("admin")
def open_admin_notification(notification_id):
    ensure_notification_link_schema()
    notification = query_one(
        "SELECT title,link_url FROM notifications WHERE id=%s AND user_id=%s",
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
        if "feedback" in title:
            target = url_for("admin_feedback")
        elif "verification" in title:
            target = url_for("admin_verifications")
        else:
            target = url_for("admin_orders")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = url_for("admin_orders")
    return redirect(target)


@route("/admin/notifications/unread-count")
@role_required("admin")
def admin_notification_unread_count():
    ensure_feedback_product_schema()
    ensure_chat_schema()
    ensure_gcash_schema()
    ensure_customer_verification_schema()
    ensure_custom_materials_schema()
    user_id = current_user()["id"]
    counts = query_one(
        """SELECT
             (SELECT COUNT(*) FROM notifications
              WHERE user_id=%s AND is_read=0) AS notifications,
             (SELECT COUNT(*) FROM notifications
              WHERE user_id=%s AND title='New order received' AND is_read=0) AS notif_orders,
             (SELECT (
                (SELECT COUNT(*) FROM orders WHERE order_status='Pending')
                +
                (SELECT COUNT(*) FROM custom_orders WHERE status='Pending')
              )) AS pending_orders,
             (SELECT COUNT(*) FROM feedback WHERE admin_checked_at IS NULL) AS feedback,
             (SELECT COUNT(*) FROM chat_messages WHERE sender_role='customer' AND is_read=0) AS chats,
             (SELECT COUNT(*) FROM users WHERE role='customer' AND verification_status='Pending') AS verifications,
             (SELECT
                (SELECT COUNT(*) FROM orders
                 WHERE payment_method='GCash' AND payment_status='Pending'
                   AND payment_proof_filename IS NOT NULL AND order_status='Pending')
                +
                (SELECT COUNT(*) FROM custom_orders
                 WHERE payment_method='GCash' AND payment_status='Pending'
                   AND payment_proof_filename IS NOT NULL AND status='Pending')
             ) AS payments,
             (SELECT COUNT(*) FROM products WHERE is_active=1 AND stock <= 5) AS low_stock_products,
             (SELECT COUNT(*) FROM custom_materials WHERE is_active=1 AND stock <= low_stock_threshold) AS low_stock_materials""",
        (user_id, user_id),
    )
    orders = max((counts["pending_orders"] or 0), (counts["notif_orders"] or 0)) if counts else 0
    notifications = (counts["notifications"] or 0) if counts else 0
    feedback = (counts["feedback"] or 0) if counts else 0
    chats = (counts["chats"] or 0) if counts else 0
    verifications = (counts["verifications"] or 0) if counts else 0
    payments = (counts["payments"] or 0) if counts else 0
    low_stock_products = (counts["low_stock_products"] or 0) if counts else 0
    low_stock_materials = (counts["low_stock_materials"] or 0) if counts else 0
    total = (
        notifications
        + orders
        + feedback
        + chats
        + verifications
        + payments
        + low_stock_products
        + low_stock_materials
    )
    return jsonify(
        notifications=notifications,
        orders=orders,
        feedback=feedback,
        chats=chats,
        verifications=verifications,
        payments=payments,
        low_stock_products=low_stock_products,
        low_stock_materials=low_stock_materials,
        total=total,
    )


@route("/admin/payments")
@role_required("admin")
def admin_payments():
    ensure_gcash_schema()
    pending_count = query_one(
        """SELECT
             (SELECT COUNT(*) FROM orders
              WHERE payment_method='GCash' AND payment_status='Pending'
                AND payment_proof_filename IS NOT NULL AND order_status='Pending')
             +
             (SELECT COUNT(*) FROM custom_orders
              WHERE payment_method='GCash' AND payment_status='Pending'
                AND payment_proof_filename IS NOT NULL AND status='Pending')
             AS count"""
    )["count"]
    payments = query_all(
        """SELECT * FROM (
             SELECT 'Order' AS order_type,o.id AS order_id,o.customer_name,
                    u.email AS customer_email,o.total AS amount,
                    o.payment_status,o.payment_proof_filename,
                    o.payment_proof_uploaded_at AS payment_date,
                    o.payment_review_note
             FROM orders o JOIN users u ON u.id=o.user_id
             WHERE o.payment_method='GCash' AND o.payment_status='Pending'
               AND o.payment_proof_filename IS NOT NULL AND o.order_status='Pending'
             UNION ALL
             SELECT 'Custom' AS order_type,c.id AS order_id,u.full_name,
                    u.email AS customer_email,c.total_price AS amount,
                    c.payment_status,c.payment_proof_filename,
                    c.payment_proof_uploaded_at AS payment_date,
                    c.payment_review_note
             FROM custom_orders c JOIN users u ON u.id=c.user_id
             WHERE c.payment_method='GCash' AND c.payment_status='Pending'
               AND c.payment_proof_filename IS NOT NULL AND c.status='Pending'
           ) AS gcash_payments
           ORDER BY payment_date DESC,order_id DESC
           LIMIT 250"""
    )
    return render_template(
        "admin/payments.html",
        payments=payments,
        pending_count=pending_count,
    )


@route("/admin/settings/gcash", methods=["GET", "POST"])
@role_required("admin")
def admin_gcash_settings():
    ensure_gcash_settings_schema()
    if request.method == "POST":
        qr_filename = None
        try:
            normalize_gcash_number(request.form.get("gcash_number", ""))
            if "gcash_name" in request.form:
                gcash_name = update_gcash_name(
                    request.form.get("gcash_name", ""),
                    current_user()["id"],
                )
            else:
                name_setting = query_one(
                    "SELECT setting_value FROM app_settings WHERE setting_key='gcash_name'"
                )
                gcash_name = (
                    name_setting["setting_value"].strip()
                    if name_setting and name_setting.get("setting_value") and name_setting["setting_value"].strip()
                    else os.getenv("GCASH_ACCOUNT_NAME", "FuzzyWire Bouquet").strip()
                )
            qr_upload = request.files.get("gcash_qr")
            qr_filename = (
                update_gcash_qr(qr_upload, current_user()["id"])
                if qr_upload and qr_upload.filename else None
            )
            number = update_gcash_number(
                request.form.get("gcash_number", ""),
                current_user()["id"],
            )
        except ValueError as exc:
            remove_gcash_qr(qr_filename)
            flash(str(exc), "danger")
            qr_setting = query_one(
                "SELECT setting_value FROM app_settings WHERE setting_key='gcash_qr_filename'"
            )
            name_setting = query_one(
                "SELECT setting_value FROM app_settings WHERE setting_key='gcash_name'"
            )
            return render_template(
                "admin/gcash_settings.html",
                gcash_name=request.form.get("gcash_name", "") or (name_setting["setting_value"] if name_setting else ""),
                gcash_number=request.form.get("gcash_number", ""),
                qr_filename=qr_setting["setting_value"] if qr_setting else None,
            ), 400
        log_admin_activity(
            "Updated GCash payment settings",
            "Payment settings",
            current_user()["id"],
            f"GCash account name '{gcash_name}', number changed to {number[:4]}******{number[-2:]}"
            + ("; QR code replaced" if qr_filename else ""),
        )
        if qr_filename:
            flash("GCash number and QR code updated successfully.", "success")
        else:
            flash("GCash number updated successfully.", "success")
        return redirect(url_for("admin_gcash_settings"))
    gcash_number = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_number'"
    )["setting_value"]
    qr_setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_qr_filename'"
    )
    name_setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_name'"
    )
    gcash_name = (
        name_setting["setting_value"].strip()
        if name_setting and name_setting.get("setting_value") and name_setting["setting_value"].strip()
        else os.getenv("GCASH_ACCOUNT_NAME", "FuzzyWire Bouquet").strip()
    )
    return render_template(
        "admin/gcash_settings.html",
        gcash_name=gcash_name,
        gcash_number=gcash_number,
        qr_filename=qr_setting["setting_value"] if qr_setting else None,
    )


@route("/admin/settings/shop-location", methods=["GET", "POST"])
@role_required("admin")
def admin_shop_location():
    ensure_gcash_settings_schema()
    location = shop_location_details()
    cities = sorted(
        (
            city for city in philippine_location_data()["cities"].values()
            if city.get("provinceCode") == LAGUNA_PROVINCE_CODE
        ),
        key=lambda city: city["name"].casefold(),
    )
    barangays = philippine_barangays_by_city().get(
        location["municipality_code"], []
    )
    if request.method == "POST":
        location.update(
            province_code=request.form.get("province_code", "").strip(),
            municipality_code=request.form.get("municipality_code", "").strip(),
            barangay=request.form.get("barangay", "").strip(),
            shop_name=request.form.get("shop_name", "").strip(),
            complete_address=request.form.get("complete_address", "").strip(),
        )
        try:
            location = update_shop_location_settings(
                location["province_code"],
                location["municipality_code"],
                location["barangay"],
                location["shop_name"],
                location["complete_address"],
                current_user()["id"],
                request.form.get("map_latitude", "").strip(),
                request.form.get("map_longitude", "").strip(),
                request.form.get("map_approximate", "").strip() == "true",
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            selected_city = location["municipality_code"]
            if selected_city != request.form.get("municipality_code", "").strip():
                selected_city = request.form.get("municipality_code", "").strip()
                barangays = philippine_barangays_by_city().get(selected_city, [])
            return render_template(
                "admin/shop_location.html",
                shop_location=location,
                cities=cities,
                barangays=barangays,
            ), 400

        log_admin_activity(
            "Updated shop address",
            "Shop settings",
            current_user()["id"],
            f"{location['shop_name']}, {location['address']}",
        )
        flash("Shop location saved. Customer maps and pickup details are updated.", "success")
        return redirect(url_for("admin_shop_location"))

    return render_template(
        "admin/shop_location.html",
        shop_location=location,
        cities=cities,
        barangays=barangays,
    )


@route("/admin/settings/shop-location/preview")
@role_required("admin")
def admin_shop_location_preview():
    try:
        point, approximate = geocode_shop_location_address(
            request.args.get("province_code", ""),
            request.args.get("municipality_code", ""),
            request.args.get("barangay", ""),
            request.args.get("complete_address", ""),
        )
    except (ValueError, KeyError) as exc:
        return jsonify(error=str(exc)), 400
    except (requests.RequestException, TypeError) as exc:
        current_app.logger.exception("Could not geocode shop location preview.")
        return jsonify(
            error="Could not load the address on the map. Check your connection and try again."
        ), 503
    return jsonify(latitude=point[0], longitude=point[1], approximate=approximate)


@route("/admin/chats")
@role_required("admin")
def admin_chats():
    ensure_chat_schema()
    ensure_customer_profile_schema()
    conversations = query_all(
        """SELECT c.id,c.updated_at,u.full_name,u.email,u.profile_image,
             (SELECT m.body FROM chat_messages m
              WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message,
             (SELECT COUNT(*) FROM chat_messages unread
              WHERE unread.conversation_id=c.id
                AND unread.sender_role='customer' AND unread.is_read=0) AS unread_count
           FROM chat_conversations c
           JOIN users u ON u.id=c.user_id
           ORDER BY c.updated_at DESC"""
    )
    return render_template("admin/chats.html", conversations=conversations)


@route("/admin/chats/<int:conversation_id>")
@role_required("admin")
def admin_chat_detail(conversation_id):
    ensure_chat_schema()
    ensure_customer_profile_schema()
    conversation = query_one(
        """SELECT c.id,c.updated_at,u.full_name,u.email,u.profile_image
           FROM chat_conversations c JOIN users u ON u.id=c.user_id
           WHERE c.id=%s""",
        (conversation_id,),
    )
    if not conversation:
        abort(404)
    return render_template("admin/chat_detail.html", conversation=conversation)


@route("/admin")
@role_required("admin")
def admin_dashboard():
    ensure_manual_order_schema()
    ensure_gcash_settings_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_custom_materials_schema()
    ensure_admin_operations_schema()
    ensure_customer_verification_schema()
    ensure_notification_link_schema()
    ensure_order_queue_schema()
    gcash_setting = query_one(
        "SELECT setting_value FROM app_settings WHERE setting_key='gcash_number'"
    )
    gcash_number = gcash_setting["setting_value"] if gcash_setting else ""
    pending_verifications = query_all(
        """SELECT id,full_name,email,created_at,
                  verification_id_front_filename,verification_id_back_filename,
                  verification_selfie_filename
           FROM users
           WHERE role='customer' AND verification_status='Pending'
           ORDER BY created_at ASC,id ASC"""
    )
    admin_notifications = query_all(
        """SELECT title,message,created_at FROM notifications
           WHERE user_id=%s AND is_read=0
           ORDER BY created_at DESC,id DESC LIMIT 10""",
        (current_user()["id"],),
    )
    newest_pending_order = query_one(
           """SELECT order_type,id,created_at,full_name,total,order_status,
                     payment_method,payment_status
              FROM (
                SELECT 'Bouquet' AS order_type,o.id,o.created_at,
                       o.customer_name AS full_name,
                       o.total,o.order_status,o.payment_method,o.payment_status,
                       q.queue_id
                FROM orders o
                JOIN order_queue q ON q.order_type='Order' AND q.order_id=o.id
                WHERE o.order_status='Pending'
                UNION ALL
                SELECT 'Custom bouquet',c.id,c.created_at,u.full_name,
                       c.total_price,c.status,c.payment_method,c.payment_status,
                       q.queue_id
                FROM custom_orders c
                JOIN users u ON u.id=c.user_id
                JOIN order_queue q ON q.order_type='Custom' AND q.order_id=c.id
                WHERE c.status='Pending'
              ) AS pending_orders
              ORDER BY created_at DESC,queue_id DESC
              LIMIT 1"""
    )
    regular = query_one("""SELECT COUNT(*) AS orders,
        COALESCE(SUM(order_status NOT IN ('Delivered','Picked Up','Cancelled')),0) AS pending,
        COALESCE(SUM(CASE WHEN order_status <> 'Cancelled'
          AND payment_status='Paid'
          THEN GREATEST(total-COALESCE((SELECT SUM(r.amount) FROM order_refunds r
            WHERE r.order_id=orders.id AND r.status='Completed'),0),0) ELSE 0 END),0) AS sales
        FROM orders""")
    custom = query_one("""SELECT COUNT(*) AS orders,
        COALESCE(SUM(status NOT IN ('Delivered','Picked Up','Cancelled')),0) AS pending,
        COALESCE(SUM(CASE WHEN status <> 'Cancelled' AND payment_status='Paid'
          THEN total_price ELSE 0 END),0) AS sales
        FROM custom_orders""")
    stats = {
        "sales": regular["sales"] + custom["sales"],
        "orders": regular["orders"] + custom["orders"],
        "customers": query_one("SELECT COUNT(*) AS value FROM users WHERE role='customer'")["value"],
        "pending_verifications": len(pending_verifications),
        "pending": regular["pending"] + custom["pending"],
        "low_stock": (
            query_one("SELECT COUNT(*) AS value FROM products WHERE stock <= 5 AND is_active=1")["value"]
            + query_one(
                """SELECT COUNT(*) AS value FROM custom_materials
                   WHERE stock <= low_stock_threshold AND is_active=1"""
            )["value"]
        ),
    }
    daily = query_all("""SELECT day, SUM(total) AS total FROM (
                          SELECT DATE(o.created_at) AS day,
                            SUM(CASE WHEN o.order_status <> 'Cancelled'
                              AND o.payment_status='Paid'
                              THEN GREATEST(o.total-COALESCE((SELECT SUM(r.amount) FROM order_refunds r
                                WHERE r.order_id=o.id AND r.status='Completed'),0),0) ELSE 0 END) AS total
                          FROM orders o
                          WHERE o.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
                          GROUP BY DATE(o.created_at)
                          UNION ALL
                          SELECT DATE(c.created_at) AS day,
                            SUM(CASE WHEN c.status <> 'Cancelled' AND c.payment_status='Paid'
                              THEN c.total_price ELSE 0 END) AS total
                          FROM custom_orders c
                          WHERE c.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
                          GROUP BY DATE(c.created_at)
                        ) AS recent_sales GROUP BY day ORDER BY day""")
    daily_by_date = {row["day"]: float(row["total"] or 0) for row in daily}
    today = datetime.now().date()
    daily = [{"day": today - timedelta(days=offset),
              "total": daily_by_date.get(today - timedelta(days=offset), 0)}
             for offset in range(6, -1, -1)]
    best = query_all("""SELECT oi.item_name, SUM(oi.quantity) AS quantity
                        FROM order_items oi JOIN orders o ON o.id=oi.order_id
                        WHERE o.order_status IN ('Delivered','Picked Up')
                        GROUP BY oi.item_name ORDER BY quantity DESC LIMIT 5""")
    low_stock_products = query_all(
        """SELECT id,name,stock FROM products
           WHERE stock <= 5 AND is_active=1 ORDER BY stock ASC,name ASC"""
    )
    low_stock_materials = query_all(
        """SELECT id,name,category,stock,unit FROM custom_materials
           WHERE stock <= low_stock_threshold AND is_active=1
           ORDER BY stock ASC,name ASC"""
    )
    return render_template("admin/dashboard.html", stats=stats, daily=daily, best=best,
                           low_stock_products=low_stock_products, low_stock_materials=low_stock_materials,
                           pending_verifications=pending_verifications[:10],
                           admin_notifications=admin_notifications,
                           newest_pending_order=newest_pending_order,
                           gcash_number=gcash_number)


@route("/admin/verifications")
@role_required("admin")
def admin_verifications():
    ensure_customer_verification_schema()
    ensure_notification_link_schema()
    execute(
        """UPDATE notifications SET is_read=1
           WHERE user_id=%s AND is_read=0
             AND title='New customer verification request'""",
        (current_user()["id"],),
    )
    verifications = query_all(
        """SELECT id,full_name,email,created_at,
                  verification_id_front_filename,verification_id_back_filename,
                  verification_selfie_filename
           FROM users
           WHERE role='customer' AND verification_status='Pending'
           ORDER BY created_at ASC,id ASC"""
    )
    return render_template("admin/verifications.html", verifications=verifications)


@route("/admin/verifications/<int:customer_id>/image/<image_kind>")
@role_required("admin")
def admin_verification_image(customer_id, image_kind):
    image_columns = {
        "front": "verification_id_front_filename",
        "back": "verification_id_back_filename",
        "selfie": "verification_selfie_filename",
    }
    column = image_columns.get(image_kind)
    if not column:
        abort(404)
    ensure_customer_verification_schema()
    customer = query_one(
        f"""SELECT `{column}` AS filename FROM users
            WHERE id=%s AND role='customer' AND verification_status='Pending'""",
        (customer_id,),
    )
    if not customer or not customer["filename"]:
        abort(404)
    filename = Path(customer["filename"]).name
    path = (VERIFICATION_UPLOAD_DIR / filename).resolve()
    if path.parent != VERIFICATION_UPLOAD_DIR.resolve() or not path.is_file():
        abort(404)
    return send_file(path, conditional=True, max_age=0)


@route("/admin/verifications/<int:customer_id>/<decision>", methods=["POST"])
@role_required("admin")
def admin_verification_decision(customer_id, decision):
    if decision not in {"approve", "reject"}:
        abort(404)
    rejection_reason = request.form.get("rejection_reason", "").strip()
    if decision == "reject" and not rejection_reason:
        flash("Enter a reason so the customer knows how to correct their ID submission.", "warning")
        return redirect(url_for("admin_verifications"))
    if len(rejection_reason) > 1000:
        flash("The rejection reason must be 1,000 characters or fewer.", "warning")
        return redirect(url_for("admin_verifications"))
    ensure_customer_verification_schema()
    ensure_notification_link_schema()
    new_status = "Approved" if decision == "approve" else "Rejected"
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id FROM users
               WHERE role='customer' AND verification_status='Pending'
               ORDER BY created_at ASC,id ASC LIMIT 1 FOR UPDATE"""
        )
        verification_head = cur.fetchone()
        if verification_head and verification_head["id"] != customer_id:
            conn.rollback()
            flash(
                "The selected request cannot be reviewed yet.",
                "warning",
            )
            return redirect(url_for("admin_verifications"))
        cur.execute(
            """SELECT id,full_name,email,email_verified,verification_status,
                      verification_id_front_filename,verification_id_back_filename,
                      verification_selfie_filename
               FROM users
               WHERE id=%s AND role='customer' FOR UPDATE""",
            (customer_id,),
        )
        customer = cur.fetchone()
        if not customer or customer["verification_status"] != "Pending":
            conn.rollback()
            flash("This verification request is no longer pending.", "info")
            return redirect(url_for("admin_verifications"))
        cur.execute(
            """UPDATE users SET verification_status=%s,
                      account_status=CASE
                       WHEN account_status='Deactivated' THEN 'Deactivated'
                       WHEN %s='Rejected' THEN 'Rejected'
                       WHEN email_verified=1 THEN 'Active'
                        ELSE 'Pending' END,
                      verification_rejection_reason=%s,
                      verification_id_front_filename=NULL,
                      verification_id_back_filename=NULL,
                      verification_selfie_filename=NULL
               WHERE id=%s AND verification_status='Pending'""",
            (
                new_status,
                new_status,
                rejection_reason if decision == "reject" else None,
                customer_id,
            ),
        )
        verification_filenames = (
            customer["verification_id_front_filename"],
            customer["verification_id_back_filename"],
            customer["verification_selfie_filename"],
        )
        title = "Account approved" if decision == "approve" else "Account verification rejected"
        message = (
            "Your account has been approved. You can now log in and place orders."
            if decision == "approve"
            else (
                "Your ID verification was rejected. Reason: "
                f"{rejection_reason} "
                "You can sign in to review the reason and resubmit your ID."
            )
        )
        cur.execute(
            """INSERT INTO notifications (user_id,title,message,link_url)
               VALUES (%s,%s,%s,%s)""",
            (
                customer_id,
                title,
                message,
                url_for("notifications") if decision == "approve" else url_for("login"),
            ),
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        current_app.logger.exception("Could not update customer verification status.")
        raise
    finally:
        conn.close()
    ensure_admin_operations_schema()
    try:
        remove_verification_images(*verification_filenames)
    except OSError:
        current_app.logger.exception(
            "Could not remove reviewed identity images for customer_id=%s", customer_id
        )
    log_admin_activity(
        f"Customer account {new_status.lower()}",
        "Customer",
        customer_id,
        f"{customer['full_name']} ({customer['verification_status']} → {new_status})",
    )
    email_sent = True
    if decision == "reject":
        email_sent = send_order_email(
            customer["email"],
            "FuzzyWire Bouquet ID verification update",
            f"Hi {customer['full_name']},\n\n"
            "Your ID verification was rejected for this reason:\n"
            f"{rejection_reason}\n\n"
            "Sign in to your account to review the reason and resubmit your ID photos.",
        )
    if email_sent:
        flash(
            f"{customer['full_name']}'s account was {new_status.lower()} and the customer was notified.",
            "success",
        )
    else:
        flash(
            f"{customer['full_name']}'s account was {new_status.lower()}, but the email could not be sent. Check the email configuration.",
            "warning",
        )
    return redirect(url_for("admin_verifications"))


@route("/admin/products")
@role_required("admin")
def admin_products():
    return render_template("admin/products.html", products=Product.all(active_only=False))


@route("/admin/materials", methods=["GET", "POST"])
@role_required("admin")
def admin_materials():
    ensure_custom_materials_schema()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            custom_category = request.form.get("custom_category", "").strip()
            raw_category = request.form.get("category", "").strip()
            category = custom_category if (raw_category == "__other__" or (custom_category and not raw_category)) else (custom_category or raw_category)
            unit = request.form.get("unit", "").strip()
            price = Decimal(request.form.get("unit_price", ""))
            stock = Decimal(request.form.get("stock", "-1"))
            threshold = Decimal(request.form.get("low_stock_threshold", "-1"))
            if (not name or len(name) > 120 or not category or len(category) > 60
                    or not unit or len(unit) > 40 or not price.is_finite()
                    or not stock.is_finite() or not threshold.is_finite()
                    or price < 0 or stock < 0 or threshold < 0):
                raise ValueError("Enter valid material details.")
            execute(
                """INSERT INTO custom_materials
                   (name,category,unit,unit_price,stock,low_stock_threshold,is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,1)""",
                (name, category, unit, price, stock, threshold),
            )
            log_admin_activity("Added custom material", "Custom material", "new", name)
            flash("Material added.", "success")
            return redirect(url_for("admin_materials"))
        except (ValueError, InvalidOperation) as exc:
            flash(str(exc) or "Enter valid material details.", "danger")
        except mysql.connector.IntegrityError:
            flash("A material with that name and category already exists.", "danger")
    materials = query_all(
        """SELECT id,name,category,unit,unit_price,stock,low_stock_threshold,is_active
           FROM custom_materials ORDER BY category,name"""
    )
    categories = sorted(list({m["category"] for m in materials if m.get("category")} | {"Flower", "Wrapping", "Extra"}))
    return render_template("admin/materials.html", materials=materials, categories=categories)


@route("/admin/materials/<int:material_id>/edit", methods=["POST"])
@role_required("admin")
def admin_material_edit(material_id):
    ensure_custom_materials_schema()
    try:
        name = request.form.get("name", "").strip()
        custom_category = request.form.get("custom_category", "").strip()
        raw_category = request.form.get("category", "").strip()
        category = custom_category if (raw_category == "__other__" or (custom_category and not raw_category)) else (custom_category or raw_category)
        unit = request.form.get("unit", "").strip()
        price = Decimal(request.form.get("unit_price", ""))
        stock = Decimal(request.form.get("stock", "-1"))
        threshold = Decimal(request.form.get("low_stock_threshold", "-1"))
        active = 1 if request.form.get("is_active") == "1" else 0
        if (not name or len(name) > 120 or not category or len(category) > 60
                or not unit or len(unit) > 40 or not price.is_finite()
                or not stock.is_finite() or not threshold.is_finite()
                or price < 0 or stock < 0 or threshold < 0):
            raise ValueError("Enter valid material details.")
        execute(
            """UPDATE custom_materials SET name=%s,category=%s,unit=%s,unit_price=%s,
               stock=%s,low_stock_threshold=%s,is_active=%s WHERE id=%s""",
            (name, category, unit, price, stock, threshold, active, material_id),
        )
        log_admin_activity("Updated custom material", "Custom material", material_id, name)
        flash("Material updated.", "success")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc) or "Enter valid material details.", "danger")
    except mysql.connector.IntegrityError:
        flash("A material with that name and category already exists.", "danger")
    return redirect(url_for("admin_materials"))


@route("/admin/vouchers", methods=["GET", "POST"])
@role_required("admin")
def admin_vouchers():
    ensure_promo_schema()
    if request.method == "POST":
        try:
            name, code, discount_type, value, minimum, max_claims, expires_at = parse_voucher_form(request.form)
            execute(
                """INSERT INTO promo_vouchers
                   (name,code,discount_type,discount_value,minimum_subtotal,
                    max_claims,max_uses,expires_at,is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                (name, code, discount_type, value, minimum, max_claims, max_claims, expires_at),
            )
            log_admin_activity("Created promo code", "Voucher", code)
            notify_customers_about_voucher(
                name, code, discount_type, value, expires_at, url_for("customer_vouchers")
            )
            flash("Voucher created and announced to customers.", "success")
            return redirect(url_for("admin_vouchers"))
        except (ValueError, InvalidOperation) as exc:
            flash(str(exc) or "Enter valid promo code details.", "danger")
        except mysql.connector.IntegrityError:
            flash("That promo code already exists.", "danger")
    vouchers = query_all(
        """SELECT v.*,
                  (SELECT COUNT(*) FROM voucher_claims c WHERE c.voucher_id=v.id) AS claimed_count,
                  GREATEST(v.used_count,
                    (SELECT COUNT(*) FROM voucher_redemptions r WHERE r.voucher_id=v.id)
                  ) AS redeemed_count
           FROM promo_vouchers v ORDER BY v.created_at DESC"""
    )
    return render_template("admin/vouchers.html", vouchers=vouchers)


@route("/admin/vouchers/<int:voucher_id>/edit", methods=["POST"])
@role_required("admin")
def admin_voucher_edit(voucher_id):
    ensure_promo_schema()
    try:
        name, code, discount_type, value, minimum, max_claims, expires_at = parse_voucher_form(request.form)
        active = 1 if request.form.get("is_active") == "1" else 0
        was_active = Voucher.update(
            voucher_id,
            {
                "name": name,
                "code": code,
                "discount_type": discount_type,
                "discount_value": value,
                "minimum_subtotal": minimum,
                "max_claims": max_claims,
                "expires_at": expires_at,
                "is_active": active,
            },
        )
        if was_active is None:
            abort(404)
        log_admin_activity("Updated promo code", "Voucher", code)
        if not was_active and active:
            notify_customers_about_voucher(
                name, code, discount_type, value, expires_at, url_for("customer_vouchers")
            )
        flash("Voucher updated.", "success")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc) or "Enter valid promo code details.", "danger")
    except mysql.connector.IntegrityError:
        flash("That promo code already exists.", "danger")
    return redirect(url_for("admin_vouchers"))


@route("/admin/vouchers/<int:voucher_id>/delete", methods=["POST"])
@role_required("admin")
def admin_voucher_delete(voucher_id):
    ensure_promo_schema()
    try:
        code = Voucher.delete_if_unused(voucher_id)
        if code is None:
            abort(404)
        log_admin_activity("Deleted voucher", "Voucher", code)
        flash("Voucher deleted.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("admin_vouchers"))


@route("/admin/delivery-slots", methods=["GET", "POST"])
@role_required("admin")
def admin_delivery_slots():
    ensure_gcash_schema()
    ensure_delivery_slots_schema()
    if request.method == "POST":
        try:
            starts_at = datetime.strptime(request.form.get("starts_at", ""), "%Y-%m-%dT%H:%M")
            fulfillment_method = request.form.get("fulfillment_method", "")
            capacity = int(request.form.get("capacity", "0"))
            if starts_at <= datetime.now() or fulfillment_method not in {"Delivery", "Pickup"} or not 1 <= capacity <= 1000:
                raise ValueError("Choose a future time, valid fulfillment method, and capacity from 1 to 1,000.")
            execute(
                "INSERT INTO delivery_slots (starts_at,fulfillment_method,capacity) VALUES (%s,%s,%s)",
                (starts_at, fulfillment_method, capacity),
            )
            log_admin_activity("Created fulfillment slot", "Delivery slot", "new",
                               f"{fulfillment_method} {starts_at:%Y-%m-%d %H:%M}, capacity {capacity}")
            flash("Delivery or pickup slot created.", "success")
            return redirect(url_for("admin_delivery_slots"))
        except ValueError as exc:
            flash(str(exc) or "Enter a valid slot date and time.", "danger")
        except mysql.connector.IntegrityError:
            flash("A slot already exists for this time and fulfillment method.", "danger")
    slots = query_all(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM orders o
                   WHERE o.delivery_slot_id=s.id AND o.order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                  +
                  (SELECT COUNT(*) FROM custom_orders c
                   WHERE c.delivery_slot_id=s.id AND c.status NOT IN ('Cancelled','Delivered','Picked Up'))
                  AS reserved
           FROM delivery_slots s
           ORDER BY s.starts_at DESC"""
    )
    return render_template("admin/delivery_slots.html", slots=slots)


@route("/admin/delivery-slots/<int:slot_id>/edit", methods=["POST"])
@role_required("admin")
def admin_delivery_slot_edit(slot_id):
    ensure_gcash_schema()
    ensure_delivery_slots_schema()
    try:
        starts_at = datetime.strptime(request.form.get("starts_at", ""), "%Y-%m-%dT%H:%M")
        fulfillment_method = request.form.get("fulfillment_method", "")
        capacity = int(request.form.get("capacity", "0"))
        active = 1 if request.form.get("is_active") == "1" else 0
        if ((active and starts_at <= datetime.now())
                or fulfillment_method not in {"Delivery", "Pickup"} or not 1 <= capacity <= 1000):
            raise ValueError("Choose a future time, valid fulfillment method, and capacity from 1 to 1,000.")
    except ValueError as exc:
        flash(str(exc) or "Enter a valid slot date and time.", "danger")
        return redirect(url_for("admin_delivery_slots"))
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM delivery_slots WHERE id=%s FOR UPDATE", (slot_id,))
        slot = cur.fetchone()
        if not slot:
            abort(404)
        cur.execute(
            """SELECT
                 (SELECT COUNT(*) FROM orders
                  WHERE delivery_slot_id=%s AND order_status NOT IN ('Cancelled','Delivered','Picked Up'))
                 +
                 (SELECT COUNT(*) FROM custom_orders
                  WHERE delivery_slot_id=%s AND status NOT IN ('Cancelled','Delivered','Picked Up'))
                 AS count""",
            (slot_id, slot_id),
        )
        reserved_count = cur.fetchone()["count"]
        cur.execute(
            """SELECT
                 (SELECT COUNT(*) FROM orders WHERE delivery_slot_id=%s)
                 +
                 (SELECT COUNT(*) FROM custom_orders WHERE delivery_slot_id=%s)
                 AS count""",
            (slot_id, slot_id),
        )
        linked_count = cur.fetchone()["count"]
        if capacity < reserved_count:
            raise ValueError("Capacity cannot be less than the number of active orders in this slot.")
        if linked_count and (
            starts_at != slot["starts_at"]
            or fulfillment_method != slot["fulfillment_method"]
        ):
            raise ValueError("A time slot with existing orders cannot change its time or fulfillment method.")
        cur.execute(
            """UPDATE delivery_slots SET starts_at=%s,fulfillment_method=%s,
               capacity=%s,is_active=%s WHERE id=%s""",
            (starts_at, fulfillment_method, capacity, active, slot_id),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("admin_delivery_slots"))
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("A slot already exists for this time and fulfillment method.", "danger")
        return redirect(url_for("admin_delivery_slots"))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log_admin_activity("Updated fulfillment slot", "Delivery slot", slot_id,
                       f"{fulfillment_method} {starts_at:%Y-%m-%d %H:%M}, capacity {capacity}, active={active}")
    flash("Delivery or pickup slot updated.", "success")
    return redirect(url_for("admin_delivery_slots"))


@route("/admin/products/add", methods=["GET", "POST"])
@role_required("admin")
def admin_product_add():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            flower_type = request.form.get("flower_type", "").strip()
            color = request.form.get("color", "").strip()
            occasion = request.form.get("occasion", "Any occasion").strip()
            price = Decimal(request.form.get("price", "0"))
            stock = int(request.form.get("stock", "0"))
            if (not name or not flower_type or not color or not occasion or len(occasion) > 60 or not price.is_finite()
                    or price <= 0 or stock < 0):
                raise ValueError("Enter valid product details.")
            image_name = save_upload(request.files.get("image"))
            Product.create({"name": name, "description": request.form.get("description", "").strip(),
                            "flower_type": flower_type, "color": color, "occasion": occasion, "price": price,
                            "stock": stock, "image_filename": image_name})
            log_admin_activity("Added product", "Product", "new", name)
            flash("Product added.", "success")
            return redirect(url_for("admin_products"))
        except (ValueError, InvalidOperation, base64.binascii.Error) as exc:
            flash(str(exc) or "Enter valid product details.", "danger")
    return render_template("admin/product_form.html")


@route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def admin_product_edit(product_id):
    product = Product.get(product_id)
    if not product:
        abort(404)
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            flower_type = request.form.get("flower_type", "").strip()
            color = request.form.get("color", "").strip()
            occasion = request.form.get("occasion", "Any occasion").strip()
            price = Decimal(request.form.get("price", "0"))
            stock = int(request.form.get("stock", "-1"))
            if (not name or not flower_type or not color or not occasion or len(occasion) > 60 or not price.is_finite()
                    or price <= 0 or stock < 0):
                raise ValueError("Enter valid product details.")
            image_name = save_upload(request.files.get("image")) or product["image_filename"]
            Product.update(product_id, {
                "name": name,
                "description": request.form.get("description", "").strip(),
                "flower_type": flower_type,
                "color": color,
                "occasion": occasion,
                "price": price,
                "stock": stock,
                "image_filename": image_name,
                "is_active": 1 if request.form.get("is_active") == "1" else 0,
            })
            log_admin_activity("Updated product", "Product", product_id, name)
            flash("Product updated.", "success")
            return redirect(url_for("admin_products"))
        except (ValueError, InvalidOperation, base64.binascii.Error) as exc:
            flash(str(exc) or "Enter valid product details.", "danger")
    return render_template("admin/product_form.html", product=product)


@route("/admin/products/<int:product_id>/stock", methods=["POST"])
@role_required("admin")
def admin_stock(product_id):
    product = Product.get(product_id)
    if not product:
        abort(404)
    try:
        stock = int(request.form.get("stock", "-1"))
        if stock < 0:
            raise ValueError
        execute("UPDATE products SET stock=%s WHERE id=%s", (stock, product_id))
        log_admin_activity("Updated product stock", "Product", product_id, f"Stock: {stock}")
        flash("Stock updated.", "success")
    except ValueError:
        flash("Stock must be zero or higher.", "danger")
    return redirect(url_for("admin_products"))


@route("/admin/orders")
@role_required("admin")
def admin_orders():
    ensure_manual_order_schema()
    ensure_gcash_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_order_messaging_schema()
    ensure_fulfillment_schema()
    ensure_order_queue_schema()
    execute(
        """UPDATE notifications SET is_read=1
           WHERE user_id=%s AND title='New order received' AND is_read=0""",
        (current_user()["id"],),
    )
    orders = query_all(
        """SELECT * FROM (
             SELECT 'Bouquet' AS order_type,o.id,o.created_at,
                    o.customer_name AS full_name,
                    o.customer_email,o.total,o.payment_method,o.payment_status,
                    o.order_status,o.cancellation_reason,o.fulfillment_method,
                    o.payment_proof_filename,q.queue_id
             FROM orders o
             JOIN order_queue q ON q.order_type='Order' AND q.order_id=o.id
             UNION ALL
             SELECT 'Custom bouquet',c.id,c.created_at,u.full_name,u.email,
                    c.total_price,c.payment_method,c.payment_status,c.status,
                    c.cancellation_reason,c.fulfillment_method,c.payment_proof_filename,q.queue_id
             FROM custom_orders c JOIN users u ON u.id=c.user_id
             JOIN order_queue q ON q.order_type='Custom' AND q.order_id=c.id
           ) AS queued_orders
           ORDER BY
             CASE WHEN order_status NOT IN ('Delivered','Picked Up','Cancelled') THEN 0 ELSE 1 END,
             CASE WHEN order_status NOT IN ('Delivered','Picked Up','Cancelled') THEN queue_id END ASC,
             CASE WHEN order_status IN ('Delivered','Picked Up','Cancelled') THEN created_at END DESC,
             id DESC"""
    )
    visible_orders, stage_counts, active_stage = filter_orders_by_stage(
        orders, request.args.get("stage", "all")
    )
    return render_template(
        "admin/orders.html",
        orders=visible_orders,
        statuses=ORDER_STATUSES,
        order_stages=ORDER_STAGES,
        order_stage_labels=dict(ORDER_STAGES),
        stage_counts=stage_counts,
        active_stage=active_stage,
    )


@route("/admin/orders/add", methods=["GET", "POST"])
@role_required("admin")
def admin_order_add():
    ensure_manual_order_schema()
    ensure_gcash_schema()
    ensure_admin_operations_schema()
    form_data = request.form.to_dict() if request.method == "POST" else {}
    if request.method == "POST":
        try:
            order_date = datetime.strptime(
                form_data.get("order_date", "").strip(), "%Y-%m-%d"
            ).date()
            if order_date > datetime.now().date():
                raise ValueError("Order date cannot be in the future.")

            customer_name = form_data.get("customer_name", "").strip()
            phone = form_data.get("phone", "").strip()
            delivery_address = form_data.get("delivery_address", "").strip()
            product_name = form_data.get("product_name", "").strip()
            notes = form_data.get("notes", "").strip()
            if not customer_name or len(customer_name) > 120:
                raise ValueError("Enter a customer name of at most 120 characters.")
            if not phone or len(phone) > 30:
                raise ValueError("Enter a contact number of at most 30 characters.")
            if not delivery_address or len(delivery_address) > 2000:
                raise ValueError("Enter a delivery address of at most 2,000 characters.")
            if not product_name or len(product_name) > 160:
                raise ValueError("Enter a bouquet name of at most 160 characters.")
            if len(notes) > 2000:
                raise ValueError("Notes must be 2,000 characters or fewer.")

            quantity = int(form_data.get("quantity", ""))
            unit_price = Decimal(form_data.get("unit_price", "")).quantize(
                Decimal("0.01")
            )
            delivery_fee = Decimal(form_data.get("delivery_fee", "0")).quantize(
                Decimal("0.01")
            )
            if quantity < 1 or quantity > 100000:
                raise ValueError("Quantity must be between 1 and 100,000.")
            max_amount = Decimal("99999999.99")
            if not unit_price.is_finite() or unit_price <= 0 or unit_price > max_amount:
                raise ValueError("Product price must be greater than zero and within the allowed amount.")
            if not delivery_fee.is_finite() or delivery_fee < 0 or delivery_fee > max_amount:
                raise ValueError("Delivery fee must be zero or more and within the allowed amount.")
            subtotal = (unit_price * quantity).quantize(Decimal("0.01"))
            total = (subtotal + delivery_fee).quantize(Decimal("0.01"))
            if total > max_amount:
                raise ValueError("The order total exceeds the allowed amount.")

            payment_status = form_data.get("payment_status", "")
            if payment_status not in {"Unpaid", "Pending", "Paid", "Failed", "Refunded"}:
                raise ValueError("Choose a valid payment status.")
            order_status = form_data.get("order_status", "")
            if order_status not in ORDER_STATUSES:
                raise ValueError("Choose a valid order status.")
            order_id = Order.create_manual(
                {
                    "order_date": order_date,
                    "customer_name": customer_name,
                    "phone": phone,
                    "delivery_address": delivery_address,
                    "product_name": product_name,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                    "delivery_fee": delivery_fee,
                    "total": total,
                    "payment_status": payment_status,
                    "order_status": order_status,
                    "notes": notes,
                }
            )
        except (ValueError, InvalidOperation, ArithmeticError) as exc:
            flash(str(exc) or "Enter valid order details.", "danger")
            return render_template(
                "admin/manual_order_form.html",
                form_data=form_data,
                statuses=ORDER_STATUSES,
                today_date=datetime.now().date().isoformat(),
            ), 400
        except mysql.connector.Error:
            current_app.logger.exception("Could not save an admin-entered order.")
            flash("The order could not be saved. Please try again.", "danger")
            return render_template(
                "admin/manual_order_form.html",
                form_data=form_data,
                statuses=ORDER_STATUSES,
                today_date=datetime.now().date().isoformat(),
            ), 500
        log_admin_activity(
            "Added manual order",
            "Order",
            order_id,
            f"{order_date} · {quantity} × {product_name} · ₱{total:.2f}",
        )
        flash("Order added successfully.", "success")
        return redirect(url_for("admin_order_detail", order_id=order_id))

    return render_template(
        "admin/manual_order_form.html",
        form_data=form_data,
        statuses=ORDER_STATUSES,
        today_date=datetime.now().date().isoformat(),
    )


@route("/admin/orders/export.csv")
@role_required("admin")
def admin_orders_export():
    ensure_manual_order_schema()
    rows = query_all(
        """SELECT order_type,order_id,created_at,customer_name,customer_email,
                  phone,fulfillment_method,delivery_address,total,payment_method,
                  payment_status,order_status
           FROM (
             SELECT 'Bouquet' AS order_type,o.id AS order_id,o.created_at,
                    o.customer_name,o.customer_email,o.phone,o.fulfillment_method,
                    o.delivery_address,o.total,o.payment_method,o.payment_status,
                    o.order_status
             FROM orders o
             UNION ALL
             SELECT 'Custom bouquet',c.id,c.created_at,u.full_name,u.email,u.phone,
                    NULL,NULL,c.total_price,c.payment_method,c.payment_status,c.status
             FROM custom_orders c JOIN users u ON u.id=c.user_id
           ) AS order_history
           ORDER BY created_at DESC"""
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "Order type", "Order number", "Created at", "Customer", "Email",
        "Phone", "Fulfillment", "Address", "Total (PHP)", "Payment method",
        "Payment status", "Order status",
    ))
    for row in rows:
        values = [
            row["order_type"], row["order_id"], row["created_at"],
            row["customer_name"], row["customer_email"], row["phone"],
            row["fulfillment_method"], row["delivery_address"], row["total"],
            row["payment_method"], row["payment_status"], row["order_status"],
        ]
        writer.writerow([
            f"'{value}" if isinstance(value, str)
            and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
            else value
            for value in values
        ])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=fuzzywire-orders.csv"
    return response


@route("/admin/orders/<int:order_id>", methods=["GET", "POST"])
@role_required("admin")
def admin_order_detail(order_id):
    ensure_gcash_schema()
    ensure_delivery_distance_schema()
    ensure_fulfillment_schema()
    ensure_order_messaging_schema()
    ensure_delivery_slots_schema()
    ensure_admin_operations_schema()
    order = query_one("SELECT * FROM orders WHERE id=%s", (order_id,))
    if not order:
        abort(404)
    statuses = (
        [status for status in ORDER_STATUSES if status not in {"Ready for Delivery", "Out for Delivery", "Delivered"}]
        if order["fulfillment_method"] == "Pickup"
        else [status for status in ORDER_STATUSES if status not in {"Ready for Pickup", "Picked Up"}]
    )
    if request.method == "POST":
        status = request.form.get("order_status")
        if status not in statuses:
            flash("Invalid status.", "danger")
        else:
            cancellation_reason = request.form.get("cancellation_reason", "").strip()
            if status == "Cancelled" and not cancellation_reason:
                flash("Please enter a reason for cancelling this order.", "danger")
            elif len(cancellation_reason) > 1000:
                flash("The cancellation reason must be 1,000 characters or fewer.", "danger")
            else:
                items = query_all("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
                payment_description = order["payment_method"]
                confirmation_message = build_order_confirmation_message(
                    order_id, items, order["total"], payment_description,
                    order["fulfillment_method"], order["delivery_address"],
                )
                previous_status, changed, confirmation_sent, queue_message = update_admin_order_status(
                    "orders", "order_status", order_id, order["user_id"], status,
                    cancellation_reason, confirmation_message,
                )
                if queue_message:
                    flash(queue_message, "warning")
                    return redirect(url_for("admin_order_detail", order_id=order_id))
                if not changed:
                    flash(f"Order status is already {status}.", "info")
                    return redirect(url_for("admin_order_detail", order_id=order_id))
                log_admin_activity("Updated order status", "Order", order_id, f"{previous_status} → {status}")
                if previous_status != status:
                    execute("UPDATE deliveries SET delivery_status=%s WHERE order_id=%s",
                            (status, order_id))
                if status == "Cancelled":
                    notification_message = f"Your order #{order_id} was cancelled. Reason: {cancellation_reason}"
                elif status == "Out for Delivery":
                    notification_message = f"Your order #{order_id} is on its way to your delivery address."
                elif status == "Ready for Pickup":
                    notification_message = f"Your order #{order_id} is ready for pickup at the shop."
                else:
                    notification_message = f"Your order #{order_id} is now: {status}."
                if status in {"Out for Delivery", "Ready for Pickup"} and order["user_id"]:
                    notification_title = (
                        "Your order is ready for pickup"
                        if status == "Ready for Pickup"
                        else "Your order is out for delivery"
                    )
                    notify(order["user_id"], notification_title,
                           notification_message,
                           url_for("order_detail", order_id=order_id))
                elif status == "Cancelled" and order["user_id"]:
                    notify(order["user_id"], "Your order was cancelled", notification_message,
                           url_for("order_detail", order_id=order_id))
                elif order["user_id"]:
                    notify(order["user_id"], "Order status updated", notification_message,
                           url_for("order_detail", order_id=order_id))
                if order["user_id"] and status in {"Out for Delivery", "Ready for Pickup", "Cancelled"}:
                    send_order_email(
                        order["customer_email"],
                        f"FuzzyWire order #{order_id} update",
                        notification_message,
                    )
                if confirmation_sent:
                    flash("Order confirmed and order details sent in chat.", "success")
                elif status == "Cancelled" and order["user_id"]:
                    flash("Order cancelled and customer notified.", "success")
                else:
                    flash(
                        "Order status updated and customer notified."
                        if order["user_id"] else "Order status updated.",
                        "success",
                    )
                return redirect(url_for("admin_order_detail", order_id=order_id))
    items = query_all("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
    delivery = query_one("SELECT * FROM deliveries WHERE order_id=%s", (order_id,))
    scheduled_slot = query_one(
        "SELECT starts_at,fulfillment_method FROM delivery_slots WHERE id=%s",
        (order["delivery_slot_id"],),
    ) if order.get("delivery_slot_id") else None
    admin_notes = query_all(
        """SELECT n.note,n.created_at,u.full_name AS admin_name FROM order_admin_notes n
           LEFT JOIN users u ON u.id=n.admin_id
           WHERE n.order_type='Order' AND n.order_id=%s ORDER BY n.created_at DESC""",
        (order_id,),
    )
    refunds = query_all(
        "SELECT * FROM order_refunds WHERE order_id=%s ORDER BY created_at DESC",
        (order_id,),
    )
    return render_template("admin/order_detail.html", order=order, items=items,
                           delivery=delivery, statuses=statuses, scheduled_slot=scheduled_slot,
                           admin_notes=admin_notes, refunds=refunds,
                           payment_proof_url=url_for("payment_proof_file", order_type="Order", order_id=order_id))


@route("/admin/custom-orders")
@role_required("admin")
def admin_custom_orders():
    return redirect(url_for("admin_orders"))


@route("/admin/custom-orders/<int:custom_id>", methods=["GET", "POST"])
@role_required("admin")
def admin_custom_detail(custom_id):
    ensure_gcash_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_order_messaging_schema()
    ensure_custom_materials_schema()
    ensure_delivery_slots_schema()
    ensure_admin_operations_schema()
    order = query_one("""SELECT c.*, u.full_name, u.email FROM custom_orders c
                         JOIN users u ON u.id=c.user_id WHERE c.id=%s""", (custom_id,))
    if not order:
        abort(404)
    statuses = (
        [status for status in ORDER_STATUSES if status not in {"Ready for Delivery", "Out for Delivery", "Delivered"}]
        if order["fulfillment_method"] == "Pickup"
        else [status for status in ORDER_STATUSES if status not in {"Ready for Pickup", "Picked Up"}]
    )
    if request.method == "POST":
        status = request.form.get("status")
        if status not in statuses:
            flash("Invalid status.", "danger")
        else:
            cancellation_reason = request.form.get("cancellation_reason", "").strip()
            if status == "Cancelled" and not cancellation_reason:
                flash("Please enter a reason for cancelling this request.", "danger")
            elif len(cancellation_reason) > 1000:
                flash("The cancellation reason must be 1,000 characters or fewer.", "danger")
            else:
                previous_status, changed, confirmation_sent, queue_message = update_admin_order_status(
                    "custom_orders", "status", custom_id, order["user_id"], status,
                    cancellation_reason, build_custom_confirmation_message(order),
                )
                if queue_message:
                    flash(queue_message, "warning")
                    return redirect(url_for("admin_custom_detail", custom_id=custom_id))
                if not changed:
                    flash(f"Custom order status is already {status}.", "info")
                    return redirect(url_for("admin_custom_detail", custom_id=custom_id))
                log_admin_activity("Updated custom order status", "Custom order", custom_id,
                                   f"{previous_status} → {status}")
                if status == "Cancelled":
                    notification_title = "Your custom order was cancelled"
                    notification_message = (
                        f"Your custom bouquet request #C{custom_id} was cancelled. "
                        f"Reason: {cancellation_reason}"
                    )
                elif status == "Out for Delivery":
                    notification_title = "Your order is out for delivery"
                    notification_message = f"Your custom bouquet request #C{custom_id} is on its way to you."
                elif status == "Ready for Pickup":
                    notification_title = "Custom bouquet ready for pickup"
                    notification_message = f"Your custom bouquet request #C{custom_id} is ready for pickup at the shop."
                else:
                    notification_title = "Custom bouquet status updated"
                    notification_message = f"Custom order #C{custom_id}: {status}."
                notify(
                    order["user_id"], notification_title, notification_message,
                    url_for("customer_custom_detail", custom_id=custom_id),
                )
                if status in {"Out for Delivery", "Ready for Pickup", "Cancelled"}:
                    send_order_email(
                        order["email"],
                        f"FuzzyWire custom order #C{custom_id} update",
                        notification_message,
                    )
                if confirmation_sent:
                    flash("Custom order confirmed and details sent in chat.", "success")
                elif status == "Cancelled":
                    flash("Custom order cancelled and customer notified.", "success")
                else:
                    flash("Custom order status updated.", "success")
                return redirect(url_for("admin_custom_detail", custom_id=custom_id))
    materials = query_all(
        """SELECT material_name,category,quantity,unit_price,line_total
           FROM custom_order_materials WHERE custom_order_id=%s ORDER BY id""",
        (custom_id,),
    )
    scheduled_slot = query_one(
        "SELECT id,starts_at,fulfillment_method FROM delivery_slots WHERE id=%s",
        (order["delivery_slot_id"],),
    ) if order.get("delivery_slot_id") else None
    admin_notes = query_all(
        """SELECT n.note,n.created_at,u.full_name AS admin_name FROM order_admin_notes n
           LEFT JOIN users u ON u.id=n.admin_id
           WHERE n.order_type='Custom' AND n.order_id=%s ORDER BY n.created_at DESC""",
        (custom_id,),
    )
    return render_template("admin/custom_detail.html", order=order, statuses=statuses,
                           materials=materials, scheduled_slot=scheduled_slot, admin_notes=admin_notes,
                           payment_proof_url=url_for("payment_proof_file", order_type="Custom", order_id=custom_id))


@route("/admin/order-notes/<order_type>/<int:order_id>", methods=["POST"])
@role_required("admin")
def admin_order_note_add(order_type, order_id):
    ensure_admin_operations_schema()
    table = {"Order": "orders", "Custom": "custom_orders"}.get(order_type)
    if not table:
        abort(404)
    if not query_one(f"SELECT id FROM {table} WHERE id=%s", (order_id,)):
        abort(404)
    note = request.form.get("note", "").strip()
    if not note or len(note) > 2000:
        flash("Internal notes must contain 1–2,000 characters.", "danger")
    else:
        execute(
            """INSERT INTO order_admin_notes (order_type,order_id,admin_id,note)
               VALUES (%s,%s,%s,%s)""",
            (order_type, order_id, current_user()["id"], note),
        )
        log_admin_activity("Added internal order note", order_type, order_id)
        flash("Internal note saved.", "success")
    return redirect(
        url_for("admin_order_detail", order_id=order_id)
        if order_type == "Order"
        else url_for("admin_custom_detail", custom_id=order_id)
    )


@route("/admin/orders/<int:order_id>/refunds", methods=["POST"])
@role_required("admin")
def admin_refund_add(order_id):
    ensure_admin_operations_schema()
    try:
        amount = Decimal(request.form.get("amount", ""))
        reason = request.form.get("reason", "").strip()
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Refund amount must be positive and no more than the order total.")
        if not reason or len(reason) > 1000:
            raise ValueError("Enter a refund reason of 1–1,000 characters.")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc) or "Enter a valid refund amount.", "danger")
        return redirect(url_for("admin_order_detail", order_id=order_id))
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,total,payment_status FROM orders WHERE id=%s FOR UPDATE", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        if amount > Decimal(str(order["total"])):
            raise ValueError("Refund amount must be positive and no more than the order total.")
        if order["payment_status"] not in {"Paid", "Refunded"}:
            raise ValueError("Only paid orders can have a refund recorded.")
        cur.execute(
            """SELECT COALESCE(SUM(amount),0) AS amount FROM order_refunds
               WHERE order_id=%s AND status='Completed'""",
            (order_id,),
        )
        already_completed = cur.fetchone()["amount"]
        if Decimal(str(already_completed)) + amount > Decimal(str(order["total"])):
            raise ValueError("Completed refunds cannot exceed the amount paid.")
        cur.execute(
            """INSERT INTO order_refunds (order_id,amount,reason,created_by)
               VALUES (%s,%s,%s,%s)""",
            (order_id, amount, reason, current_user()["id"]),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("admin_order_detail", order_id=order_id))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log_admin_activity("Recorded refund", "Order", order_id, f"₱{amount:.2f}")
    flash("Refund record saved. This does not initiate a payment-provider refund.", "success")
    return redirect(url_for("admin_order_detail", order_id=order_id))


@route("/admin/orders/<int:order_id>/refunds/<int:refund_id>/complete", methods=["POST"])
@role_required("admin")
def admin_refund_complete(order_id, refund_id):
    ensure_admin_operations_schema()
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,total FROM orders WHERE id=%s FOR UPDATE", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        cur.execute(
            "SELECT id,amount,status FROM order_refunds WHERE id=%s AND order_id=%s FOR UPDATE",
            (refund_id, order_id),
        )
        refund = cur.fetchone()
        if not refund:
            abort(404)
        if refund["status"] == "Completed":
            conn.rollback()
            flash("That refund is already marked completed.", "info")
            return redirect(url_for("admin_order_detail", order_id=order_id))
        cur.execute(
            """SELECT COALESCE(SUM(amount),0) AS amount FROM order_refunds
               WHERE order_id=%s AND status='Completed'""",
            (order_id,),
        )
        completed_before = cur.fetchone()["amount"]
        if Decimal(str(completed_before)) + Decimal(str(refund["amount"])) > Decimal(str(order["total"])):
            raise ValueError("Completed refunds cannot exceed the amount paid.")
        cur.execute(
            "UPDATE order_refunds SET status='Completed' WHERE id=%s",
            (refund_id,),
        )
        cur.execute(
            """SELECT COALESCE(SUM(amount),0) AS amount FROM order_refunds
               WHERE order_id=%s AND status='Completed'""",
            (order_id,),
        )
        completed_total = cur.fetchone()["amount"]
        if Decimal(str(completed_total)) >= Decimal(str(order["total"])):
            cur.execute("UPDATE orders SET payment_status='Refunded' WHERE id=%s", (order_id,))
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("admin_order_detail", order_id=order_id))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log_admin_activity(
        "Completed refund record", "Order", order_id, f"₱{Decimal(str(refund['amount'])):.2f}"
    )
    flash("Refund record marked completed. Process the actual refund separately with the payment provider.", "success")
    return redirect(url_for("admin_order_detail", order_id=order_id))


@route("/admin/customers")
@role_required("admin")
def admin_customers():
    ensure_customer_profile_schema()
    ensure_admin_operations_schema()
    ensure_order_queue_schema()
    customers = query_all("""SELECT u.id,u.full_name,u.email,u.phone,u.created_at,u.admin_tags,
      u.account_status,u.verification_status,u.email_verified,
      u.profile_image,u.delivery_address,
      (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) +
      (SELECT COUNT(*) FROM custom_orders c WHERE c.user_id=u.id) AS order_count
      FROM users u WHERE u.role='customer' ORDER BY u.created_at DESC""")
    order_rows = query_all(
        """SELECT user_id,order_type,order_id,created_at,total,order_status,
                  payment_status,fulfillment_method
           FROM (
             SELECT o.user_id,'Bouquet' AS order_type,o.id AS order_id,o.created_at,
                    o.total,o.order_status,o.payment_status,o.fulfillment_method,q.queue_id
             FROM orders o JOIN order_queue q
               ON q.order_type='Order' AND q.order_id=o.id
             UNION ALL
             SELECT c.user_id,'Custom bouquet',c.id,c.created_at,c.total_price,
                    c.status,c.payment_status,NULL,q.queue_id
             FROM custom_orders c JOIN order_queue q
               ON q.order_type='Custom' AND q.order_id=c.id
           ) AS customer_orders
           ORDER BY
             CASE WHEN order_status NOT IN ('Delivered','Cancelled') THEN 0 ELSE 1 END,
             CASE WHEN order_status NOT IN ('Delivered','Cancelled') THEN queue_id END ASC,
             CASE WHEN order_status IN ('Delivered','Cancelled') THEN created_at END DESC,
             order_id DESC"""
    )
    orders_by_customer = {}
    for row in order_rows:
        orders_by_customer.setdefault(row["user_id"], []).append(row)
    for customer in customers:
        customer["order_history"] = orders_by_customer.get(customer["id"], [])
    return render_template("admin/customers.html", customers=customers)


@route("/admin/customers/<int:customer_id>/status", methods=["POST"])
@role_required("admin")
def admin_customer_status(customer_id):
    action = request.form.get("action")
    if action not in {"deactivate", "reactivate"}:
        abort(400, description="Choose a valid customer account action.")
    ensure_customer_verification_schema()
    customer = query_one(
        """SELECT id,full_name,account_status,verification_status,email_verified
           FROM users WHERE id=%s AND role='customer'""",
        (customer_id,),
    )
    if not customer:
        abort(404)
    if action == "deactivate":
        execute(
            "UPDATE users SET account_status='Deactivated' WHERE id=%s",
            (customer_id,),
        )
        new_status = "Deactivated"
    elif (
        customer["verification_status"] != "Approved"
        or not customer["email_verified"]
    ):
        flash(
            "This customer can only be reactivated after their email and ID check are approved.",
            "warning",
        )
        return redirect(url_for("admin_customers"))
    else:
        connection = db()
        try:
            cur = connection.cursor()
            cur.execute(
                """UPDATE users SET account_status='Active'
                   WHERE id=%s AND role='customer'
                     AND verification_status='Approved' AND email_verified=1""",
                (customer_id,),
            )
            if cur.rowcount != 1:
                connection.rollback()
                flash(
                    "This customer can only be reactivated after their email and ID check are approved.",
                    "warning",
                )
                return redirect(url_for("admin_customers"))
            connection.commit()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            connection.close()
        new_status = "Active"
    log_admin_activity(
        f"Customer account {new_status.lower()}",
        "Customer",
        customer_id,
        f"{customer['full_name']} ({customer['account_status']} → {new_status})",
    )
    flash(f"{customer['full_name']}'s account is now {new_status.lower()}.", "success")
    return redirect(url_for("admin_customers"))


@route("/admin/activity")
@role_required("admin")
def admin_activity():
    ensure_admin_operations_schema()
    entries = query_all(
        """SELECT l.action,l.target_type,l.target_id,l.details,l.created_at,u.full_name AS admin_name
           FROM admin_activity_logs l LEFT JOIN users u ON u.id=l.admin_id
           ORDER BY l.created_at DESC LIMIT 200"""
    )
    return render_template("admin/activity.html", entries=entries)


@route("/admin/customers/<int:customer_id>/tags", methods=["POST"])
@role_required("admin")
def admin_customer_tags(customer_id):
    ensure_admin_operations_schema()
    customer = query_one(
        "SELECT id,full_name FROM users WHERE id=%s AND role='customer'",
        (customer_id,),
    )
    if not customer:
        abort(404)
    tags = request.form.get("admin_tags", "").strip()
    if len(tags) > 500:
        flash("Customer tags must be 500 characters or fewer.", "danger")
    else:
        execute("UPDATE users SET admin_tags=%s WHERE id=%s", (tags or None, customer_id))
        log_admin_activity("Updated customer tags", "Customer", customer_id)
        flash("Customer tags updated.", "success")
    return redirect(url_for("admin_customers"))


@route("/admin/feedback")
@role_required("admin")
def admin_feedback():
    ensure_feedback_product_schema()
    execute("UPDATE feedback SET admin_checked_at=NOW() WHERE admin_checked_at IS NULL")
    execute(
        """UPDATE notifications SET is_read=1
           WHERE user_id=%s AND title='New customer feedback' AND is_read=0""",
        (current_user()["id"],),
    )
    rows = query_all("""SELECT f.*,u.full_name,u.email,
                               COALESCE(oi.item_name,f.product_name) AS item_name
                        FROM feedback f JOIN users u ON u.id=f.user_id
                        LEFT JOIN order_items oi
                          ON oi.order_id=f.order_id AND oi.product_id=f.product_id
                        ORDER BY f.created_at DESC,f.id DESC""")
    summary = query_one(
        """SELECT COUNT(*) AS total,COALESCE(AVG(rating),0) AS avg_rating
           FROM feedback WHERE product_id IS NOT NULL"""
    )
    return render_template("admin/feedback.html", feedback=rows, summary=summary)


@route("/admin/feedback/<int:feedback_id>/reply", methods=["POST"])
@role_required("admin")
def admin_feedback_reply(feedback_id):
    ensure_feedback_product_schema()
    feedback_row = query_one(
        """SELECT f.id,f.user_id,f.admin_reply,
                  COALESCE(oi.item_name,f.product_name) AS item_name
           FROM feedback f
           LEFT JOIN order_items oi
             ON oi.order_id=f.order_id AND oi.product_id=f.product_id
           WHERE f.id=%s""",
        (feedback_id,),
    )
    if not feedback_row:
        abort(404)
    reply = request.form.get("admin_reply", "").strip()
    if len(reply) > 2000:
        flash("Replies must be 2,000 characters or fewer.", "danger")
        return redirect(url_for("admin_feedback") + f"#feedback-{feedback_id}")
    changed = reply != (feedback_row["admin_reply"] or "")
    execute(
        "UPDATE feedback SET admin_reply=%s WHERE id=%s",
        (reply or None, feedback_id),
    )
    if changed and reply:
        bouquet_name = feedback_row["item_name"] or "your bouquet"
        notify(
            feedback_row["user_id"],
            "Admin replied to your feedback",
            f"The admin replied to your review of {bouquet_name}.",
            url_for("feedback"),
        )
    flash(
        "Feedback reply saved." if reply else "Feedback reply removed.",
        "success",
    )
    return redirect(url_for("admin_feedback") + f"#feedback-{feedback_id}")


@route("/admin/feedback/<int:feedback_id>/delete", methods=["POST"])
@role_required("admin")
def admin_feedback_delete(feedback_id):
    ensure_feedback_product_schema()
    feedback_row = query_one(
        "SELECT id,product_name FROM feedback WHERE id=%s",
        (feedback_id,),
    )
    if not feedback_row:
        abort(404)
    execute("DELETE FROM feedback WHERE id=%s", (feedback_id,))
    log_admin_activity(
        "Deleted customer feedback",
        "Feedback",
        feedback_id,
        feedback_row["product_name"] or "",
    )
    flash("Customer feedback deleted.", "success")
    return redirect(url_for("admin_feedback"))


@route("/admin/analytics")
@role_required("admin")
def admin_analytics():
    ensure_manual_order_schema()
    ensure_feedback_product_schema()
    ensure_customer_delivery_confirmation_schema()
    ensure_admin_operations_schema()

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    parsed_start = parsed_end = None
    try:
        if start_date:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date:
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        abort(400, "Choose valid start and end dates.")
    if parsed_start and parsed_end and parsed_start > parsed_end:
        abort(400, "The start date must be on or before the end date.")

    def period_filter(alias):
        clauses = []
        params = []
        if parsed_start:
            clauses.append(f"{alias}.created_at >= %s")
            params.append(parsed_start.isoformat())
        if parsed_end:
            clauses.append(f"{alias}.created_at < DATE_ADD(%s, INTERVAL 1 DAY)")
            params.append(parsed_end.isoformat())
        return (" AND ".join(clauses) or "1=1", tuple(params))

    order_filter, order_params = period_filter("o")
    custom_filter, custom_params = period_filter("c")
    regular = query_one(f"""SELECT COUNT(*) AS total,
        COALESCE(SUM(o.order_status IN ('Delivered','Picked Up')),0) AS delivered,
        COALESCE(SUM(o.order_status='Cancelled'),0) AS cancelled,
        COALESCE(SUM(o.order_status NOT IN ('Delivered','Picked Up','Cancelled')),0) AS open,
        COALESCE(SUM(CASE
          WHEN o.order_status <> 'Cancelled'
            AND o.payment_status='Paid'
          THEN GREATEST(o.total-COALESCE((SELECT SUM(r.amount) FROM order_refunds r
            WHERE r.order_id=o.id AND r.status='Completed'),0),0) ELSE 0 END),0) AS sales
        FROM orders o WHERE {order_filter}""", order_params)
    custom = query_one(f"""SELECT COUNT(*) AS total,
        COALESCE(SUM(c.status IN ('Delivered','Picked Up')),0) AS delivered,
        COALESCE(SUM(c.status='Cancelled'),0) AS cancelled,
        COALESCE(SUM(c.status NOT IN ('Delivered','Picked Up','Cancelled')),0) AS open,
        COALESCE(SUM(CASE WHEN c.status <> 'Cancelled' AND c.payment_status='Paid'
          THEN c.total_price ELSE 0 END),0) AS sales
        FROM custom_orders c WHERE {custom_filter}""", custom_params)
    buyers = query_one(f"""SELECT COUNT(DISTINCT buyer_id) AS total FROM (
        SELECT o.user_id AS buyer_id FROM orders o WHERE {order_filter}
        UNION
        SELECT c.user_id AS buyer_id FROM custom_orders c WHERE {custom_filter}
    ) AS customers_with_orders""", order_params + custom_params)["total"]

    period_days = (parsed_end - parsed_start).days if parsed_start and parsed_end else None
    trend_interval = "Daily" if period_days is not None and period_days <= 90 else "Monthly"
    bucket = ("DATE_FORMAT(created_at, '%Y-%m-%d')"
              if period_days is not None and period_days <= 90
              else "DATE_FORMAT(created_at, '%Y-%m')")
    trend = query_all(f"""SELECT period, SUM(order_count) AS orders_count,
        SUM(sales) AS sales FROM (
          SELECT {bucket} AS period, COUNT(*) AS order_count,
            SUM(CASE WHEN o.order_status <> 'Cancelled'
              AND o.payment_status='Paid'
              THEN GREATEST(o.total-COALESCE((SELECT SUM(r.amount) FROM order_refunds r
                WHERE r.order_id=o.id AND r.status='Completed'),0),0) ELSE 0 END) AS sales
          FROM orders o WHERE {order_filter} GROUP BY period
          UNION ALL
          SELECT {bucket} AS period, COUNT(*) AS order_count,
            SUM(CASE WHEN c.status <> 'Cancelled' AND c.payment_status='Paid'
              THEN c.total_price ELSE 0 END) AS sales
          FROM custom_orders c WHERE {custom_filter} GROUP BY period
        ) AS monthly_orders GROUP BY period ORDER BY period""",
        order_params + custom_params)
    best_filter, best_params = period_filter("o")
    best = query_all(f"""SELECT oi.item_name,SUM(oi.quantity) AS quantity,
        SUM(oi.line_total) AS revenue
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE o.order_status IN ('Delivered','Picked Up') AND {best_filter}
        GROUP BY oi.item_name ORDER BY quantity DESC,oi.item_name LIMIT 10""",
        best_params)
    colors = query_all(f"""SELECT oi.color, SUM(oi.quantity) AS quantity
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE o.order_status IN ('Delivered','Picked Up') AND {best_filter}
        GROUP BY oi.color ORDER BY quantity DESC, oi.color""", best_params)
    custom_flowers = query_all(f"""SELECT c.flower_type, SUM(c.quantity) AS quantity
        FROM custom_orders c WHERE c.status IN ('Delivered','Picked Up') AND {custom_filter}
        GROUP BY c.flower_type ORDER BY quantity DESC, c.flower_type""", custom_params)
    repeat_customers = query_all(f"""SELECT u.full_name,u.email,COUNT(*) AS orders_count
        FROM (
          SELECT o.user_id FROM orders o
          WHERE o.order_status <> 'Cancelled' AND {order_filter}
          UNION ALL
          SELECT c.user_id FROM custom_orders c
          WHERE c.status <> 'Cancelled' AND {custom_filter}
        ) AS customer_orders
        JOIN users u ON u.id=customer_orders.user_id
        GROUP BY u.id,u.full_name,u.email
        HAVING COUNT(*) >= 2
        ORDER BY orders_count DESC,u.full_name LIMIT 10""",
        order_params + custom_params)
    peak_hours = query_all(f"""SELECT hour,COUNT(*) AS orders_count FROM (
        SELECT HOUR(o.created_at) AS hour FROM orders o
        WHERE o.order_status <> 'Cancelled' AND {order_filter}
        UNION ALL
        SELECT HOUR(c.created_at) AS hour FROM custom_orders c
        WHERE c.status <> 'Cancelled' AND {custom_filter}
    ) AS live_order_hours GROUP BY hour ORDER BY orders_count DESC,hour LIMIT 8""",
        order_params + custom_params)
    statuses = query_all(f"""SELECT order_type, status, COUNT(*) AS count FROM (
        SELECT 'Bouquet' AS order_type, o.order_status AS status
        FROM orders o WHERE {order_filter}
        UNION ALL
        SELECT 'Custom bouquet', c.status FROM custom_orders c WHERE {custom_filter}
    ) AS all_orders GROUP BY order_type, status
       ORDER BY order_type, FIELD(status, 'Pending','Confirmed','Preparing',
         'Ready for Delivery','Out for Delivery','Delivered','Picked Up','Cancelled')""",
        order_params + custom_params)
    payments = query_all(f"""SELECT payment_method,payment_status,COUNT(*) AS count,
        SUM(amount) AS amount,SUM(sales) AS sales
        FROM (
          SELECT o.payment_method,o.payment_status,o.total AS amount,
            CASE WHEN o.order_status <> 'Cancelled'
              AND o.payment_status='Paid' THEN GREATEST(o.total-COALESCE((SELECT SUM(r.amount) FROM order_refunds r
                WHERE r.order_id=o.id AND r.status='Completed'),0),0) ELSE 0 END AS sales
          FROM orders o WHERE {order_filter}
          UNION ALL
          SELECT c.payment_method,c.payment_status,c.total_price,
            CASE WHEN c.status <> 'Cancelled' AND c.payment_status='Paid'
              THEN c.total_price ELSE 0 END
          FROM custom_orders c WHERE {custom_filter}
        ) AS payment_records
        GROUP BY payment_method,payment_status
        ORDER BY payment_method,payment_status""",
        order_params + custom_params)
    ratings = query_one(
        """SELECT COALESCE(AVG(rating),0) AS avg_rating,COUNT(*) AS total
           FROM feedback f WHERE f.product_id IS NOT NULL
             AND (%s IS NULL OR f.created_at >= %s)
             AND (%s IS NULL OR f.created_at < DATE_ADD(%s, INTERVAL 1 DAY))""",
        (parsed_start, parsed_start, parsed_end, parsed_end)
    )
    for row in trend:
        row["period"] = str(row["period"])
        row["sales"] = float(row["sales"] or 0)
        row["orders_count"] = int(row["orders_count"] or 0)
    for row in best:
        row["quantity"] = int(row["quantity"] or 0)
        row["revenue"] = float(row["revenue"] or 0)
    for rows in (colors, custom_flowers):
        for row in rows:
            row["quantity"] = int(row["quantity"] or 0)
    stats = {
        "sales": regular["sales"] + custom["sales"],
        "regular_sales": regular["sales"],
        "custom_sales": custom["sales"],
        "orders": regular["total"] + custom["total"],
        "delivered": regular["delivered"] + custom["delivered"],
        "cancelled": regular["cancelled"] + custom["cancelled"],
        "open": regular["open"] + custom["open"],
        "buyers": buyers,
        "regular_orders": regular["total"],
        "custom_orders": custom["total"],
    }
    return render_template("admin/analytics.html", stats=stats, trend=trend, best=best,
                           colors=colors, custom_flowers=custom_flowers, statuses=statuses,
                           repeat_customers=repeat_customers, peak_hours=peak_hours,
                           payments=payments, ratings=ratings, start_date=start_date,
                           end_date=end_date, trend_interval=trend_interval)
