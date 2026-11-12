import hashlib

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import BASE_DIR, Config
from database import query_one
from routes import register_routes
from services.common import (
    current_user,
    csrf_token,
    ensure_chat_schema,
    ensure_custom_materials_schema,
    ensure_customer_verification_schema,
    ensure_feedback_product_schema,
    ensure_gcash_schema,
)


def development_source_version():
    watched_files = list(BASE_DIR.glob("*.py"))
    for folder, suffix in (
        ("routes", ".py"),
        ("services", ".py"),
        ("templates", ".html"),
        ("static/css", ".css"),
        ("static/js", ".js"),
    ):
        watched_files.extend(
            path for path in (BASE_DIR / folder).rglob(f"*{suffix}")
            if path.is_file()
        )

    signature = hashlib.sha256()
    for path in sorted(watched_files):
        stat = path.stat()
        signature.update(path.relative_to(BASE_DIR).as_posix().encode("utf-8"))
        signature.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii"))
    return signature.hexdigest()


def inject_globals():
    user = current_user()
    if getattr(g, "rendering_error_page", False):
        return {
            "current_user": None,
            "csrf_token": csrf_token,
            "unread_notifications": 0,
            "admin_unread_notifications": 0,
            "admin_new_order_count": 0,
            "admin_new_feedback_count": 0,
            "pending_verification_count": 0,
            "cart_item_count": 0,
            "unread_chat_count": 0,
            "active_orders_count": 0,
            "pending_feedback_count": 0,
            "wishlist_count": 0,
            "available_vouchers_count": 0,
        }
    unread_notifications = 0
    admin_unread_notifications = 0
    admin_new_order_count = 0
    admin_new_feedback_count = 0
    pending_verification_count = 0
    admin_unread_chat_count = 0
    admin_pending_payments_count = 0
    admin_low_stock_products_count = 0
    admin_low_stock_materials_count = 0
    unread_chat_count = 0
    active_orders_count = 0
    pending_feedback_count = 0
    wishlist_count = 0
    available_vouchers_count = 0

    cart = session.get("cart", {})
    cart_item_count = (
        sum(int(quantity) for quantity in cart.values() if str(quantity).isdigit())
        if isinstance(cart, dict) else 0
    )
    if user and user.get("role") == "customer":
        try:
            result = query_one(
                "SELECT COUNT(*) AS count FROM notifications WHERE user_id=%s AND is_read=0",
                (user["id"],),
            )
            unread_notifications = result["count"] if result else 0
        except Exception:
            unread_notifications = 0

        try:
            chat_result = query_one(
                """SELECT COUNT(*) AS count FROM chat_messages m
                   JOIN chat_conversations c ON c.id=m.conversation_id
                   WHERE c.user_id=%s AND m.sender_role != 'customer' AND m.is_read=0""",
                (user["id"],),
            )
            unread_chat_count = chat_result["count"] if chat_result else 0
        except Exception:
            unread_chat_count = 0

        try:
            orders_res = query_one(
                """SELECT COUNT(*) AS count FROM orders
                   WHERE user_id=%s AND order_status IN ('Pending', 'Confirmed', 'Preparing', 'Ready for Delivery', 'Ready for Pickup', 'Out for Delivery')""",
                (user["id"],),
            )
            custom_res = query_one(
                """SELECT COUNT(*) AS count FROM custom_orders
                   WHERE user_id=%s AND status IN ('Pending', 'Approved', 'Preparing', 'Ready for Delivery', 'Ready for Pickup', 'Out for Delivery')""",
                (user["id"],),
            )
            active_orders_count = (orders_res["count"] if orders_res else 0) + (custom_res["count"] if custom_res else 0)
        except Exception:
            active_orders_count = 0

        try:
            wishlist = session.get("wishlist", [])
            wishlist_count = len(wishlist) if isinstance(wishlist, list) else 0
        except Exception:
            wishlist_count = 0

        try:
            vouchers_res = query_one(
                "SELECT COUNT(*) AS count FROM vouchers WHERE is_active=1 AND (expires_at IS NULL OR expires_at >= NOW())"
            )
            available_vouchers_count = vouchers_res["count"] if vouchers_res else 0
        except Exception:
            available_vouchers_count = 0

        try:
            feedback_res = query_one(
                """SELECT COUNT(*) AS count FROM orders o
                   JOIN order_items oi ON oi.order_id=o.id
                   WHERE o.user_id=%s AND o.order_status='Delivered' AND oi.product_id IS NOT NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM feedback f
                       WHERE f.order_id=o.id AND f.product_id=oi.product_id
                     )""",
                (user["id"],),
            )
            pending_feedback_count = feedback_res["count"] if feedback_res else 0
        except Exception:
            pending_feedback_count = 0
    elif user and user.get("role") == "admin":
        ensure_customer_verification_schema()
        ensure_feedback_product_schema()
        ensure_chat_schema()
        ensure_gcash_schema()
        ensure_custom_materials_schema()

        try:
            result = query_one(
                "SELECT COUNT(*) AS count FROM notifications WHERE user_id=%s AND is_read=0",
                (user["id"],),
            )
            admin_unread_notifications = result["count"] if result else 0
        except Exception:
            admin_unread_notifications = 0

        try:
            orders_res = query_one(
                """SELECT (
                     (SELECT COUNT(*) FROM orders WHERE order_status='Pending')
                     +
                     (SELECT COUNT(*) FROM custom_orders WHERE status='Pending')
                   ) AS count"""
            )
            notif_orders_res = query_one(
                """SELECT COUNT(*) AS count FROM notifications
                   WHERE user_id=%s AND title='New order received' AND is_read=0""",
                (user["id"],),
            )
            pending_orders = orders_res["count"] if orders_res else 0
            notif_orders = notif_orders_res["count"] if notif_orders_res else 0
            admin_new_order_count = max(pending_orders, notif_orders)
        except Exception:
            admin_new_order_count = 0

        try:
            fb_res = query_one(
                "SELECT COUNT(*) AS count FROM feedback WHERE admin_checked_at IS NULL"
            )
            admin_new_feedback_count = fb_res["count"] if fb_res else 0
        except Exception:
            admin_new_feedback_count = 0

        try:
            ver_res = query_one(
                """SELECT COUNT(*) AS count FROM users
                   WHERE role='customer' AND verification_status='Pending'"""
            )
            pending_verification_count = ver_res["count"] if ver_res else 0
        except Exception:
            pending_verification_count = 0

        try:
            chat_res = query_one(
                "SELECT COUNT(*) AS count FROM chat_messages WHERE sender_role='customer' AND is_read=0"
            )
            admin_unread_chat_count = chat_res["count"] if chat_res else 0
        except Exception:
            admin_unread_chat_count = 0

        try:
            pay_res = query_one(
                """SELECT
                     (SELECT COUNT(*) FROM orders
                      WHERE payment_method='GCash' AND payment_status='Pending'
                        AND payment_proof_filename IS NOT NULL AND order_status='Pending')
                     +
                     (SELECT COUNT(*) FROM custom_orders
                      WHERE payment_method='GCash' AND payment_status='Pending'
                        AND payment_proof_filename IS NOT NULL AND status='Pending')
                     AS count"""
            )
            admin_pending_payments_count = pay_res["count"] if pay_res else 0
        except Exception:
            admin_pending_payments_count = 0

        try:
            prod_res = query_one(
                "SELECT COUNT(*) AS count FROM products WHERE is_active=1 AND stock <= 5"
            )
            admin_low_stock_products_count = prod_res["count"] if prod_res else 0
        except Exception:
            admin_low_stock_products_count = 0

        try:
            mat_res = query_one(
                "SELECT COUNT(*) AS count FROM custom_materials WHERE is_active=1 AND stock <= low_stock_threshold"
            )
            admin_low_stock_materials_count = mat_res["count"] if mat_res else 0
        except Exception:
            admin_low_stock_materials_count = 0
    return {
        "current_user": user,
        "csrf_token": csrf_token,
        "unread_notifications": unread_notifications,
        "admin_unread_notifications": admin_unread_notifications,
        "admin_new_order_count": admin_new_order_count,
        "admin_new_feedback_count": admin_new_feedback_count,
        "pending_verification_count": pending_verification_count,
        "admin_unread_chat_count": admin_unread_chat_count,
        "admin_pending_payments_count": admin_pending_payments_count,
        "admin_low_stock_products_count": admin_low_stock_products_count,
        "admin_low_stock_materials_count": admin_low_stock_materials_count,
        "cart_item_count": cart_item_count,
        "unread_chat_count": unread_chat_count,
        "active_orders_count": active_orders_count,
        "pending_feedback_count": pending_feedback_count,
        "wishlist_count": wishlist_count,
        "available_vouchers_count": available_vouchers_count,
    }


