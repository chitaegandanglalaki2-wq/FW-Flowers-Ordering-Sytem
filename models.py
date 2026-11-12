from datetime import datetime
from decimal import Decimal
from flask import url_for, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from database import db, execute, query_all, query_one
from services.common import (
    calculate_voucher_discount,
    delivery_fee_for_address,
    ensure_customer_profile_schema,
    ensure_customer_verification_schema,
    ensure_delivery_slots_schema,
    ensure_feedback_product_schema,
    ensure_gift_message_schema,
    ensure_manual_order_schema,
    ensure_notification_link_schema,
    ensure_order_queue_schema,
    ensure_product_occasion_schema,
    ensure_promo_schema,
)


class User:
    @staticmethod
    def create(
        full_name, email, password, role="customer", customer_id=None,
        delivery_address=None, address_fields=None, verification_images=None,
    ):
        ensure_customer_verification_schema()
        if delivery_address:
            ensure_customer_profile_schema()
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT GET_LOCK('fuzzywire_registration_queue_create',10)")
            queue_lock = cur.fetchone()
            if not queue_lock or queue_lock[0] != 1:
                raise ValueError("The registration queue is busy. Please submit again shortly.")
            account_values = (
                full_name,
                email.lower(),
                customer_id,
                "Pending" if role == "customer" else "Approved",
                0 if role == "customer" else 1,
                "Pending" if role == "customer" else "Active",
                None,
                generate_password_hash(password),
                role,
            )
            verification_images = verification_images or {}
            if delivery_address:
                address_fields = address_fields or {}
                cur.execute(
                    """INSERT INTO users
                       (full_name,email,customer_id,verification_status,email_verified,account_status,
                        verification_rejection_reason,password_hash,role,
                        verification_id_front_filename,verification_id_back_filename,
                        verification_selfie_filename,
                        delivery_address,delivery_region_code,delivery_province_code,
                        delivery_city_code,delivery_barangay_code,delivery_barangay,
                        delivery_postal_code,delivery_street_address)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    account_values + (
                        verification_images.get("front"),
                        verification_images.get("back"),
                        verification_images.get("selfie"),
                        delivery_address,
                        address_fields.get("delivery_region_code"),
                        address_fields.get("delivery_province_code"),
                        address_fields.get("delivery_city_code"),
                        address_fields.get("delivery_barangay_code"),
                        address_fields.get("delivery_barangay"),
                        address_fields.get("delivery_postal_code"),
                        address_fields.get("delivery_street_address"),
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO users
                       (full_name,email,customer_id,verification_status,email_verified,account_status,
                        verification_rejection_reason,password_hash,role,
                        verification_id_front_filename,verification_id_back_filename,
                        verification_selfie_filename)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    account_values + (
                        verification_images.get("front"),
                        verification_images.get("back"),
                        verification_images.get("selfie"),
                    ),
                )
            conn.commit()
            return cur.lastrowid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def authenticate(email, password):
        ensure_customer_verification_schema()
        email = (email or "").strip().lower()
        user = query_one("SELECT * FROM users WHERE LOWER(email)=%s", (email,))
        if not user:
            current_app.logger.warning("Login failed: no matching account.")
            return None

        stored_hash = user.get("password_hash") or ""
        try:
            password_ok = check_password_hash(stored_hash, password or "")
        except (ValueError, TypeError):
            password_ok = False

        if not password_ok:
            current_app.logger.warning("Login failed: password verification did not match.")
            return None

        # Normalize the role so capitalization or accidental spaces won't block admin access.
        return {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "role": str(user.get("role") or "customer").strip().lower(),
            "phone": user.get("phone"),
            "customer_id": user.get("customer_id"),
            "verification_status": user.get("verification_status", "Approved"),
            "email_verified": bool(user.get("email_verified", True)),
            "account_status": user.get("account_status", "Active"),
            "verification_rejection_reason": user.get("verification_rejection_reason"),
        }

    @staticmethod
    def by_id(user_id):
        ensure_customer_verification_schema()
        user = query_one("SELECT * FROM users WHERE id=%s", (user_id,))
        if not user:
            return None
        return {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "role": str(user.get("role") or "customer").strip().lower(),
            "phone": user.get("phone"),
            "customer_id": user.get("customer_id"),
            "verification_status": user.get("verification_status", "Approved"),
            "email_verified": bool(user.get("email_verified", True)),
            "account_status": user.get("account_status", "Active"),
            "verification_rejection_reason": user.get("verification_rejection_reason"),
        }


class Product:
    @staticmethod
    def all(active_only=True):
        ensure_product_occasion_schema()
        ensure_feedback_product_schema()
        sql = """SELECT p.*,
                   (SELECT COALESCE(SUM(oi.quantity),0)
                    FROM order_items oi JOIN orders o ON o.id=oi.order_id
                    WHERE oi.product_id=p.id AND o.order_status='Delivered') AS total_sold,
                   (SELECT COALESCE(AVG(f.rating),0)
                    FROM feedback f WHERE f.product_id=p.id) AS avg_rating,
                   (SELECT COUNT(*)
                    FROM feedback f WHERE f.product_id=p.id) AS rating_count
                 FROM products p"""
        if active_only:
            sql += " WHERE p.is_active=1"
        sql += " ORDER BY p.created_at DESC"
        return query_all(sql)

    @staticmethod
    def get(product_id):
        ensure_product_occasion_schema()
        return query_one("SELECT * FROM products WHERE id=%s", (product_id,))

    @staticmethod
    def create(data):
        ensure_product_occasion_schema()
        return execute("""INSERT INTO products
          (name,description,flower_type,color,occasion,price,stock,image_filename,is_active)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)""",
          (data["name"], data["description"], data["flower_type"], data["color"],
           data["occasion"], data["price"], data["stock"], data["image_filename"]))

    @staticmethod
    def update(product_id, data):
        ensure_product_occasion_schema()
        execute("""UPDATE products
                   SET name=%s,description=%s,flower_type=%s,color=%s,occasion=%s,price=%s,
                       stock=%s,image_filename=%s,is_active=%s
                   WHERE id=%s""",
                (data["name"], data["description"], data["flower_type"], data["color"],
                 data["occasion"], data["price"], data["stock"], data["image_filename"], data["is_active"],
                 product_id))

    @staticmethod
    def update_stock(product_id, quantity):
        execute("UPDATE products SET stock=stock-%s WHERE id=%s AND stock >= %s",
                (quantity, product_id, quantity))


class Voucher:
    @staticmethod
    def claim(voucher_id, user_id):
        ensure_promo_schema()
        conn = db()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT * FROM promo_vouchers WHERE id=%s FOR UPDATE",
                (voucher_id,),
            )
            voucher = cur.fetchone()
            if (
                not voucher
                or not voucher["is_active"]
                or (voucher["expires_at"] and voucher["expires_at"] <= datetime.now())
            ):
                raise ValueError("This voucher is no longer available.")
            if (
                voucher["max_uses"] is not None
                and voucher["used_count"] >= voucher["max_uses"]
            ):
                raise ValueError("This voucher has already been fully used.")

            cur.execute(
                "SELECT COUNT(*) AS count FROM voucher_claims WHERE voucher_id=%s",
                (voucher_id,),
            )
            claimed_count = cur.fetchone()["count"]
            if (
                voucher["max_claims"] is not None
                and claimed_count >= voucher["max_claims"]
            ):
                raise ValueError("This voucher has already been fully claimed.")

            cur.execute(
                "SELECT id FROM voucher_claims WHERE voucher_id=%s AND user_id=%s",
                (voucher_id, user_id),
            )
            if cur.fetchone():
                raise ValueError("You have already claimed this voucher.")

            cur.execute(
                "INSERT INTO voucher_claims (voucher_id,user_id) VALUES (%s,%s)",
                (voucher_id, user_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def update(voucher_id, data):
        ensure_promo_schema()
        conn = db()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT id,is_active,used_count FROM promo_vouchers WHERE id=%s FOR UPDATE",
                (voucher_id,),
            )
            voucher = cur.fetchone()
            if not voucher:
                conn.commit()
                return None

            cur.execute(
                "SELECT COUNT(*) AS count FROM voucher_claims WHERE voucher_id=%s",
                (voucher_id,),
            )
            claimed_count = cur.fetchone()["count"]
            cur.execute(
                "SELECT COUNT(*) AS count FROM voucher_redemptions WHERE voucher_id=%s",
                (voucher_id,),
            )
            redeemed_count = cur.fetchone()["count"]
            max_claims = data["max_claims"]
            if max_claims is not None and max_claims < max(
                claimed_count, redeemed_count, voucher["used_count"]
            ):
                raise ValueError(
                    "Available quantity cannot be lower than the number already claimed or used."
                )

            cur.execute(
                """UPDATE promo_vouchers SET name=%s,code=%s,discount_type=%s,
                   discount_value=%s,minimum_subtotal=%s,max_claims=%s,max_uses=%s,
                   expires_at=%s,is_active=%s WHERE id=%s""",
                (
                    data["name"],
                    data["code"],
                    data["discount_type"],
                    data["discount_value"],
                    data["minimum_subtotal"],
                    max_claims,
                    max_claims,
                    data["expires_at"],
                    data["is_active"],
                    voucher_id,
                ),
            )
            conn.commit()
            return bool(voucher["is_active"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def delete_if_unused(voucher_id):
        ensure_promo_schema()
        conn = db()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT id,code,used_count FROM promo_vouchers WHERE id=%s FOR UPDATE",
                (voucher_id,),
            )
            voucher = cur.fetchone()
            if not voucher:
                conn.commit()
                return None
            for table in ("voucher_claims", "voucher_redemptions"):
                cur.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE voucher_id=%s",
                    (voucher_id,),
                )
                if cur.fetchone()["count"]:
                    raise ValueError(
                        "This voucher has customer activity. Deactivate it instead of deleting it."
                    )
            if voucher["used_count"]:
                raise ValueError(
                    "This voucher has customer activity. Deactivate it instead of deleting it."
                )
            cur.execute("DELETE FROM promo_vouchers WHERE id=%s", (voucher_id,))
            conn.commit()
            return voucher["code"]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class Order:
    @staticmethod
    def create_manual(data):
        ensure_manual_order_schema()
        ensure_order_queue_schema()
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO orders
                   (user_id,customer_name,customer_email,phone,fulfillment_method,
                    delivery_address,delivery_notes,delivery_distance_km,subtotal,
                    delivery_fee,discount_amount,total,payment_method,payment_status,
                    order_status,created_at)
                   VALUES (NULL,%s,'',%s,'Delivery',%s,%s,0,%s,%s,0,%s,
                           'Cash on Delivery',%s,%s,%s)""",
                (
                    data["customer_name"],
                    data["phone"],
                    data["delivery_address"],
                    data["notes"] or None,
                    data["subtotal"],
                    data["delivery_fee"],
                    data["total"],
                    data["payment_status"],
                    data["order_status"],
                    data["order_date"],
                ),
            )
            order_id = cur.lastrowid
            cur.execute(
                """INSERT INTO order_items
                   (order_id,product_id,item_name,flower_type,color,quantity,
                    unit_price,line_total)
                   VALUES (%s,NULL,%s,%s,'N/A',%s,%s,%s)""",
                (
                    order_id,
                    data["product_name"],
                    data["product_name"][:80],
                    data["quantity"],
                    data["unit_price"],
                    data["subtotal"],
                ),
            )
            cur.execute(
                """INSERT INTO order_queue (order_type,order_id,created_at)
                   VALUES ('Order',%s,NOW())""",
                (order_id,),
            )
            cur.execute(
                """INSERT INTO deliveries (order_id,delivery_status)
                   VALUES (%s,%s)""",
                (order_id, data["order_status"]),
            )
            conn.commit()
            return order_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def create(user, cart, form):
        ensure_gift_message_schema()
        ensure_promo_schema()
        ensure_delivery_slots_schema()
        ensure_order_queue_schema()
        products = []
        subtotal = Decimal("0.00")
        for pid_text, qty_text in cart.items():
            try:
                pid, qty = int(pid_text), int(qty_text)
            except ValueError:
                raise ValueError("Invalid cart item.")
            if qty < 1 or qty > 50:
                raise ValueError("Quantity must be between 1 and 50.")
            product = Product.get(pid)
            if not product or not product["is_active"] or product["stock"] < qty:
                raise ValueError("A product is unavailable or has insufficient stock. Please update your cart.")
            line = Decimal(str(product["price"])) * qty
            subtotal += line
            products.append((product, qty, line))
        if not products:
            raise ValueError("Your cart is empty.")
        fulfillment_method = form["fulfillment_method"]
        delivery_distance = form["delivery_distance_km"]
        delivery_fee = delivery_fee_for_address(
            form.get("delivery_address"),
            form.get("delivery_city_code"),
            fulfillment_method,
        )
        raw_slot = (form.get("delivery_slot_id") or "").strip()
        schedule_pref = None
        slot_id = None
        if raw_slot in ("Weekdays", "Weekends"):
            schedule_pref = raw_slot
            delivery_notes = form.get("delivery_notes", "") or ""
            if schedule_pref not in delivery_notes:
                form["delivery_notes"] = f"Schedule preference: {schedule_pref}. {delivery_notes}".strip()
        elif raw_slot:
            try:
                slot_id = int(raw_slot)
            except (ValueError, TypeError):
                slot_id = None
        ensure_notification_link_schema()
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT GET_LOCK('fuzzywire_order_queue_create',10)")
            queue_lock = cur.fetchone()
            if not queue_lock or queue_lock[0] != 1:
                raise ValueError("The order queue is busy. Please submit your order again shortly.")
            if slot_id:
                slot_cur = conn.cursor(dictionary=True)
                slot_cur.execute(
                    "SELECT * FROM delivery_slots WHERE id=%s FOR UPDATE",
                    (slot_id,),
                )
                slot = slot_cur.fetchone()
                if (not slot or not slot["is_active"]
                        or slot["fulfillment_method"] != fulfillment_method
                        or slot["starts_at"] <= datetime.now()):
                    raise ValueError("Choose a valid future delivery or pickup time slot.")
                slot_cur.execute(
                    """SELECT COUNT(*) AS reserved FROM orders
                       WHERE delivery_slot_id=%s AND order_status <> 'Cancelled'""",
                    (slot_id,),
                )
                if slot_cur.fetchone()["reserved"] >= slot["capacity"]:
                    raise ValueError("That delivery or pickup time slot is full. Choose another time.")
            voucher = None
            promo_code = form.get("promo_code", "").strip().upper()
            discount = Decimal("0.00")
            if promo_code:
                voucher_cur = conn.cursor(dictionary=True)
                voucher_cur.execute(
                    "SELECT * FROM promo_vouchers WHERE code=%s FOR UPDATE",
                    (promo_code,),
                )
                voucher = voucher_cur.fetchone()
                if not voucher:
                    raise ValueError("That promo code is invalid or inactive.")
                voucher_cur.execute(
                    """SELECT id FROM voucher_claims
                       WHERE voucher_id=%s AND user_id=%s LIMIT 1""",
                    (voucher["id"], user["id"]),
                )
                if not voucher_cur.fetchone():
                    raise ValueError("Claim this voucher before using its code.")
                voucher_cur.execute(
                    "SELECT COUNT(*) AS count FROM voucher_redemptions WHERE voucher_id=%s AND user_id=%s",
                    (voucher["id"], user["id"]),
                )
                discount = calculate_voucher_discount(
                    voucher, subtotal, bool(voucher_cur.fetchone()["count"])
                )
            total = subtotal + delivery_fee - discount
            cur.execute("""INSERT INTO orders
              (user_id,customer_name,customer_email,phone,fulfillment_method,
               delivery_address,delivery_notes,gift_message,delivery_distance_km,subtotal,
               delivery_fee,promo_code,discount_amount,delivery_slot_id,schedule_preference,total,payment_method,payment_status)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (user["id"], form["customer_name"], user["email"], form["phone"],
               fulfillment_method, form["delivery_address"], form["delivery_notes"],
               form["gift_message"], delivery_distance,
               subtotal, delivery_fee, promo_code or None, discount, slot_id, schedule_pref, total, form["payment_method"],
               "Pending" if form["payment_method"] == "GCash" else "Unpaid"))
            order_id = cur.lastrowid
            cur.execute(
                """INSERT INTO order_queue (order_type,order_id,created_at)
                   SELECT 'Order',id,created_at FROM orders WHERE id=%s""",
                (order_id,),
            )
            if voucher:
                cur.execute(
                    """INSERT INTO voucher_redemptions
                       (voucher_id,user_id,order_id,discount_amount) VALUES (%s,%s,%s,%s)""",
                    (voucher["id"], user["id"], order_id, discount),
                )
                cur.execute(
                    "UPDATE promo_vouchers SET used_count=used_count+1 WHERE id=%s",
                    (voucher["id"],),
                )
            for product, qty, line in products:
                cur.execute("""INSERT INTO order_items
                  (order_id,product_id,item_name,flower_type,color,quantity,unit_price,line_total)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (order_id, product["id"], product["name"], product["flower_type"],
                   product["color"], qty, product["price"], line))
                cur.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (qty, product["id"]))
            cur.execute("INSERT INTO deliveries (order_id,delivery_status) VALUES (%s,'Pending')", (order_id,))
            cur.execute(
                "INSERT INTO notifications (user_id,title,message,link_url) VALUES (%s,%s,%s,%s)",
                (user["id"], "Order placed", f"Your order #{order_id} has been submitted.",
                 url_for("order_detail", order_id=order_id)),
            )
            cur.execute(
                """INSERT INTO notifications (user_id,title,message,link_url)
                   SELECT id,'New order received',%s,%s FROM users WHERE role='admin'""",
                (
                    f"A new bouquet order #{order_id} is pending.",
                    url_for("admin_order_detail", order_id=order_id),
                ),
            )
            conn.commit()
            return order_id, total
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
