import os
from pathlib import Path
import mysql.connector
from flask import current_app
from config import PAYMENT_PROOF_DIR, UPLOAD_DIR
from database import db, execute, query_all, query_one

_delivery_distance_schema_ready = False


_customer_cart_schema_ready = False


_wishlist_schema_ready = False


_product_occasion_schema_ready = False


_gift_message_schema_ready = False


_promo_schema_ready = False


_delivery_slots_schema_ready = False


_admin_operations_schema_ready = False


_fulfillment_schema_ready = False


_customer_delivery_confirmation_schema_ready = False


_custom_payment_schema_ready = False


_chat_schema_ready = False


_notification_link_schema_ready = False


_customer_profile_schema_ready = False


_order_messaging_schema_ready = False


_password_reset_schema_ready = False


_historical_sales_schema_ready = False


_custom_materials_schema_ready = False


_customer_verification_schema_ready = False


_order_queue_schema_ready = False


_delivery_map_schema_ready = False


_gcash_schema_ready = False


_feedback_product_schema_ready = False


_gcash_settings_schema_ready = False


_manual_order_schema_ready = False


def ensure_admin_operations_schema():
    global _admin_operations_schema_ready
    if _admin_operations_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS order_admin_notes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_type ENUM('Order','Custom') NOT NULL,
        order_id INT NOT NULL,
        admin_id INT NULL,
        note TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_order_admin_notes_target (order_type,order_id),
        CONSTRAINT fk_order_admin_notes_admin FOREIGN KEY (admin_id)
            REFERENCES users(id) ON DELETE SET NULL
    )""")
    execute("""CREATE TABLE IF NOT EXISTS order_refunds (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_id INT NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        reason VARCHAR(1000) NOT NULL,
        status ENUM('Recorded','Completed') NOT NULL DEFAULT 'Recorded',
        created_by INT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_order_refunds_order (order_id),
        CONSTRAINT fk_order_refunds_order FOREIGN KEY (order_id)
            REFERENCES orders(id) ON DELETE CASCADE,
        CONSTRAINT fk_order_refunds_admin FOREIGN KEY (created_by)
            REFERENCES users(id) ON DELETE SET NULL
    )""")
    execute("""CREATE TABLE IF NOT EXISTS admin_activity_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        admin_id INT NULL,
        action VARCHAR(80) NOT NULL,
        target_type VARCHAR(40) NOT NULL,
        target_id VARCHAR(80) NULL,
        details VARCHAR(1000) NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_admin_activity_created (created_at),
        CONSTRAINT fk_admin_activity_user FOREIGN KEY (admin_id)
            REFERENCES users(id) ON DELETE SET NULL
    )""")
    present = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'
             AND COLUMN_NAME='admin_tags'"""
    )
    if not present["present"]:
        execute("ALTER TABLE users ADD COLUMN admin_tags VARCHAR(500) NULL")
    _admin_operations_schema_ready = True


def ensure_delivery_slots_schema():
    global _delivery_slots_schema_ready
    if _delivery_slots_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS delivery_slots (
        id INT AUTO_INCREMENT PRIMARY KEY,
        starts_at DATETIME NOT NULL,
        fulfillment_method ENUM('Delivery','Pickup') NOT NULL,
        capacity INT NOT NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_delivery_slot_start_method (starts_at,fulfillment_method)
    )""")
    present = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
             AND COLUMN_NAME='delivery_slot_id'"""
    )
    if not present["present"]:
        execute("ALTER TABLE orders ADD COLUMN delivery_slot_id INT NULL")
        execute(
            """ALTER TABLE orders ADD CONSTRAINT fk_orders_delivery_slot
               FOREIGN KEY (delivery_slot_id) REFERENCES delivery_slots(id) ON DELETE SET NULL"""
        )
    custom_slot = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='delivery_slot_id'"""
    )
    custom_constraint = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.KEY_COLUMN_USAGE
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='delivery_slot_id'
             AND REFERENCED_TABLE_NAME='delivery_slots'"""
    )
    if custom_slot["present"] and not custom_constraint["present"]:
        execute(
            """ALTER TABLE custom_orders ADD CONSTRAINT fk_custom_delivery_slot
               FOREIGN KEY (delivery_slot_id)
               REFERENCES delivery_slots(id) ON DELETE SET NULL"""
        )
    for table in ("orders", "custom_orders"):
        pref_col = query_one(
            """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME='schedule_preference'""",
            (table,),
        )
        if not pref_col or not pref_col["present"]:
            execute(f"ALTER TABLE `{table}` ADD COLUMN `schedule_preference` VARCHAR(50) NULL DEFAULT NULL")
    _delivery_slots_schema_ready = True