def inject_development_reload():
    if not current_app.config.get("DEV_AUTO_RELOAD"):
        return {}
    return {"dev_reload_version": development_source_version()}


def development_live_reload():
    if not current_app.config.get("DEV_AUTO_RELOAD"):
        abort(404)
    response = jsonify(version=development_source_version())
    response.headers["Cache-Control"] = "no-store"
    return response


def protect_post_forms():
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        expected = session.get("_csrf")
        if not sent or not expected or sent != expected:
            abort(400, description="Invalid or missing CSRF token. Refresh the page and try again.")


def validate_session():
    stored_user = session.get("user")
    if not stored_user:
        return None
    if not isinstance(stored_user, dict):
        session.clear()
        flash("Your session is invalid. Please log in again.", "warning")
        return redirect(url_for("login"))
    if not session.get("auth_user_id"):
        if current_app.testing:
            return None
        session.clear()
        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for("login"))
    if session.get("auth_user_id") != stored_user.get("id"):
        session.clear()
        flash("Your session is invalid. Please log in again.", "warning")
        return redirect(url_for("login"))

    ensure_customer_verification_schema()
    user = query_one(
        """SELECT id,full_name,email,role,phone,customer_id,verification_status,
                  email_verified,account_status,verification_rejection_reason
           FROM users WHERE id=%s""",
        (session["auth_user_id"],),
    )
    if not user or str(user.get("role") or "").strip().lower() != stored_user.get("role"):
        session.clear()
        flash("Your session is no longer valid. Please log in again.", "warning")
        return redirect(url_for("login"))
    user["role"] = str(user["role"]).strip().lower()
    user["email_verified"] = bool(user.get("email_verified", True))
    session["user"] = user
    session.permanent = True
    if user["role"] == "customer" and user.get("account_status") == "Deactivated":
        session.clear()
        flash("Your account is deactivated. Contact the shop for assistance.", "danger")
        return redirect(url_for("login"))
    return None


