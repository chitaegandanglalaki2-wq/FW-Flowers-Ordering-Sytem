CREATE DATABASE IF NOT EXISTS fuzzywirebouquet
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE fuzzywirebouquet;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  full_name VARCHAR(120) NOT NULL,
  email VARCHAR(160) NOT NULL UNIQUE,
  customer_id VARCHAR(80) NULL,
  verification_status ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Approved',
  email_verified TINYINT(1) NOT NULL DEFAULT 1,
  account_status ENUM('Active','Pending','Rejected','Deactivated') NOT NULL DEFAULT 'Active',
  verification_rejection_reason TEXT NULL,
  verification_id_front_filename VARCHAR(255) NULL,
  verification_id_back_filename VARCHAR(255) NULL,
  verification_selfie_filename VARCHAR(255) NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('customer','admin') NOT NULL DEFAULT 'customer',
  phone VARCHAR(30) NULL,
  delivery_address TEXT NULL,
  profile_image VARCHAR(255) NULL,
  delivery_region_code VARCHAR(10) NULL,
  delivery_province_code VARCHAR(10) NULL,
  delivery_city_code VARCHAR(10) NULL,
  delivery_barangay_code VARCHAR(10) NULL,
  delivery_barangay VARCHAR(120) NULL,
  delivery_postal_code VARCHAR(10) NULL,
  delivery_street_address VARCHAR(500) NULL,
  admin_tags VARCHAR(500) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_users_customer_id (customer_id),
  INDEX idx_users_verification_queue (role, verification_status, created_at, id)
);