def ensure_promo_schema():
    global _promo_schema_ready
    if _promo_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS promo_vouchers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(120) NOT NULL DEFAULT '',
        code VARCHAR(40) NOT NULL UNIQUE,
        discount_type ENUM('Percent','Fixed') NOT NULL,
        discount_value DECIMAL(10,2) NOT NULL,
        minimum_subtotal DECIMAL(10,2) NOT NULL DEFAULT 0.00,
        max_claims INT NULL,
        max_uses INT NULL,
        used_count INT NOT NULL DEFAULT 0,
        expires_at DATETIME NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    columns = {
        row["COLUMN_NAME"]
        for row in query_all(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='promo_vouchers'"""
        )
    }
    if "name" not in columns:
        execute(
            "ALTER TABLE promo_vouchers ADD COLUMN name VARCHAR(120) NOT NULL DEFAULT '' AFTER id"
        )
    if "max_claims" not in columns:
        execute(
            "ALTER TABLE promo_vouchers ADD COLUMN max_claims INT NULL AFTER minimum_subtotal"
        )
    execute("UPDATE promo_vouchers SET name=code WHERE name=''")
    execute(
        "UPDATE promo_vouchers SET max_claims=max_uses WHERE max_claims IS NULL AND max_uses IS NOT NULL"
    )
    execute("""CREATE TABLE IF NOT EXISTS voucher_claims (
        id INT AUTO_INCREMENT PRIMARY KEY,
        voucher_id INT NOT NULL,
        user_id INT NOT NULL,
        claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_voucher_claim_user (voucher_id,user_id),
        CONSTRAINT fk_voucher_claim_voucher FOREIGN KEY (voucher_id)
            REFERENCES promo_vouchers(id) ON DELETE CASCADE,
        CONSTRAINT fk_voucher_claim_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE
    )""")
    execute("""CREATE TABLE IF NOT EXISTS voucher_redemptions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        voucher_id INT NOT NULL,
        user_id INT NOT NULL,
        order_id INT NOT NULL,
        discount_amount DECIMAL(10,2) NOT NULL,
        redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_voucher_redemption_order (order_id),
        CONSTRAINT fk_voucher_redemption_voucher FOREIGN KEY (voucher_id)
            REFERENCES promo_vouchers(id),
        CONSTRAINT fk_voucher_redemption_user FOREIGN KEY (user_id)
            REFERENCES users(id),
        CONSTRAINT fk_voucher_redemption_order FOREIGN KEY (order_id)
            REFERENCES orders(id) ON DELETE CASCADE
    )""")
    for column, definition in (
        ("promo_code", "VARCHAR(40) NULL"),
        ("discount_amount", "DECIMAL(10,2) NOT NULL DEFAULT 0.00"),
    ):
        present = query_one(
            """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
                 AND COLUMN_NAME=%s""",
            (column,),
        )
        if not present["present"]:
            execute(f"ALTER TABLE orders ADD COLUMN `{column}` {definition}")
    _promo_schema_ready = True


def ensure_gift_message_schema():
    global _gift_message_schema_ready
    if _gift_message_schema_ready:
        return
    present = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
             AND COLUMN_NAME='gift_message'"""
    )
    if not present["present"]:
        execute("ALTER TABLE orders ADD COLUMN gift_message VARCHAR(500) NULL")
    _gift_message_schema_ready = True


def ensure_product_occasion_schema():
    global _product_occasion_schema_ready
    if _product_occasion_schema_ready:
        return
    present = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='products'
             AND COLUMN_NAME='occasion'"""
    )
    if not present["present"]:
        execute(
            "ALTER TABLE products ADD COLUMN occasion VARCHAR(60) NOT NULL DEFAULT 'Any occasion'"
        )
    _product_occasion_schema_ready = True


def ensure_wishlist_schema():
    global _wishlist_schema_ready
    if _wishlist_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS wishlists (
        user_id INT NOT NULL,
        product_id INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id,product_id),
        CONSTRAINT fk_wishlist_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_wishlist_product FOREIGN KEY (product_id)
            REFERENCES products(id) ON DELETE CASCADE
    )""")
    _wishlist_schema_ready = True


def ensure_customer_cart_schema():
    global _customer_cart_schema_ready
    if _customer_cart_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS customer_cart_items (
        user_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        PRIMARY KEY (user_id, product_id),
        CONSTRAINT fk_customer_cart_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE
    )""")
    _customer_cart_schema_ready = True


def ensure_delivery_distance_schema():
    global _delivery_distance_schema_ready
    if _delivery_distance_schema_ready:
        return
    column = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
             AND COLUMN_NAME='delivery_distance_km'"""
    )
    if not column["present"]:
        execute(
            "ALTER TABLE orders ADD COLUMN delivery_distance_km DECIMAL(7,2) NOT NULL DEFAULT 0.00 AFTER delivery_fee"
        )
    _delivery_distance_schema_ready = True