def forbidden(_error):
    g.rendering_error_page = True
    return render_template("error.html", code=403, message="You do not have permission to view this page."), 403


def not_found(_error):
    g.rendering_error_page = True
    return render_template("error.html", code=404, message="The page you’re looking for could not be found."), 404


def bad_request(error):
    g.rendering_error_page = True
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request.")), 400


def internal_server_error(_error):
    g.rendering_error_page = True
    return render_template(
        "error.html",
        code=500,
        message="Something went wrong on our end. Please try again in a little while.",
    ), 500


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.context_processor(inject_globals)
    app.context_processor(inject_development_reload)
    app.before_request(validate_session)
    app.before_request(protect_post_forms)
    app.add_url_rule(
        "/__dev/live-reload",
        endpoint="development_live_reload",
        view_func=development_live_reload,
    )

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.pop("SourceMap", None)
        response.headers.pop("X-SourceMap", None)
        if app.config.get("DEV_AUTO_RELOAD"):
            response.headers["Cache-Control"] = "no-store"
            app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
        return response

    @app.before_request
    def block_static_directories():
        if request.path in ("/static/", "/static/uploads/", "/static/css/", "/static/js/"):
            abort(403)

    register_routes(app)
    app.register_error_handler(403, forbidden)
    app.register_error_handler(404, not_found)
    app.register_error_handler(400, bad_request)
    app.register_error_handler(500, internal_server_error)
    return app


app = create_app()
