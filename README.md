# FuzzyWireBouquet — Full System Starter

## Included
- Customer registration/login and role-based admin protection
- Customer registration requires a unique customer ID and matching passwords; new customers remain Pending until an admin approves or rejects them
- FIFO queues for open regular/custom orders and pending account verification; backend status transitions prevent newer active orders from bypassing the oldest, while the dashboard separately surfaces the newest pending order
- Admin verification inbox with approval/rejection confirmations, customer decision notifications, and pending-request badges
- Product catalog, stock, cart, checkout, delivery information
- Custom bouquet: choose shop-managed flower products and a bouquet color, upload a reference image or draw, add florist notes, and preview current material pricing
- Admin-managed custom bouquet materials with per-unit prices, stock, availability, and configurable low-stock thresholds; customer estimates use material-price snapshots and requests remain allowed when stock is low or empty
- Admin voucher management with names/codes, fixed or percentage discounts, minimum order amounts, expiration, claim quantities, activation controls, and claim/redemption counts; customers receive announcements, collect vouchers from the floating home-page popup or Vouchers page, and can redeem each claimed voucher once
- Admin-managed delivery and pickup time slots with capacity limits, customer scheduling at checkout, and transactional capacity checks
- Admin-only order notes and customer tags, refund records that do not falsely claim to process payments, and a store activity log
- Unified admin order list for bouquet purchases and custom bouquet requests
- Admin product editing for price, image, stock, and customer-facing availability
- Product sold counts and customer star ratings/reviews
- Clickable bouquet cards open product details with both Buy now and Add to cart; Buy now checks out the selected item without changing the regular cart
- Customer cart navigation and cart page use a matching bouquet shopping-bag icon with the current cart quantity
- Customers can select individual cart items for checkout; purchased selections are removed while unchecked items remain in the cart
- Cart selection includes select-all, selected-item count, and a live subtotal for the bouquets chosen for checkout
- Customer order history, delivery status page, feedback
- Account-based customer-to-admin chat with an admin inbox and automatic message refresh
- Descriptive analytics with Chart.js
- Delivery is restricted to validated addresses in Laguna. Delivery is free in Cavinti and costs ₱100 in other Laguna municipalities; free pickup remains available. The map uses OpenStreetMap road routes and a simulated rider marker without GPS.
- Checkout lets customers choose delivery or free pickup at Ancop Subdivision, store tapat ng court, 2nd to the last housing
- Cash on Delivery / Cash at Pickup, or manual GCash QR payment with customer proof and admin review
- OpenStreetMap/Leaflet delivery tracking with a routed, simulated rider; no rider GPS or location permission is used
- Customer menu for store links, with Cart, notifications, and Logout kept in the top navigation
- Customer profiles can edit name, email, phone, and a saved default Philippine delivery address using region, province, city/municipality, barangay, and ZIP/postal-code selectors; checkout can use that saved address or a different address for one order without changing the profile, and each order keeps its own address snapshot
- Customer registration requires a valid-ID front photo, ID back photo, selfie holding that ID, and acceptance of the terms. Verification photos are private, admin-only, and deleted after the FIFO approve/reject decision; the customer can also save an optional validated Philippine delivery address.
- The customer profile opens as an accessible floating portal dialog; the admin pages use a shared dashboard-style heading and updated summary cards
- Customer Cart, My Orders, order details, notifications, product details, checkout, custom bouquet, chat, feedback, and tracking open in a matching responsive floating panel; Profile remains a floating portal
- Customer profiles summarize total, delivered, and in-progress orders; the admin Customers directory opens a customer profile and combined regular/custom order history
- Customer and admin regular-order detail pages show a visual order progress timeline; admin order details include a printable delivery/pickup waybill
- The admin dashboard lists active low-stock bouquets, and Manage Orders can export the combined regular/custom order history as CSV
- Admins open their separate sign-in page directly at `/admin/login`; the customer login page has no admin sign-in link, and customer accounts cannot sign in through the admin portal
- Admins can record orders from before the system was used in Manual Order History. Entries are saved as normal order and item records with the entered order date, customer/contact/address, quantity, unit price, delivery fee, payment and order statuses, and notes. They appear in Manage Orders and Analytics without changing current inventory.
- Customers can upload a profile photo that admins can see in Customer Management and the customer chat inbox
- Customers can save bouquets to a private wishlist, reorder a prior regular order into their cart, and filter the catalog by flower, color, occasion, stock status, and price range; bouquets can be sorted by rating or popularity
- Checkout supports an optional gift message saved with the order and shown to the customer and admin
- Customer unread notification badge when an order is out for delivery
- Customer receipt confirmation after an admin marks a regular or custom order Delivered
- Admin cancellation reasons shown to customers on their order pages
- Automatic order-summary chat messages when an admin confirms a regular or custom order
- Six-digit email OTPs for registration, password reset, and first-time device verification; codes expire after 10 minutes, are single-use, and have resend/attempt limits
- Customer accounts have Active, Pending, Rejected, and Deactivated states; ID rejection reasons are shown to customers, who can resubmit documents for admin review
- Admins can deactivate/reactivate eligible customers; customer sessions are refreshed against account status and each role is restricted to its own routes
- Admin order and feedback notifications with unread badges, feedback replies, and an isolated PyQt6 OOP desktop tool for product management, admin order processing, and customer order tracking