def ensure_fulfillment_schema():
    global _fulfillment_schema_ready
    if _fulfillment_schema_ready:
        return
    column = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
             AND COLUMN_NAME='fulfillment_method'"""
    )
    if not column["present"]:
        execute(
            """ALTER TABLE orders ADD COLUMN fulfillment_method
               ENUM('Delivery','Pickup') NOT NULL DEFAULT 'Delivery' AFTER phone"""
        )
    _fulfillment_schema_ready = True


def ensure_password_reset_schema():
    global _password_reset_schema_ready
    if _password_reset_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        token_hash CHAR(64) NOT NULL UNIQUE,
        expires_at DATETIME NOT NULL,
        used_at DATETIME NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_password_reset_user (user_id, used_at, expires_at),
        CONSTRAINT fk_password_reset_user FOREIGN KEY (user_id)
          REFERENCES users(id) ON DELETE CASCADE
    )""")
    _password_reset_schema_ready = True


def ensure_historical_sales_schema():
    global _historical_sales_schema_ready
    if _historical_sales_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS historical_sales (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_date DATE NOT NULL,
        customer_name VARCHAR(120) NULL,
        customer_contact VARCHAR(30) NULL,
        total DECIMAL(10,2) NOT NULL,
        payment_status ENUM('Paid','Unpaid') NOT NULL DEFAULT 'Paid',
        created_by INT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_historical_sales_date (order_date),
        CONSTRAINT fk_historical_sales_admin FOREIGN KEY (created_by)
          REFERENCES users(id) ON DELETE SET NULL
    )""")
    execute("""CREATE TABLE IF NOT EXISTS historical_sale_items (
        id INT AUTO_INCREMENT PRIMARY KEY,
        historical_sale_id INT NOT NULL,
        item_name VARCHAR(160) NOT NULL,
        quantity INT NOT NULL,
        unit_price DECIMAL(10,2) NOT NULL,
        line_total DECIMAL(10,2) NOT NULL,
        INDEX idx_historical_sale_items_name (item_name),
        CONSTRAINT fk_historical_sale_items_sale FOREIGN KEY (historical_sale_id)
          REFERENCES historical_sales(id) ON DELETE CASCADE
    )""")
    _historical_sales_schema_ready = True


def ensure_gcash_settings_schema():
    global _gcash_settings_schema_ready
    if _gcash_settings_schema_ready:
        return
    execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
             setting_key VARCHAR(60) PRIMARY KEY,
             setting_value VARCHAR(255) NOT NULL,
             updated_by INT NULL,
             updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
               ON UPDATE CURRENT_TIMESTAMP,
             CONSTRAINT fk_app_settings_admin FOREIGN KEY (updated_by)
               REFERENCES users(id) ON DELETE SET NULL
           )"""
    )
    execute(
        """INSERT IGNORE INTO app_settings
           (setting_key,setting_value) VALUES ('gcash_number',%s)""",
        (os.getenv("GCASH_NUMBER", "09XXXXXXXXX").strip(),),
    )
    _gcash_settings_schema_ready = True