CREATE TABLE IF NOT EXISTS app_settings (
  setting_key VARCHAR(60) PRIMARY KEY,
  setting_value VARCHAR(255) NOT NULL,
  updated_by INT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_app_settings_admin FOREIGN KEY (updated_by)
    REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  token_hash CHAR(64) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL,
  used_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_password_reset_user (user_id, used_at, expires_at),
  CONSTRAINT fk_password_reset_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS customer_email_verification_tokens (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  token_hash CHAR(64) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL,
  used_at DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_customer_email_verification_user (user_id, used_at, expires_at)
);

CREATE TABLE IF NOT EXISTS email_otp_challenges (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  purpose ENUM('registration','password_reset','new_device') NOT NULL,
  code_hash CHAR(64) NOT NULL,
  expires_at DATETIME NOT NULL,
  attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
  verified_at DATETIME NULL,
  used_at DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_email_otp_user_purpose (user_id, purpose, used_at, expires_at),
  CONSTRAINT fk_email_otp_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trusted_login_devices (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  device_hash CHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_trusted_device_hash (device_hash),
  INDEX idx_trusted_devices_user (user_id, last_seen_at),
  CONSTRAINT fk_trusted_device_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  description TEXT NULL,
  flower_type VARCHAR(80) NOT NULL,
  color VARCHAR(50) NOT NULL DEFAULT 'Mixed',
  occasion VARCHAR(60) NOT NULL DEFAULT 'Any occasion',
  price DECIMAL(10,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0,
  image_filename VARCHAR(255) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_cart_items (
  user_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  PRIMARY KEY (user_id, product_id),
  CONSTRAINT fk_customer_cart_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wishlists (
  user_id INT NOT NULL,
  product_id INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id,product_id),
  CONSTRAINT fk_wishlist_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_wishlist_product FOREIGN KEY (product_id)
    REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS delivery_slots (
  id INT AUTO_INCREMENT PRIMARY KEY,
  starts_at DATETIME NOT NULL,
  fulfillment_method ENUM('Delivery','Pickup') NOT NULL,
  capacity INT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_delivery_slot_start_method (starts_at,fulfillment_method)
);

CREATE TABLE IF NOT EXISTS orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  customer_name VARCHAR(120) NOT NULL,
  customer_email VARCHAR(160) NOT NULL,
  phone VARCHAR(30) NOT NULL,
  fulfillment_method ENUM('Delivery','Pickup') NOT NULL DEFAULT 'Delivery',
  delivery_slot_id INT NULL,
  delivery_address TEXT NOT NULL,
  delivery_notes TEXT NULL,
  gift_message VARCHAR(500) NULL,
  delivery_distance_km DECIMAL(7,2) NOT NULL DEFAULT 0.00,
  subtotal DECIMAL(10,2) NOT NULL,
  delivery_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  promo_code VARCHAR(40) NULL,
  discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total DECIMAL(10,2) NOT NULL,
  payment_method ENUM('Cash on Delivery','Cash at Pickup','GCash') NOT NULL DEFAULT 'Cash on Delivery',
  payment_status ENUM('Unpaid','Pending','Paid','Failed','Refunded') NOT NULL DEFAULT 'Unpaid',
  payment_proof_filename VARCHAR(255) NULL,
  payment_proof_uploaded_at DATETIME NULL,
  payment_reviewed_at DATETIME NULL,
  payment_reviewed_by INT NULL,
  payment_review_note VARCHAR(1000) NULL,
  order_status ENUM('Pending','Confirmed','Preparing','Ready for Delivery','Ready for Pickup','Out for Delivery','Delivered','Picked Up','Cancelled') NOT NULL DEFAULT 'Pending',
  cancellation_reason TEXT NULL,
  confirmation_chat_sent TINYINT(1) NOT NULL DEFAULT 0,
  customer_received_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_orders_delivery_slot FOREIGN KEY (delivery_slot_id)
    REFERENCES delivery_slots(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS order_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  order_id INT NOT NULL,
  product_id INT NULL,
  item_name VARCHAR(160) NOT NULL,
  flower_type VARCHAR(80) NOT NULL,
  color VARCHAR(50) NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  line_total DECIMAL(10,2) NOT NULL,
  CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS manual_order_imports (
  source_sale_id INT PRIMARY KEY,
  order_id INT NOT NULL UNIQUE,
  imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_manual_order_import_order FOREIGN KEY (order_id)
    REFERENCES orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS promo_vouchers (
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
);

CREATE TABLE IF NOT EXISTS voucher_claims (
  id INT AUTO_INCREMENT PRIMARY KEY,
  voucher_id INT NOT NULL,
  user_id INT NOT NULL,
  claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_voucher_claim_user (voucher_id,user_id),
  CONSTRAINT fk_voucher_claim_voucher FOREIGN KEY (voucher_id)
    REFERENCES promo_vouchers(id) ON DELETE CASCADE,
  CONSTRAINT fk_voucher_claim_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voucher_redemptions (
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
);

CREATE TABLE IF NOT EXISTS custom_orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  flower_type VARCHAR(80) NOT NULL,
  flower_color VARCHAR(40) NOT NULL DEFAULT 'Pink',
  bouquet_size ENUM('Small','Medium','Large') NOT NULL,
  size_multiplier DECIMAL(5,2) NOT NULL DEFAULT 1.00,
  wrapping_style VARCHAR(80) NOT NULL DEFAULT 'Matte',
  custom_message VARCHAR(500) NULL,
  reference_image VARCHAR(255) NULL,
  drawing_image VARCHAR(255) NULL,
  quantity INT NOT NULL DEFAULT 1,
  total_price DECIMAL(10,2) NOT NULL,
  payment_method ENUM('Cash on Delivery','Cash at Pickup','GCash') NOT NULL DEFAULT 'Cash on Delivery',
  payment_status ENUM('Unpaid','Pending','Paid','Failed','Refunded') NOT NULL DEFAULT 'Unpaid',
  payment_proof_filename VARCHAR(255) NULL,
  payment_proof_uploaded_at DATETIME NULL,
  payment_reviewed_at DATETIME NULL,
  payment_reviewed_by INT NULL,
  payment_review_note VARCHAR(1000) NULL,
  phone VARCHAR(30) NULL,
  fulfillment_method ENUM('Delivery','Pickup') NOT NULL DEFAULT 'Delivery',
  delivery_address TEXT NULL,
  delivery_distance_km DECIMAL(7,2) NOT NULL DEFAULT 0.00,
  delivery_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  delivery_slot_id INT NULL,
  materials_reserved TINYINT(1) NOT NULL DEFAULT 0,
  status ENUM('Pending','Confirmed','Preparing','Ready for Delivery','Ready for Pickup','Out for Delivery','Delivered','Picked Up','Cancelled') NOT NULL DEFAULT 'Pending',
  cancellation_reason TEXT NULL,
  confirmation_chat_sent TINYINT(1) NOT NULL DEFAULT 0,
  customer_received_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_custom_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_custom_delivery_slot FOREIGN KEY (delivery_slot_id)
    REFERENCES delivery_slots(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS order_queue (
  queue_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_type ENUM('Order','Custom') NOT NULL,
  order_id INT NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uq_order_queue_record (order_type, order_id)
);

CREATE TABLE IF NOT EXISTS custom_materials (
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
);

CREATE TABLE IF NOT EXISTS custom_order_materials (
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
);

CREATE TABLE IF NOT EXISTS order_admin_notes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  order_type ENUM('Order','Custom') NOT NULL,
  order_id INT NOT NULL,
  admin_id INT NULL,
  note TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_order_admin_notes_target (order_type,order_id),
  CONSTRAINT fk_order_admin_notes_admin FOREIGN KEY (admin_id)
    REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS order_refunds (
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
);

CREATE TABLE IF NOT EXISTS admin_activity_logs (
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
);

CREATE TABLE IF NOT EXISTS historical_sales (
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
);

CREATE TABLE IF NOT EXISTS historical_sale_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  historical_sale_id INT NOT NULL,
  item_name VARCHAR(160) NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  line_total DECIMAL(10,2) NOT NULL,
  INDEX idx_historical_sale_items_name (item_name),
  CONSTRAINT fk_historical_sale_items_sale FOREIGN KEY (historical_sale_id)
    REFERENCES historical_sales(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deliveries (
  id INT AUTO_INCREMENT PRIMARY KEY,
  order_id INT NOT NULL UNIQUE,
  delivery_status VARCHAR(40) NOT NULL DEFAULT 'Pending',
  estimated_delivery DATETIME NULL,
  delivery_notes TEXT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_delivery_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS delivery_map_geocodes (
  address_hash CHAR(64) PRIMARY KEY,
  address_text VARCHAR(2000) NOT NULL,
  latitude DECIMAL(10,7) NOT NULL,
  longitude DECIMAL(10,7) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_map_routes (
  order_id INT PRIMARY KEY,
  route_hash CHAR(64) NOT NULL,
  geometry_json MEDIUMTEXT NOT NULL,
  distance_meters DECIMAL(12,2) NOT NULL,
  duration_seconds INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_delivery_map_route_order FOREIGN KEY (order_id)
    REFERENCES orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_delivery_map_routes (
  custom_order_id INT PRIMARY KEY,
  route_hash CHAR(64) NOT NULL,
  geometry_json MEDIUMTEXT NOT NULL,
  distance_meters DECIMAL(12,2) NOT NULL,
  duration_seconds INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_custom_delivery_map_route_order FOREIGN KEY (custom_order_id)
    REFERENCES custom_orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  order_id INT NULL,
  product_id INT NULL,
  product_name VARCHAR(160) NULL,
  rating INT NOT NULL,
  comment TEXT NOT NULL,
  admin_reply TEXT NULL,
  admin_checked_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY feedback_order_product_unique (order_id, product_id),
  CONSTRAINT fk_feedback_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_feedback_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  title VARCHAR(160) NOT NULL,
  message TEXT NOT NULL,
  link_url VARCHAR(500) NULL,
  is_read TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_notifications_user_unread (user_id, is_read, created_at),
  CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_conversations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL UNIQUE,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_chat_conversation_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_messages (
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
);

INSERT INTO products (name, description, flower_type, color, price, stock, image_filename)
SELECT 'Classic Red Rose Bouquet','Classic red roses wrapped beautifully.','Red Rose','Red',850.00,24,NULL
WHERE NOT EXISTS (SELECT 1 FROM products WHERE name='Classic Red Rose Bouquet');
INSERT INTO products (name, description, flower_type, color, price, stock, image_filename)
SELECT 'Sunny Sunflower Bouquet','Bright sunflower bouquet for cheerful occasions.','Sunflower','Yellow',950.00,18,NULL
WHERE NOT EXISTS (SELECT 1 FROM products WHERE name='Sunny Sunflower Bouquet');
INSERT INTO products (name, description, flower_type, color, price, stock, image_filename)
SELECT 'Tulip Bouquet','Elegant tulips with premium wrapping.','Tulip','Pink',1200.00,12,NULL
WHERE NOT EXISTS (SELECT 1 FROM products WHERE name='Tulip Bouquet');
INSERT INTO products (name, description, flower_type, color, price, stock, image_filename)
SELECT 'Mixed Flower Bouquet','A colorful mix of seasonal flowers.','Mixed Flowers','Mixed',1000.00,15,NULL
WHERE NOT EXISTS (SELECT 1 FROM products WHERE name='Mixed Flower Bouquet');