## Project layout
- `app.py` remains the run/import entry point; `application.py` creates the Flask app.
- Runtime settings, MySQL access, and data models are separated into `config.py`, `database.py`, and `models.py`.
- Customer-facing and admin endpoints are organized under `routes/`; shared schema and workflow code is under `services/`.
- The original monolithic app is preserved at `_backup/app.py.original`.
- Jinja pages are in `templates/`, with admin pages in `templates/admin/`.
- Stylesheets and JavaScript are in `static/css/` and `static/js/`; images and data stay under `static/`.
- The optional PyQt6 desktop tools are in `oop_desktop/` and use the existing `products`, `orders`, `order_items`, `custom_orders`, `deliveries`, and user tables.
- `venv/` and `.env` are local to each computer. Create `.env` from `.env.example`; do not share or commit it.

## Setup (Windows / VS Code)
1. Install Python and MySQL.
2. Open this folder in VS Code.
3. Create a virtual environment:
   `python -m venv venv`
4. Activate it in PowerShell:
   `.\venv\Scripts\Activate.ps1`
5. Install packages:
   `pip install -r requirements.txt`
6. In phpMyAdmin, create/import the database using `schema.sql`.
7. Copy `.env.example` to `.env` (the app does not load `.env.example` directly) and set your MySQL credentials and a strong `SECRET_KEY`. Keep `FLASK_DEBUG=false` outside local development; set `SESSION_COOKIE_SECURE=true` when serving the site over HTTPS.
8. Configure `GCASH_ACCOUNT_NAME` and the initial `GCASH_NUMBER` in `.env`. Admins can change the stored number and optionally upload or replace the shop's PNG, JPG, or WEBP QR code (up to 5 MB) from **GCash Settings**. Customers see the latest saved payment details, can scan the QR, upload a payment receipt, and see its verification status. Admins review receipts from **GCash Verification**; upload alone never marks an order Paid.
9. Admins can set the public pickup location from **Shop Location** in the sidebar. Choose the Laguna municipality and barangay, enter the shop name and full address, then drag the map pin to the shop if needed; no coordinates need to be typed. Customers can open **Shop for Pick-up** in the site navigation to see the shop name, address, and map before ordering. If the street cannot be found, the map marks the selected municipality as approximate.
10. To send customer verification, Forgot Password, and order notification emails from Gmail, set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, and use the sending Gmail address for both `SMTP_USERNAME` and `SMTP_FROM`. Put that account's Google App Password (not the Gmail sign-in password) in `SMTP_PASSWORD`, without spaces. Google requires 2-Step Verification to create an App Password. Set `BASE_URL` to `http://127.0.0.1:5000` for local testing or the public HTTPS address customers use in production. Keep `.env` private and restart Flask after changing it.
11. For local development with automatic code reload and browser refresh, start the app with:
   `python app.py --dev`
   Keep this running while you edit. Python, template, CSS, and JavaScript changes reload the local page automatically. This development mode listens only on your computer at `127.0.0.1`; it is not available to phones or other devices.