def ensure_manual_order_schema():
    global _manual_order_schema_ready
    if _manual_order_schema_ready:
        return
    ensure_historical_sales_schema()
    ensure_order_queue_schema()
    user_id_column = query_one(
        """SELECT IS_NULLABLE AS is_nullable
           FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
             AND COLUMN_NAME='user_id'"""
    )
    if user_id_column and user_id_column["is_nullable"] == "NO":
        execute("ALTER TABLE orders MODIFY user_id INT NULL")
    execute(
        """CREATE TABLE IF NOT EXISTS manual_order_imports (
             source_sale_id INT PRIMARY KEY,
             order_id INT NOT NULL UNIQUE,
             imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
             CONSTRAINT fk_manual_order_import_order FOREIGN KEY (order_id)
               REFERENCES orders(id) ON DELETE CASCADE
           )"""
    )

    conn = db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT GET_LOCK('fuzzywire_manual_order_import',10) AS acquired")
        lock = cursor.fetchone()
        if not lock or lock["acquired"] != 1:
            raise RuntimeError("Could not acquire the manual order import lock.")
        cursor.execute(
            """SELECT h.id,h.order_date,h.customer_name,h.customer_contact,h.total,
                      h.payment_status
               FROM historical_sales h
               LEFT JOIN manual_order_imports i ON i.source_sale_id=h.id
               WHERE i.source_sale_id IS NULL
               ORDER BY h.id"""
        )
        sales = cursor.fetchall()
        for sale in sales:
            cursor.execute(
                """INSERT INTO orders
                   (user_id,customer_name,customer_email,phone,fulfillment_method,
                    delivery_address,delivery_notes,delivery_distance_km,subtotal,
                    delivery_fee,discount_amount,total,payment_method,payment_status,
                    order_status,created_at)
                   VALUES (NULL,%s,'',%s,'Delivery','Manual order entry',NULL,0,
                           %s,0,0,%s,'Cash on Delivery',%s,'Delivered',%s)""",
                (
                    sale["customer_name"] or "Walk-in customer",
                    sale["customer_contact"] or "",
                    sale["total"],
                    sale["total"],
                    sale["payment_status"],
                    sale["order_date"],
                ),
            )
            order_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO order_items
                   (order_id,product_id,item_name,flower_type,color,quantity,
                    unit_price,line_total)
                   SELECT %s,NULL,hi.item_name,LEFT(hi.item_name,80),'N/A',
                          hi.quantity,hi.unit_price,hi.line_total
                   FROM historical_sale_items hi
                   WHERE hi.historical_sale_id=%s""",
                (order_id, sale["id"]),
            )
            cursor.execute(
                "INSERT INTO order_queue (order_type,order_id,created_at) VALUES ('Order',%s,NOW())",
                (order_id,),
            )
            cursor.execute(
                "INSERT INTO deliveries (order_id,delivery_status) VALUES (%s,'Delivered')",
                (order_id,),
            )
            cursor.execute(
                "INSERT INTO manual_order_imports (source_sale_id,order_id) VALUES (%s,%s)",
                (sale["id"], order_id),
            )
        conn.commit()
        cursor.execute("SELECT RELEASE_LOCK('fuzzywire_manual_order_import')")
    except Exception:
        conn.rollback()
        try:
            conn.cursor().execute("SELECT RELEASE_LOCK('fuzzywire_manual_order_import')")
        except mysql.connector.Error:
            current_app.logger.exception("Could not release manual order import lock.")
        raise
    finally:
        conn.close()
    _manual_order_schema_ready = True


def ensure_custom_materials_schema():
    global _custom_materials_schema_ready
    if _custom_materials_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS custom_materials (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        category VARCHAR(60) NOT NULL,
        unit VARCHAR(40) NOT NULL DEFAULT 'piece',
        unit_price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
        stock DECIMAL(10,2) NOT NULL DEFAULT 0.00,
        low_stock_threshold DECIMAL(10,2) NOT NULL DEFAULT 5.00,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_custom_material_name_category (name,category)
    )""")
    category_col = query_one(
        """SELECT DATA_TYPE AS data_type FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_materials'
             AND COLUMN_NAME='category'"""
    )
    if category_col and category_col["data_type"].lower() == "enum":
        execute("ALTER TABLE custom_materials MODIFY `category` VARCHAR(60) NOT NULL")
    for column, default in (("stock", "0.00"), ("low_stock_threshold", "5.00")):
        definition = query_one(
            """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_materials'
                 AND COLUMN_NAME=%s""",
            (column,),
        )
        if definition and not definition["column_type"].lower().startswith("decimal"):
            execute(
                f"ALTER TABLE custom_materials MODIFY `{column}` DECIMAL(10,2) NOT NULL DEFAULT {default}"
            )
    execute("""CREATE TABLE IF NOT EXISTS custom_order_materials (
        id INT AUTO_INCREMENT PRIMARY KEY,
        custom_order_id INT NOT NULL,
        material_id INT NULL,
        material_name VARCHAR(120) NOT NULL,
        category VARCHAR(40) NOT NULL,
        quantity DECIMAL(8,2) NOT NULL,
        unit_price DECIMAL(10,2) NOT NULL,
        line_total DECIMAL(10,2) NOT NULL,
        INDEX idx_custom_order_materials_order (custom_order_id),
        CONSTRAINT fk_custom_order_materials_order FOREIGN KEY (custom_order_id)
          REFERENCES custom_orders(id) ON DELETE CASCADE,
        CONSTRAINT fk_custom_order_materials_material FOREIGN KEY (material_id)
          REFERENCES custom_materials(id) ON DELETE SET NULL
    )""")
    for column, definition in (
        ("size_multiplier", "DECIMAL(5,2) NOT NULL DEFAULT 1.00 AFTER bouquet_size"),
        ("wrapping_style", "VARCHAR(80) NOT NULL DEFAULT 'Matte'"),
        ("flower_color", "VARCHAR(40) NOT NULL DEFAULT 'Pink' AFTER flower_type"),
    ):
        present = query_one(
            """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
                 AND COLUMN_NAME=%s""",
            (column,),
        )
        if not present["present"]:
            execute(f"ALTER TABLE custom_orders ADD COLUMN `{column}` {definition}")
        elif column == "wrapping_style":
            column_type = query_one(
                """SELECT DATA_TYPE AS data_type FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
                     AND COLUMN_NAME=%s""",
                (column,),
            )
            if column_type["data_type"] == "enum":
                execute("ALTER TABLE custom_orders MODIFY wrapping_style VARCHAR(80) NOT NULL DEFAULT 'Matte'")
    size_type = query_one(
        """SELECT DATA_TYPE AS data_type FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='bouquet_size'"""
    )
    if size_type and size_type["data_type"] == "enum":
        execute("ALTER TABLE custom_orders MODIFY bouquet_size VARCHAR(60) NOT NULL DEFAULT '1 pc'")
    seed_materials = [
        ("Red Rose", "Flower", "bouquet base", 50, 100, 5),
        ("Gerbera", "Flower", "bouquet base", 50, 100, 5),
        ("Sunflower", "Flower", "bouquet base", 40, 100, 5),
        ("Tulip", "Flower", "bouquet base", 60, 100, 5),
        ("Lily", "Flower", "bouquet base", 70, 100, 5),
        ("Mixed Flowers", "Flower", "bouquet base", 55, 100, 5),
        ("Kraft", "Wrapping", "sheet", 0, 100, 5),
        ("Matte", "Wrapping", "sheet", 50, 100, 5),
        ("Premium", "Wrapping", "sheet", 100, 100, 5),
        ("Ribbon", "Extra", "piece", 15, 100, 5),
        ("Foliage Filler", "Extra", "piece", 20, 100, 5),
    ]
    conn = db()
    try:
        cursor = conn.cursor()
        cursor.executemany(
            """INSERT IGNORE INTO custom_materials
               (name,category,unit,unit_price,stock,low_stock_threshold)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            seed_materials,
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        current_app.logger.exception("Could not initialize custom bouquet materials")
        raise
    finally:
        conn.close()
    _custom_materials_schema_ready = True


def ensure_order_messaging_schema():
    global _order_messaging_schema_ready
    if _order_messaging_schema_ready:
        return
    for table in ("orders", "custom_orders"):
        columns = {
            row["COLUMN_NAME"]
            for row in query_all(
                """SELECT COLUMN_NAME FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s""",
                (table,),
            )
        }
        if "cancellation_reason" not in columns:
            execute(f"ALTER TABLE `{table}` ADD COLUMN cancellation_reason TEXT NULL")
        if "confirmation_chat_sent" not in columns:
            execute(
                f"""ALTER TABLE `{table}` ADD COLUMN confirmation_chat_sent
                    TINYINT(1) NOT NULL DEFAULT 0"""
            )
    _order_messaging_schema_ready = True


def ensure_customer_profile_schema():
    global _customer_profile_schema_ready
    if _customer_profile_schema_ready:
        return
    columns = {
        row["COLUMN_NAME"]
        for row in query_all(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'"""
        )
    }
    if "delivery_address" not in columns:
        execute("ALTER TABLE users ADD COLUMN delivery_address TEXT NULL AFTER phone")
    if "profile_image" not in columns:
        execute("ALTER TABLE users ADD COLUMN profile_image VARCHAR(255) NULL AFTER delivery_address")
    address_columns = {
        "delivery_region_code": "VARCHAR(10) NULL",
        "delivery_province_code": "VARCHAR(10) NULL",
        "delivery_city_code": "VARCHAR(10) NULL",
        "delivery_barangay_code": "VARCHAR(10) NULL",
        "delivery_barangay": "VARCHAR(120) NULL",
        "delivery_postal_code": "VARCHAR(10) NULL",
        "delivery_street_address": "VARCHAR(500) NULL",
    }
    for column_name, column_type in address_columns.items():
        if column_name not in columns:
            execute(f"ALTER TABLE users ADD COLUMN `{column_name}` {column_type}")
    _customer_profile_schema_ready = True


def ensure_notification_link_schema():
    global _notification_link_schema_ready
    if _notification_link_schema_ready:
        return
    column = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='notifications'
             AND COLUMN_NAME='link_url'"""
    )
    if not column["present"]:
        execute("ALTER TABLE notifications ADD COLUMN link_url VARCHAR(500) NULL AFTER message")
    _notification_link_schema_ready = True


def ensure_customer_verification_schema():
    global _customer_verification_schema_ready
    if _customer_verification_schema_ready:
        return
    columns = {
        row["COLUMN_NAME"]
        for row in query_all(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'"""
        )
    }
    if "customer_id" not in columns:
        execute("ALTER TABLE users ADD COLUMN customer_id VARCHAR(80) NULL AFTER email")
    if "verification_status" not in columns:
        execute(
            """ALTER TABLE users ADD COLUMN verification_status
               ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Approved'
               AFTER customer_id"""
        )
    if "email_verified" not in columns:
        execute(
            """ALTER TABLE users ADD COLUMN email_verified
               TINYINT(1) NOT NULL DEFAULT 1 AFTER verification_status"""
        )
    if "account_status" not in columns:
        execute(
            """ALTER TABLE users ADD COLUMN account_status
               ENUM('Active','Pending','Rejected','Deactivated')
               NOT NULL DEFAULT 'Active' AFTER email_verified"""
        )
        execute(
            """UPDATE users SET account_status=CASE verification_status
                 WHEN 'Pending' THEN 'Pending'
                 WHEN 'Rejected' THEN 'Rejected'
                 ELSE 'Active' END
               WHERE role='customer'"""
        )
    if "verification_rejection_reason" not in columns:
        execute(
            """ALTER TABLE users ADD COLUMN verification_rejection_reason
               TEXT NULL AFTER account_status"""
        )
    for column in (
        "verification_id_front_filename",
        "verification_id_back_filename",
        "verification_selfie_filename",
    ):
        if column not in columns:
            execute(f"ALTER TABLE users ADD COLUMN `{column}` VARCHAR(255) NULL")
    indexes = {
        row["INDEX_NAME"]
        for row in query_all(
            """SELECT INDEX_NAME FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'"""
        )
    }
    if "uq_users_customer_id" not in indexes:
        execute("ALTER TABLE users ADD UNIQUE KEY uq_users_customer_id (customer_id)")
    if "idx_users_verification_queue" not in indexes:
        execute(
            """ALTER TABLE users ADD INDEX idx_users_verification_queue
               (role,verification_status,created_at,id)"""
        )
    execute("""CREATE TABLE IF NOT EXISTS customer_email_verification_tokens (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        token_hash CHAR(64) NOT NULL,
        expires_at DATETIME NOT NULL,
        used_at DATETIME NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_customer_email_verification_token (token_hash),
        INDEX idx_customer_email_verification_user (user_id,used_at,expires_at)
    )""")
    execute("""CREATE TABLE IF NOT EXISTS email_otp_challenges (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        purpose ENUM('registration','password_reset','new_device') NOT NULL,
        code_hash CHAR(64) NOT NULL,
        expires_at DATETIME NOT NULL,
        attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
        verified_at DATETIME NULL,
        used_at DATETIME NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_email_otp_user_purpose (user_id,purpose,used_at,expires_at),
        CONSTRAINT fk_email_otp_user FOREIGN KEY (user_id)
          REFERENCES users(id) ON DELETE CASCADE
    )""")
    execute("""CREATE TABLE IF NOT EXISTS trusted_login_devices (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        device_hash CHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_trusted_device_hash (device_hash),
        INDEX idx_trusted_devices_user (user_id,last_seen_at),
        CONSTRAINT fk_trusted_device_user FOREIGN KEY (user_id)
          REFERENCES users(id) ON DELETE CASCADE
    )""")
    _customer_verification_schema_ready = True