12. Open `http://127.0.0.1:5000`. To use the site from a phone on the same Wi-Fi instead, start it normally with `python app.py`; run `ipconfig` on the computer, find its Wi-Fi adapter's **IPv4 Address**, then visit `http://<computer-ipv4>:5000` on the phone (for example, `http://192.168.1.25:5000`). Keep the computer and Flask app running. If Windows Firewall prompts, allow Python on **Private networks only**; do not expose the port to Public networks or forward it from your router. Keep `FLASK_DEBUG=false` outside local development. Some guest Wi-Fi networks block devices from communicating with each other.

## Tests
- Run the safe, database-independent application checks with `python -m unittest discover -v`.
- Database-backed customer/admin workflows require a separate test database; do not run write-flow tests against production records.

## Optional PyQt6 OOP desktop features
- Start MySQL and configure the same `.env` database settings used by the Flask site.
- Install the desktop app's separate dependencies: `pip install -r oop_desktop\requirements.txt`.
- Run `python oop_desktop\main.py` from the project root.
- Choose Product Manager, Admin Order Management, or Customer Order Tracking at startup. The product manager retains its existing behavior; the order tools sign in with existing website credentials.
- Admin Order Management shows regular bouquet orders and advances them one step at a time, checking GCash payment requirements and the existing FIFO queue (including older custom bouquet requests).
- Customer Order Tracking shows that signed-in customer's regular and custom orders, with product/bouquet names and current order and delivery statuses. Customer database queries are limited to that customer's account.
- The desktop module reuses the Flask MySQL settings and existing tables; it does not create another database or order table. Its dependencies are separate from the Flask application's `requirements.txt`.

## Create an admin account
- Register an account using the normal registration page.
- In phpMyAdmin run (replace the email) to promote that account and bypass customer verification:
  `UPDATE users SET role='admin', verification_status='Approved', account_status='Active', email_verified=1 WHERE email='your-admin-email@example.com';`
- Log out, then sign in at `/admin/login` to open the separate admin portal.