def ensure_order_queue_schema():
    global _order_queue_schema_ready
    if _order_queue_schema_ready:
        return
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """CREATE TABLE IF NOT EXISTS order_queue (
                 queue_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                 order_type ENUM('Order','Custom') NOT NULL,
                 order_id INT NOT NULL,
                 created_at DATETIME NOT NULL,
                 UNIQUE KEY uq_order_queue_record (order_type,order_id)
               )"""
        )
        execute(
            """CREATE TABLE IF NOT EXISTS custom_delivery_map_routes (
                 custom_order_id INT PRIMARY KEY,
                 route_hash CHAR(64) NOT NULL,
                 geometry_json MEDIUMTEXT NOT NULL,
                 distance_meters DECIMAL(12,2) NOT NULL,
                 duration_seconds INT NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 CONSTRAINT fk_custom_delivery_map_route_order FOREIGN KEY (custom_order_id)
                   REFERENCES custom_orders(id) ON DELETE CASCADE
               )"""
        )
        cur.execute("SELECT GET_LOCK('fuzzywire_order_queue_migration',10) AS acquired")
        lock = cur.fetchone()
        if not lock or lock["acquired"] != 1:
            raise RuntimeError("Could not acquire the order queue migration lock.")
        cur.execute(
            """INSERT IGNORE INTO order_queue (order_type,order_id,created_at)
               SELECT order_type,order_id,created_at
               FROM (
                 SELECT 'Order' AS order_type,id AS order_id,created_at FROM orders
                 UNION ALL
                 SELECT 'Custom',id,created_at FROM custom_orders
               ) AS existing_orders
               ORDER BY created_at ASC,order_id ASC,order_type ASC"""
        )
        conn.commit()
        cur.execute("SELECT RELEASE_LOCK('fuzzywire_order_queue_migration')")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _order_queue_schema_ready = True


def ensure_delivery_map_schema():
    global _delivery_map_schema_ready
    if _delivery_map_schema_ready:
        return
    execute(
        """CREATE TABLE IF NOT EXISTS delivery_map_geocodes (
             address_hash CHAR(64) PRIMARY KEY,
             address_text VARCHAR(2000) NOT NULL,
             latitude DECIMAL(10,7) NOT NULL,
             longitude DECIMAL(10,7) NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    execute(
        """CREATE TABLE IF NOT EXISTS delivery_map_routes (
             order_id INT PRIMARY KEY,
             route_hash CHAR(64) NOT NULL,
             geometry_json MEDIUMTEXT NOT NULL,
             distance_meters DECIMAL(12,2) NOT NULL,
             duration_seconds INT NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             CONSTRAINT fk_delivery_map_route_order FOREIGN KEY (order_id)
               REFERENCES orders(id) ON DELETE CASCADE
           )"""
    )
    execute(
        """CREATE TABLE IF NOT EXISTS custom_delivery_map_routes (
             custom_order_id INT PRIMARY KEY,
             route_hash CHAR(64) NOT NULL,
             geometry_json MEDIUMTEXT NOT NULL,
             distance_meters DECIMAL(12,2) NOT NULL,
             duration_seconds INT NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             CONSTRAINT fk_custom_delivery_map_route_order FOREIGN KEY (custom_order_id)
               REFERENCES custom_orders(id) ON DELETE CASCADE
           )"""
    )
    _delivery_map_schema_ready = True


def ensure_customer_delivery_confirmation_schema():
    global _customer_delivery_confirmation_schema_ready
    if _customer_delivery_confirmation_schema_ready:
        return
    for table, status_column in (("orders", "order_status"), ("custom_orders", "status")):
        column = query_one(
            """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
                 AND COLUMN_NAME='customer_received_at'""",
            (table,),
        )
        if not column["present"]:
            execute(
                f"ALTER TABLE `{table}` ADD COLUMN customer_received_at DATETIME NULL AFTER `{status_column}`"
            )
    ensure_custom_payment_schema()
    execute(
        """UPDATE orders SET payment_status='Paid'
           WHERE payment_method IN ('Cash on Delivery','Cash at Pickup')
             AND order_status IN ('Delivered','Picked Up')
             AND customer_received_at IS NOT NULL AND payment_status<>'Paid'"""
    )
    execute(
        """UPDATE custom_orders SET payment_status='Paid'
           WHERE payment_method IN ('Cash on Delivery','Cash at Pickup')
             AND status IN ('Delivered','Picked Up') AND customer_received_at IS NOT NULL
             AND payment_status<>'Paid'"""
    )
    _customer_delivery_confirmation_schema_ready = True


def ensure_custom_payment_schema():
    global _custom_payment_schema_ready
    if _custom_payment_schema_ready:
        return
    column = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='payment_status'"""
    )
    if not column["present"]:
        execute(
            """ALTER TABLE custom_orders ADD COLUMN payment_status
               ENUM('Unpaid','Paid') NOT NULL DEFAULT 'Unpaid' AFTER total_price"""
        )
    _custom_payment_schema_ready = True


def ensure_gcash_schema():
    global _gcash_schema_ready
    if _gcash_schema_ready:
        return
    ensure_fulfillment_schema()
    ensure_custom_payment_schema()
    for table in ("orders", "custom_orders"):
        payment_status_type = query_one(
            """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
                 AND COLUMN_NAME='payment_status'""",
            (table,),
        )
        if payment_status_type and any(
            value not in payment_status_type["column_type"]
            for value in ("Pending", "Failed", "Refunded")
        ):
            execute(
                f"""ALTER TABLE `{table}` MODIFY payment_status
                    ENUM('Unpaid','Pending','Paid','Failed','Refunded')
                    NOT NULL DEFAULT 'Unpaid'"""
            )
    order_columns = {
        row["COLUMN_NAME"]
        for row in query_all(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'"""
        )
    }
    if "payment_method" in order_columns:
        payment_type = query_one(
            """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'
                 AND COLUMN_NAME='payment_method'"""
        )
        if "PayMongo" in payment_type["column_type"]:
            execute(
                """ALTER TABLE orders MODIFY payment_method
                   ENUM('Cash on Delivery','Cash at Pickup','PayMongo','GCash')
                   NOT NULL DEFAULT 'Cash on Delivery'"""
            )
        execute(
            """UPDATE orders SET payment_method='GCash',
                   payment_status=CASE
                     WHEN payment_status IN ('Unpaid','Pending') THEN 'Pending'
                     ELSE payment_status END
               WHERE payment_method='PayMongo'"""
        )
        if "PayMongo" in payment_type["column_type"]:
            execute(
                """ALTER TABLE orders MODIFY payment_method
                   ENUM('Cash on Delivery','Cash at Pickup','GCash')
                   NOT NULL DEFAULT 'Cash on Delivery'"""
            )
    for table in ("orders", "custom_orders"):
        columns = {
            row["COLUMN_NAME"]
            for row in query_all(
                """SELECT COLUMN_NAME FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s""",
                (table,),
            )
        }
        additions = {
            "payment_proof_filename": "VARCHAR(255) NULL",
            "payment_proof_uploaded_at": "DATETIME NULL",
            "payment_reviewed_at": "DATETIME NULL",
            "payment_reviewed_by": "INT NULL",
            "payment_review_note": "VARCHAR(1000) NULL",
        }
        if table == "custom_orders":
            additions["payment_method"] = (
                "ENUM('Cash on Delivery','Cash at Pickup','GCash') "
                "NOT NULL DEFAULT 'Cash on Delivery'"
            )
            additions["fulfillment_method"] = (
                "ENUM('Delivery','Pickup') NOT NULL DEFAULT 'Delivery'"
            )
            additions["delivery_address"] = "TEXT NULL"
            additions["delivery_distance_km"] = (
                "DECIMAL(7,2) NOT NULL DEFAULT 0.00"
            )
            additions["delivery_fee"] = "DECIMAL(10,2) NOT NULL DEFAULT 0.00"
            additions["delivery_slot_id"] = "INT NULL"
            additions["phone"] = "VARCHAR(30) NULL"
            additions["materials_reserved"] = "TINYINT(1) NOT NULL DEFAULT 0"
            additions["updated_at"] = (
                "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
                "ON UPDATE CURRENT_TIMESTAMP"
            )
        for column, definition in additions.items():
            if column not in columns:
                execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
    legacy_upload_dir = UPLOAD_DIR.resolve()
    for table in ("orders", "custom_orders"):
        for proof in query_all(
            f"SELECT payment_proof_filename FROM `{table}` WHERE payment_proof_filename IS NOT NULL"
        ):
            filename = Path(proof["payment_proof_filename"]).name
            legacy_path = (legacy_upload_dir / filename).resolve()
            private_path = (PAYMENT_PROOF_DIR / filename).resolve()
            if legacy_path.parent != legacy_upload_dir or private_path.parent != PAYMENT_PROOF_DIR.resolve():
                continue
            if legacy_path.is_file():
                if private_path.exists():
                    legacy_path.unlink()
                else:
                    os.replace(legacy_path, private_path)
    custom_payment_type = query_one(
        """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='payment_method'"""
    )
    if custom_payment_type and "PayMongo" in custom_payment_type["column_type"]:
        execute(
            """ALTER TABLE custom_orders MODIFY payment_method
               ENUM('Cash on Delivery','Cash at Pickup','PayMongo','GCash')
               NOT NULL DEFAULT 'Cash on Delivery'"""
        )
        execute(
            """UPDATE custom_orders SET payment_method='GCash',
                   payment_status=CASE
                     WHEN payment_status IN ('Unpaid','Pending') THEN 'Pending'
                     ELSE payment_status END
               WHERE payment_method='PayMongo'"""
        )
        execute(
            """ALTER TABLE custom_orders MODIFY payment_method
               ENUM('Cash on Delivery','Cash at Pickup','GCash')
               NOT NULL DEFAULT 'Cash on Delivery'"""
        )
    custom_status_type = query_one(
        """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='custom_orders'
             AND COLUMN_NAME='payment_status'"""
    )
    if custom_status_type and "Pending" not in custom_status_type["column_type"]:
        execute(
            """ALTER TABLE custom_orders MODIFY payment_status
               ENUM('Unpaid','Pending','Paid','Failed','Refunded')
               NOT NULL DEFAULT 'Unpaid'"""
        )
    for table, status_column in (("orders", "order_status"), ("custom_orders", "status")):
        status_type = query_one(
            """SELECT COLUMN_TYPE AS column_type FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""",
            (table, status_column),
        )
        if status_type and "Ready for Pickup" not in status_type["column_type"]:
            execute(
                f"""ALTER TABLE `{table}` MODIFY `{status_column}`
                    ENUM('Pending','Confirmed','Preparing','Ready for Delivery',
                         'Ready for Pickup','Out for Delivery','Delivered','Picked Up',
                         'Cancelled') NOT NULL DEFAULT 'Pending'"""
            )
        execute(
            f"""UPDATE `{table}` SET `{status_column}`='Ready for Pickup'
                WHERE fulfillment_method='Pickup' AND `{status_column}`='Ready for Delivery'"""
        )
        execute(
            f"""UPDATE `{table}` SET `{status_column}`='Picked Up'
                WHERE fulfillment_method='Pickup' AND `{status_column}`='Delivered'"""
        )
    _gcash_schema_ready = True