## Important notes
- The admin pages are role-protected; do not deploy with a weak SECRET_KEY.
- New customer registrations are saved as Pending and cannot sign in until the admin approves them. Admin → Pending Verifications processes requests FIFO; only the oldest pending request can be approved or rejected.
- Account registration does not ask customers to type a customer ID. It requires three PNG/JPG/JPEG/WEBP photos (front/back of a valid ID and a selfie holding it), each at most 5 MB. Files are stored under the ignored `private_uploads/customer_verification/` folder and are served only through an admin-protected route while the account is Pending; reviewed files are removed when an admin approves or rejects the account. Back up or restrict access to `private_uploads/` on the host.
- Existing database migration: back up the database first, then run `python app.py` and open the relevant pages to apply the idempotent schema updates. Existing PayMongo-method orders are migrated to GCash; prior Unpaid/Pending records become Pending and Paid records remain Paid. Existing payment-session records and the legacy `payments` table are not deleted, but the application no longer uses them. Existing orders are backfilled into the FIFO queue in creation-time/ID order, and existing accounts default to Approved so they retain access. For a new database, import the updated `schema.sql` before starting Flask.
- Forgot Password and customer email verification send one-time links by SMTP (valid for 30 minutes and 24 hours, respectively). Existing databases create the `password_reset_tokens` table automatically when the recovery page is first opened. SMTP must be configured in `.env` for these emails to be sent.
- Delivery is available only for validated Laguna addresses. The configured shop and destination are geocoded and routed with OpenStreetMap/OSRM; rider movement is simulated and does not use GPS. A manual distance override is available only if address lookup fails. Pickup remains free.
- Regular and custom orders support delivery or free pickup. Pickup orders save the configured shop location, do not require a delivery distance, and use Ready for Pickup/Picked Up statuses.
- GCash uses an optional admin-managed shop QR plus a separate customer-uploaded image proof. Payments remain Pending until an admin opens the order, reviews the proof, and approves or rejects it. Customers can re-upload after rejection. Existing payment-session records, if any, are left untouched; the legacy payment table is no longer used.
- Payment-proof images are validated, limited to 5 MB, and stored outside `static/` so only the owning customer and admins can view them. Back up `private_uploads/` along with the database.
- Tracking uses Leaflet with OpenStreetMap tiles, Nominatim geocoding, and OSRM road routes. It is labeled “Estimated route / demo rider — no GPS.” An internet connection is required. On first lookup, delivery and shop addresses are sent to the public geocoder and routing services; locations and routes are then cached in MySQL. Admins update the shop address from **Shop Location**; maps geocode the address and fall back to an approximate municipality pin if needed.
- The admin Manage Orders list includes both product orders and custom bouquet requests. The product catalog lets admins edit product details and show/hide items; items with zero stock appear as Sold out.
- Admin → Custom Materials lets admins add materials and edit their category, unit price, decimal stock, low-stock threshold, and customer visibility. Custom bouquet prices are calculated server-side from the selected materials and size; the saved order retains its itemized price snapshot. Material stock is reserved when the admin confirms a custom request and restored if it is cancelled.
- Admins can assign each product an occasion; customers can filter bouquets by occasion, color, price range, and availability alongside the existing flower, rating, and popularity controls. Wishlist entries are private to each customer, and Order again adds the original bouquet items to the saved cart only when current products are available.
- Checkout validates voucher codes on the server and shows a server-verified discount preview; each customer may use a code once, with redemptions and global limits recorded transactionally.
- Checkout supports an optional scheduled delivery or pickup time. Admin-created future slots share capacity across regular and custom orders; selections are checked transactionally and customers can change or clear a slot while Pending. Unscheduled orders remain allowed.
- Existing databases create custom material and order-material snapshot tables automatically when a customer opens Custom Bouquet or an admin opens the dashboard, materials page, or custom order details. Existing custom-order tables are migrated to support editable material names and save the size multiplier.
- Existing databases add product occasion metadata when the shop or product inventory is opened and create the customer wishlist table when a signed-in customer views the shop or product details.
- Existing databases add the optional gift-message field when a new regular order is created.
- Existing databases create promo-code, voucher-claim, and delivery-slot tables as checkout/admin pages are used. Voucher display names and claim limits are added without deleting existing codes or redemptions; existing use limits are retained.
- Admins can add private notes to regular or custom orders, tag customer records, and record/complete refund entries for paid regular orders. Refund records are bookkeeping only and must be processed separately with the payment provider; the order status is marked Refunded only after completed refunds cover the full paid total.
- Activity Log records key admin edits, order-status changes, tags, refunds, and payment-setting changes. Analytics also reports repeat customers and peak live-order hours alongside item popularity and sales trends.
- Existing databases create admin notes, refund, and activity-log tables and add customer tags when an admin opens order details or Customer Management. Existing databases add the optional order time-slot, voucher, discount, gift-message, and product-occasion columns through the relevant page workflows.
- Customer My Orders and Admin → Manage Orders group regular and custom orders into All, To Pay, To Ship, To Receive, Delivered, and Cancelled tabs. Unpaid GCash orders appear under To Pay; pending preparation is To Ship; ready-for-delivery, out-for-delivery, and ready-for-pickup orders appear under To Receive.
- Admin Analytics can be filtered by order/review date, with 7-day, 30-day, and current-month shortcuts; without dates selected it summarizes all order records. Manually entered orders use their entered order date in sales, order, and product reports. Completed-order counts and product preference charts include both Delivered and Picked Up orders; neither counts as open. “Customers with orders” is the number of unique customers with orders in the selected date range. Paid sales value is based on the saved payment status, excludes cancelled orders, and subtracts completed refunds for regular bouquet orders; it is not independently reconciled against bank/GCash records. COD/custom paid amounts reflect saved statuses rather than verified cash collection. Profit is not calculated because product costs are not stored.
- Existing records previously entered from Analytics are imported once into normal order records and are included in Manage Orders and Analytics. The source rows are retained, and the import map prevents duplicate counting.
- Admin Analytics payment breakdown includes regular and custom bouquet payments. Admin order, product, customer, and feedback lists support live text search; charts show an explanatory fallback if the chart library cannot load.
- Customers can submit one product review for each bouquet in a delivered order. Product ratings and recent customer comments appear in the shop; the admin Feedback page shows submitted reviews.
- Customer carts are saved to the account, so cart items remain after logout and are restored at the next login. Customers can update quantities with the cart’s plus/minus controls; the server validates quantity against stock and checkout uses the saved values. Existing databases create the `customer_cart_items` table automatically when a customer logs in; guest cart items are merged into the account cart at login.
- After an admin marks an order Delivered or Picked Up, the customer can confirm receipt from My Orders; the confirmation is saved separately and does not replace the admin-managed status. Receipt confirmation marks COD/Cash at Pickup payments Paid; GCash remains Pending until an admin approves the uploaded proof.
- Out-for-delivery status changes create customer notifications. The unread count is shown beside Cart and refreshes every 20 seconds while the customer is browsing; notifications are marked read when the customer opens the notifications page.
- Customer notifications are clickable and open the related order detail, the matching custom request, or the customer chat. Existing databases add the notification destination column automatically when notifications are opened or a new notification is created.
- Logged-in customers can chat with the shop using the floating chat bubble or the customer menu. Customer messages appear in Admin → Customer Chats; messages refresh every four seconds while the chat is open.
- When an admin confirms a regular order or custom bouquet request, its order summary is automatically sent to the customer's chat once. Admins must provide a cancellation reason when cancelling; customers can see it on their order history/details, and cancellation notifications include the reason.
- Existing databases get the `chat_conversations` and `chat_messages` tables automatically the first time a customer opens chat or an admin opens the chat inbox.
- If the existing database was created before product reviews were added, the app adds the nullable feedback `product_id` column and per-order/product review uniqueness key when product, feedback, or admin product pages are first opened.
- If you already imported `schema.sql` before adding delivery-distance pricing, the app adds the `delivery_distance_km` column to the existing `orders` table the first time checkout or tracking is opened. It also adds customer receipt-confirmation columns to existing regular and custom order tables when the customer opens My Orders.
- Existing databases add `custom_orders.payment_status` when orders or analytics are opened. Existing delivered orders already confirmed as received are synchronized to Paid for Cash on Delivery/custom orders; other COD/custom orders become Paid when the customer confirms receipt.
- Existing databases add account-status and ID-rejection-reason fields while preserving prior approved/rejected/pending states, and create OTP-challenge and trusted-device tables. New registrations must verify the six-digit email OTP and receive admin ID approval before full customer access.
- Password resets require a separate six-digit email OTP before the one-time password-change token is issued. Customer sign-ins from unrecognized devices require an email OTP; trusted-device identifiers are stored as hashes.
- Existing databases add the cancellation-reason and confirmation-chat tracking columns automatically when order management or customer order pages are opened.
- Existing databases get the saved customer delivery-address and profile-image columns automatically when a customer opens My Profile, checkout, Admin → Customers, or registers with a delivery address. Profile edits update the current account; already-placed orders keep their original recipient and address.
- Philippine address choices are bundled locally from the PSGC API mirror ([regions](https://psgc.gitlab.io/api/regions/), [provinces](https://psgc.gitlab.io/api/provinces/), [cities/municipalities](https://psgc.gitlab.io/api/cities-municipalities/), and [barangays](https://psgc.gitlab.io/api/barangays/)). The bundle includes the Region 18 (Negros Island Region) assignments. ZIP/postal codes are optional and retained where available; if a locality has no barangay list, the form asks the customer to type the barangay. The app adds structured saved-address columns to existing user tables automatically.
- This is a classroom starter, not a production-audited system. Test locally and back up your database before real use.