def ensure_chat_schema():
    global _chat_schema_ready
    if _chat_schema_ready:
        return
    execute("""CREATE TABLE IF NOT EXISTS chat_conversations (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_chat_conversation_user FOREIGN KEY (user_id)
          REFERENCES users(id) ON DELETE CASCADE
    )""")
    execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        conversation_id INT NOT NULL,
        sender_role ENUM('customer','admin') NOT NULL,
        body TEXT NOT NULL,
        is_read TINYINT(1) NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_chat_messages_conversation (conversation_id, id),
        INDEX idx_chat_messages_unread (conversation_id, sender_role, is_read),
        CONSTRAINT fk_chat_message_conversation FOREIGN KEY (conversation_id)
          REFERENCES chat_conversations(id) ON DELETE CASCADE
    )""")
    _chat_schema_ready = True


def ensure_feedback_product_schema():
    global _feedback_product_schema_ready
    if _feedback_product_schema_ready:
        return
    columns = {
        row["COLUMN_NAME"]
        for row in query_all(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='feedback'"""
        )
    }
    if "product_id" not in columns:
        execute("ALTER TABLE feedback ADD COLUMN product_id INT NULL AFTER order_id")
    if "product_name" not in columns:
        execute("ALTER TABLE feedback ADD COLUMN product_name VARCHAR(160) NULL AFTER product_id")
    if "admin_reply" not in columns:
        execute("ALTER TABLE feedback ADD COLUMN admin_reply TEXT NULL AFTER comment")
    if "admin_checked_at" not in columns:
        execute("ALTER TABLE feedback ADD COLUMN admin_checked_at DATETIME NULL AFTER admin_reply")
    index = query_one(
        """SELECT COUNT(*) AS present FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='feedback'
             AND INDEX_NAME='feedback_order_product_unique'"""
    )
    if not index["present"]:
        execute(
            "ALTER TABLE feedback ADD UNIQUE KEY feedback_order_product_unique (order_id, product_id)"
        )
    _feedback_product_schema_ready = True
