"""Desktop product manager demonstrating encapsulation, inheritance, and CRUD."""

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import check_password_hash
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class DatabaseRepository:
    """Shares the Flask website's MySQL connection settings."""

    def __init__(self):
        self._connection_options = {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DB", "fuzzywirebouquet"),
            "connect_timeout": 5,
        }

    def _connect(self):
        return mysql.connector.connect(**self._connection_options)


@dataclass(frozen=True)
class Product:
    """Validated product data mapped to the Flask application's products table."""

    name: str
    description: str
    flower_type: str
    color: str
    price: Decimal
    stock: int
    is_active: bool = True
    product_id: int | None = None

    def __post_init__(self):
        if not self.name or len(self.name) > 120:
            raise ValueError("Product name is required and must be 120 characters or fewer.")
        if len(self.description) > 5000:
            raise ValueError("Description must be 5,000 characters or fewer.")
        if not self.flower_type or len(self.flower_type) > 80:
            raise ValueError("Flower type is required and must be 80 characters or fewer.")
        if not self.color or len(self.color) > 50:
            raise ValueError("Color is required and must be 50 characters or fewer.")
        try:
            normalized_price = Decimal(str(self.price)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            raise ValueError("Enter a valid product price.") from None
        if not normalized_price.is_finite() or normalized_price <= 0:
            raise ValueError("Product price must be greater than zero.")
        if self.stock < 0:
            raise ValueError("Stock cannot be negative.")
        object.__setattr__(self, "price", normalized_price)


class ProductRepository(DatabaseRepository):
    """Owns database access and CRUD operations for existing product records."""

    @staticmethod
    def _product_from_row(row: dict) -> Product:
        return Product(
            product_id=row["id"],
            name=row["name"],
            description=row.get("description") or "",
            flower_type=row["flower_type"],
            color=row["color"],
            price=Decimal(str(row["price"])),
            stock=int(row["stock"]),
            is_active=bool(row["is_active"]),
        )

    def list_products(self, include_inactive: bool = False) -> list[Product]:
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            sql = """SELECT id,name,description,flower_type,color,price,stock,is_active
                     FROM products"""
            if not include_inactive:
                sql += " WHERE is_active=1"
            sql += " ORDER BY name,id"
            cursor.execute(sql)
            return [self._product_from_row(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def create(self, product: Product) -> int:
        return self._write(
            """INSERT INTO products
               (name,description,flower_type,color,price,stock,is_active)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            self._values(product) + (int(product.is_active),),
        )

    def update(self, product_id: int, product: Product) -> None:
        self._write(
            """UPDATE products
               SET name=%s,description=%s,flower_type=%s,color=%s,price=%s,stock=%s
               WHERE id=%s""",
            self._values(product) + (product_id,),
        )

    def set_active(self, product_id: int, is_active: bool) -> None:
        self._write(
            "UPDATE products SET is_active=%s WHERE id=%s",
            (int(is_active), product_id),
        )

    @staticmethod
    def _values(product: Product) -> tuple:
        return (
            product.name,
            product.description or None,
            product.flower_type,
            product.color,
            product.price,
            product.stock,
        )

    def _write(self, sql: str, values: tuple) -> int:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, values)
            connection.commit()
            return cursor.lastrowid
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            connection.close()


@dataclass(frozen=True)
class Order:
    """A regular or custom customer order shown in the desktop windows."""

    order_id: int
    customer_name: str
    product_name: str
    quantity: int
    total_price: Decimal
    order_date: datetime
    order_status: str
    delivery_status: str
    order_type: str = "Bouquet"
    order_kind: str = "Order"
    fulfillment_method: str = "Delivery"


class OrderRepository(DatabaseRepository):
    """Reads customer orders and safely advances regular-order statuses."""

    @staticmethod
    def _order_from_row(row: dict) -> Order:
        return Order(
            order_id=int(row["order_id"]),
            customer_name=row["customer_name"],
            product_name=row["product_name"] or "Bouquet",
            quantity=int(row["quantity"] or 0),
            total_price=Decimal(str(row["total_price"])),
            order_date=row["order_date"],
            order_status=row["order_status"],
            delivery_status=row["delivery_status"] or row["order_status"],
            order_type=row["order_type"],
            order_kind=row["order_kind"],
            fulfillment_method=row["fulfillment_method"],
        )

    def authenticate_user(self, email: str, password: str, role: str) -> dict | None:
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT id,full_name,email,password_hash,role,verification_status
                   FROM users WHERE email=%s AND role=%s""",
                (email.strip(), role),
            )
            user = cursor.fetchone()
        finally:
            connection.close()

        if not user or (
            role == "customer" and user["verification_status"] != "Approved"
        ):
            return None
        try:
            password_matches = check_password_hash(
                user["password_hash"], password
            )
        except ValueError:
            password_matches = False
        if not password_matches:
            return None
        return user

    def list_orders(self) -> list[Order]:
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT o.id AS order_id,o.customer_name,
                          COALESCE(GROUP_CONCAT(DISTINCT oi.item_name
                              ORDER BY oi.item_name SEPARATOR ', '),'Bouquet') AS product_name,
                          COALESCE(SUM(oi.quantity),0) AS quantity,o.total AS total_price,
                          o.created_at AS order_date,o.order_status,
                          o.order_status AS delivery_status,'Bouquet' AS order_type,
                          'Order' AS order_kind,o.fulfillment_method
                   FROM orders o
                   LEFT JOIN order_items oi ON oi.order_id=o.id
                   GROUP BY o.id
                   ORDER BY o.created_at ASC,o.id ASC"""
            )
            return [self._order_from_row(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def list_customer_orders(self, user_id: int) -> list[Order]:
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT o.id AS order_id,o.customer_name,
                          COALESCE(GROUP_CONCAT(DISTINCT oi.item_name
                              ORDER BY oi.item_name SEPARATOR ', '),'Bouquet') AS product_name,
                          COALESCE(SUM(oi.quantity),0) AS quantity,o.total AS total_price,
                          o.created_at AS order_date,o.order_status,
                          COALESCE(d.delivery_status,o.order_status) AS delivery_status,
                          'Bouquet' AS order_type,'Order' AS order_kind,
                          o.fulfillment_method
                   FROM orders o
                   LEFT JOIN order_items oi ON oi.order_id=o.id
                   LEFT JOIN deliveries d ON d.order_id=o.id
                   WHERE o.user_id=%s
                   GROUP BY o.id,d.delivery_status
                   UNION ALL
                   SELECT c.id AS order_id,u.full_name AS customer_name,
                          CONCAT('Custom ',c.flower_type,' bouquet (',c.bouquet_size,')')
                              AS product_name,
                          c.quantity,c.total_price,c.created_at AS order_date,
                          c.status AS order_status,c.status AS delivery_status,
                          'Custom bouquet' AS order_type,'Custom' AS order_kind,
                          c.fulfillment_method
                   FROM custom_orders c
                   JOIN users u ON u.id=c.user_id
                   WHERE c.user_id=%s
                   ORDER BY order_date DESC,order_id DESC""",
                (user_id, user_id),
            )
            return [self._order_from_row(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def update_order_status(self, order_id: int, new_status: str) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT q.queue_id,q.order_type,q.order_id
                   FROM order_queue q
                   WHERE (q.order_type='Order' AND EXISTS (
                            SELECT 1 FROM orders o WHERE o.id=q.order_id
                              AND o.order_status NOT IN
                                  ('Delivered','Picked Up','Cancelled')
                         ))
                      OR (q.order_type='Custom' AND EXISTS (
                            SELECT 1 FROM custom_orders c WHERE c.id=q.order_id
                              AND c.status NOT IN
                                  ('Delivered','Picked Up','Cancelled')
                         ))
                   ORDER BY q.queue_id ASC LIMIT 1 FOR UPDATE"""
            )
            queue_head = cursor.fetchone()
            cursor.execute(
                """SELECT id,order_status,fulfillment_method,payment_method,
                          payment_status,payment_proof_filename,payment_reviewed_by
                   FROM orders WHERE id=%s FOR UPDATE""",
                (order_id,),
            )
            order = cursor.fetchone()
            if not order:
                raise ValueError("The order could not be found.")

            next_status = {
                "Pending": "Confirmed",
                "Confirmed": "Preparing",
                "Preparing": (
                    "Ready for Pickup"
                    if order["fulfillment_method"] == "Pickup"
                    else "Ready for Delivery"
                ),
                "Ready for Pickup": "Picked Up",
                "Ready for Delivery": "Out for Delivery",
                "Out for Delivery": "Delivered",
            }.get(order["order_status"])
            if new_status != next_status:
                raise ValueError("Order statuses must advance one step at a time.")
            if (
                new_status == "Confirmed"
                and order["payment_method"] == "GCash"
                and (
                    order["payment_status"] != "Paid"
                    or not order["payment_proof_filename"]
                    or not order["payment_reviewed_by"]
                )
            ):
                raise ValueError(
                    "Verify the customer's GCash proof as Paid before confirming."
                )

            cursor.execute(
                """SELECT queue_id FROM order_queue
                   WHERE order_type='Order' AND order_id=%s FOR UPDATE""",
                (order_id,),
            )
            order_queue = cursor.fetchone()
            if not order_queue:
                raise ValueError("The order is missing from the processing list.")
            if queue_head and queue_head["queue_id"] != order_queue["queue_id"]:
                raise ValueError("This order cannot be advanced yet.")

            cursor.execute(
                "UPDATE orders SET order_status=%s WHERE id=%s",
                (new_status, order_id),
            )
            cursor.execute(
                "UPDATE deliveries SET delivery_status=%s WHERE order_id=%s",
                (new_status, order_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class ProductDialog(QDialog):
    """Dialog for creating or updating one product."""

    def __init__(self, parent=None, product: Product | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit bouquet" if product else "Add bouquet")
        self.setMinimumWidth(420)
        self._name = QLineEdit()
        self._name.setMaxLength(120)
        self._flower_type = QLineEdit()
        self._flower_type.setMaxLength(80)
        self._color = QLineEdit()
        self._color.setMaxLength(50)
        self._price = QDoubleSpinBox()
        self._price.setRange(0.01, 99999999.99)
        self._price.setDecimals(2)
        self._price.setPrefix("₱ ")
        self._stock = QSpinBox()
        self._stock.setRange(0, 1000000)
        self._description = QPlainTextEdit()
        self._description.setMaximumHeight(100)

        form = QFormLayout()
        form.addRow("Bouquet name", self._name)
        form.addRow("Flower type", self._flower_type)
        form.addRow("Color", self._color)
        form.addRow("Price", self._price)
        form.addRow("Stock", self._stock)
        form.addRow("Description", self._description)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._product_id = product.product_id if product else None
        if product:
            self._name.setText(product.name)
            self._flower_type.setText(product.flower_type)
            self._color.setText(product.color)
            self._price.setValue(float(product.price))
            self._stock.setValue(product.stock)
            self._description.setPlainText(product.description)

    def _validate_and_accept(self):
        try:
            self.product()
        except ValueError as error:
            QMessageBox.warning(self, "Check bouquet details", str(error))
            return
        self.accept()

    def product(self) -> Product:
        return Product(
            product_id=self._product_id,
            name=self._name.text().strip(),
            description=self._description.toPlainText().strip(),
            flower_type=self._flower_type.text().strip(),
            color=self._color.text().strip(),
            price=Decimal(str(self._price.value())),
            stock=self._stock.value(),
        )


class ProductManagerWindow(QMainWindow):
    """Main product-management window built by extending QMainWindow."""

    def __init__(self, repository: ProductRepository):
        super().__init__()
        self._repository = repository
        self._products: list[Product] = []
        self.setWindowTitle("FuzzyWire Bouquet | Product Manager")
        self.resize(980, 620)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Bouquet", "Flower type", "Color", "Price", "Stock", "Status"]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 2, 3, 4, 5, 6):
            self._table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )

        self._show_inactive = QCheckBox("Include inactive products")
        self._show_inactive.stateChanged.connect(self.refresh_products)
        self._status = QLabel("Products are saved in the website's existing database.")
        self._add_button = QPushButton("Add bouquet")
        self._edit_button = QPushButton("Edit selected")
        self._toggle_button = QPushButton("Deactivate selected")
        self._refresh_button = QPushButton("Refresh")
        self._add_button.clicked.connect(self.add_product)
        self._edit_button.clicked.connect(self.edit_product)
        self._toggle_button.clicked.connect(self.toggle_active)
        self._refresh_button.clicked.connect(self.refresh_products)
        self._table.itemSelectionChanged.connect(self._update_action_buttons)

        actions = QHBoxLayout()
        actions.addWidget(self._add_button)
        actions.addWidget(self._edit_button)
        actions.addWidget(self._toggle_button)
        actions.addStretch()
        actions.addWidget(self._refresh_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Bouquet inventory"))
        layout.addWidget(self._show_inactive)
        layout.addLayout(actions)
        layout.addWidget(self._table)
        layout.addWidget(self._status)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.setStyleSheet(
            "QMainWindow{background:#fff9fb} QLabel{color:#302935}"
            "QPushButton{padding:8px 12px;border:1px solid #edc8d5;"
            "border-radius:8px;background:white;color:#7e2948}"
            "QPushButton:hover{background:#fff0f5}"
            "QTableWidget{background:white;border:1px solid #f0dce4;"
            "border-radius:8px;gridline-color:#f4e8ed}"
        )
        self.refresh_products()

    def refresh_products(self, *_):
        try:
            self._products = self._repository.list_products(
                self._show_inactive.isChecked()
            )
        except mysql.connector.Error as error:
            QMessageBox.critical(
                self,
                "Could not load products",
                f"Check your MySQL service and .env settings.\n\n{error}",
            )
            return

        self._table.setRowCount(len(self._products))
        for row_index, product in enumerate(self._products):
            values = (
                str(product.product_id),
                product.name,
                product.flower_type,
                product.color,
                f"₱{product.price:.2f}",
                str(product.stock),
                "Active" if product.is_active else "Inactive",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column in (0, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row_index, column, item)
        self._status.setText(f"{len(self._products)} product(s) loaded.")
        self._update_action_buttons()

    def _selected_product(self) -> Product | None:
        selected_rows = self._table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        return self._products[selected_rows[0].row()]

    def _update_action_buttons(self):
        product = self._selected_product()
        self._edit_button.setEnabled(product is not None)
        self._toggle_button.setEnabled(product is not None)
        self._toggle_button.setText(
            "Reactivate selected"
            if product and not product.is_active
            else "Deactivate selected"
        )

    def add_product(self):
        dialog = ProductDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_product(dialog.product())

    def edit_product(self):
        product = self._selected_product()
        if not product:
            return
        dialog = ProductDialog(self, product)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._repository.update(product.product_id, dialog.product())
            except mysql.connector.Error as error:
                self._show_database_error(error)
                return
            self.refresh_products()

    def _save_product(self, product: Product):
        try:
            self._repository.create(product)
        except mysql.connector.Error as error:
            self._show_database_error(error)
            return
        self.refresh_products()

    def toggle_active(self):
        product = self._selected_product()
        if not product:
            return
        activate = not product.is_active
        action = "reactivate" if activate else "deactivate"
        answer = QMessageBox.question(
            self,
            "Update product availability",
            f"Do you want to {action} “{product.name}”?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.set_active(product.product_id, activate)
        except mysql.connector.Error as error:
            self._show_database_error(error)
            return
        self.refresh_products()

    def _show_database_error(self, error: mysql.connector.Error):
        QMessageBox.critical(self, "Database operation failed", str(error))


class DesktopLoginDialog(QDialog):
    """Authenticates an existing customer or admin account for desktop access."""

    def __init__(self, repository: OrderRepository, role: str):
        super().__init__()
        self._repository = repository
        self._role = role
        self.user = None
        self.setWindowTitle(
            "Admin order management login"
            if role == "admin"
            else "Customer order tracking login"
        )
        self._email = QLineEdit()
        self._email.setPlaceholderText("Email address")
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("Email", self._email)
        form.addRow("Password", self._password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._authenticate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sign in with your existing website account."))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _authenticate(self):
        if not self._email.text().strip() or not self._password.text():
            QMessageBox.warning(self, "Sign in", "Enter your email and password.")
            return
        try:
            self.user = self._repository.authenticate_user(
                self._email.text(), self._password.text(), self._role
            )
        except mysql.connector.Error as error:
            QMessageBox.critical(self, "Could not sign in", str(error))
            return
        if not self.user:
            QMessageBox.warning(
                self,
                "Sign in failed",
                "The credentials are invalid or the customer account is not approved.",
            )
            self._password.clear()
            return
        self.accept()


class AdminOrderManagerWindow(QMainWindow):
    """Lets an authenticated admin review and advance regular bouquet orders."""

    def __init__(self, repository: OrderRepository):
        super().__init__()
        self._repository = repository
        self._orders: list[Order] = []
        self._visible_orders: list[Order] = []
        self.setWindowTitle("FuzzyWire Bouquet | Admin Order Management")
        self.resize(1120, 650)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by order, customer, bouquet, or status")
        self._search.returnPressed.connect(self.search_order)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            [
                "Order ID", "Customer Name", "Product/Bouquet Name", "Quantity",
                "Total Price", "Order Date", "Order Status",
            ]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 1, 3, 4, 5, 6):
            self._table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._table.currentCellChanged.connect(self._selection_changed)

        self._next_status = QComboBox()
        self._next_status.setEnabled(False)
        self._update_button = QPushButton("Update selected order")
        self._refresh_button = QPushButton("Refresh")
        self._clear_button = QPushButton("Clear")
        self._update_button.clicked.connect(self.update_order_status)
        self._refresh_button.clicked.connect(self.refresh_orders)
        self._clear_button.clicked.connect(self.clear_fields)
        self._status = QLabel("Manage customer orders.")

        search_row = QHBoxLayout()
        search_row.addWidget(self._search)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.search_order)
        search_row.addWidget(search_button)
        search_row.addWidget(self._clear_button)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("Next status"))
        actions.addWidget(self._next_status)
        actions.addWidget(self._update_button)
        actions.addStretch()
        actions.addWidget(self._refresh_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Regular bouquet orders"))
        layout.addLayout(search_row)
        layout.addLayout(actions)
        layout.addWidget(self._table)
        layout.addWidget(self._status)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.setStyleSheet(
            "QMainWindow{background:#fff9fb} QLabel{color:#302935}"
            "QPushButton{padding:8px 12px;border:1px solid #edc8d5;"
            "border-radius:8px;background:white;color:#7e2948}"
            "QPushButton:hover{background:#fff0f5}"
            "QTableWidget{background:white;border:1px solid #f0dce4;"
            "border-radius:8px;gridline-color:#f4e8ed}"
        )
        self.load_orders()

    def load_orders(self):
        try:
            self._orders = self._repository.list_orders()
        except mysql.connector.Error as error:
            QMessageBox.critical(self, "Could not load orders", str(error))
            return
        self.search_order()

    def search_order(self):
        search_text = self._search.text().strip().casefold()
        self._visible_orders = [
            order for order in self._orders
            if not search_text
            or search_text in " ".join(
                (
                    str(order.order_id),
                    order.customer_name,
                    order.product_name,
                    order.order_status,
                )
            ).casefold()
        ]
        self._table.setRowCount(len(self._visible_orders))
        for row_index, order in enumerate(self._visible_orders):
            date_value = order.order_date.strftime("%Y-%m-%d %H:%M")
            values = (
                str(order.order_id),
                order.customer_name,
                order.product_name,
                str(order.quantity),
                f"₱{order.total_price:.2f}",
                date_value,
                order.order_status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row_index, column, item)
        self._status.setText(f"{len(self._visible_orders)} order(s) shown.")
        self._selection_changed()

    def update_order_status(self):
        order = self._selected_order()
        status = self._next_status.currentText()
        if not order or not status:
            QMessageBox.information(
                self, "Select an order", "Select an order with a next status first."
            )
            return
        try:
            self._repository.update_order_status(order.order_id, status)
        except ValueError as error:
            QMessageBox.warning(self, "Order was not updated", str(error))
            return
        except mysql.connector.Error as error:
            QMessageBox.critical(self, "Database operation failed", str(error))
            return
        self.refresh_orders()

    def refresh_orders(self):
        self.load_orders()

    def clear_fields(self):
        self._search.clear()
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._next_status.clear()
        self._next_status.setEnabled(False)
        self.search_order()

    def _selected_order(self) -> Order | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._visible_orders):
            return None
        return self._visible_orders[row]

    def _selection_changed(self, *_):
        self._next_status.clear()
        order = self._selected_order()
        next_status = None
        if order:
            next_status = {
                "Pending": "Confirmed",
                "Confirmed": "Preparing",
                "Preparing": (
                    "Ready for Pickup"
                    if order.fulfillment_method == "Pickup"
                    else "Ready for Delivery"
                ),
                "Ready for Pickup": "Picked Up",
                "Ready for Delivery": "Out for Delivery",
                "Out for Delivery": "Delivered",
            }.get(order.order_status)
        if next_status:
            self._next_status.addItem(next_status)
        self._next_status.setEnabled(bool(next_status))
        self._update_button.setEnabled(bool(next_status))


class CustomerOrderTrackingWindow(QMainWindow):
    """Shows only the authenticated customer's bouquet and custom orders."""

    def __init__(self, repository: OrderRepository, user: dict):
        super().__init__()
        self._repository = repository
        self._user = user
        self._orders: list[Order] = []
        self._visible_orders: list[Order] = []
        self.setWindowTitle("FuzzyWire Bouquet | My Orders")
        self.resize(1120, 620)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by order, bouquet, or status")
        self._search.returnPressed.connect(self.search_order)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            [
                "Order ID", "Product/Bouquet Name", "Quantity", "Total Price",
                "Order Date", "Order Status", "Delivery Status",
            ]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 2, 3, 4, 5, 6):
            self._table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._table.doubleClicked.connect(self.view_order_details)

        self._refresh_button = QPushButton("Refresh")
        self._details_button = QPushButton("View order details")
        self._details_button.clicked.connect(self.view_order_details)
        self._refresh_button.clicked.connect(self.refresh_orders)
        search_row = QHBoxLayout()
        search_row.addWidget(self._search)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.search_order)
        search_row.addWidget(search_button)
        search_row.addWidget(self._refresh_button)
        search_row.addWidget(self._details_button)

        self._status = QLabel("Your current order and delivery statuses appear here.")
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Orders for {user['full_name']}"))
        layout.addLayout(search_row)
        layout.addWidget(self._table)
        layout.addWidget(self._status)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.setStyleSheet(
            "QMainWindow{background:#fff9fb} QLabel{color:#302935}"
            "QPushButton{padding:8px 12px;border:1px solid #edc8d5;"
            "border-radius:8px;background:white;color:#7e2948}"
            "QPushButton:hover{background:#fff0f5}"
            "QTableWidget{background:white;border:1px solid #f0dce4;"
            "border-radius:8px;gridline-color:#f4e8ed}"
        )
        self.load_customer_orders()

    def load_customer_orders(self):
        try:
            self._orders = self._repository.list_customer_orders(self._user["id"])
        except mysql.connector.Error as error:
            QMessageBox.critical(self, "Could not load your orders", str(error))
            return
        self.search_order()

    def search_order(self):
        search_text = self._search.text().strip().casefold()
        self._visible_orders = [
            order for order in self._orders
            if not search_text
            or search_text in " ".join(
                (
                    str(order.order_id),
                    order.order_type,
                    order.product_name,
                    order.order_status,
                    order.delivery_status,
                )
            ).casefold()
        ]
        self._table.setRowCount(len(self._visible_orders))
        for row_index, order in enumerate(self._visible_orders):
            date_value = order.order_date.strftime("%Y-%m-%d %H:%M")
            order_label = (
                f"Custom #{order.order_id}"
                if order.order_kind == "Custom"
                else f"#{order.order_id}"
            )
            values = (
                order_label,
                order.product_name,
                str(order.quantity),
                f"₱{order.total_price:.2f}",
                date_value,
                order.order_status,
                order.delivery_status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row_index, column, item)
        self._status.setText(f"{len(self._visible_orders)} order(s) found.")

    def refresh_orders(self):
        self.load_customer_orders()

    def view_order_details(self, *_):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._visible_orders):
            QMessageBox.information(
                self, "Select an order", "Select one of your orders to view details."
            )
            return
        order = self._visible_orders[row]
        order_label = (
            f"Custom #{order.order_id}"
            if order.order_kind == "Custom"
            else f"#{order.order_id}"
        )
        date_value = order.order_date.strftime("%Y-%m-%d %H:%M")
        QMessageBox.information(
            self,
            f"Order {order_label}",
            f"Bouquet: {order.product_name}\n"
            f"Quantity: {order.quantity}\n"
            f"Total: ₱{order.total_price:.2f}\n"
            f"Order date: {date_value}\n"
            f"Order status: {order.order_status}\n"
            f"Delivery status: {order.delivery_status}",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    feature, accepted = QInputDialog.getItem(
        None,
        "FuzzyWire Bouquet Desktop",
        "Choose a desktop feature:",
        [
            "Product Manager",
            "Admin Order Management",
            "Customer Order Tracking",
        ],
        0,
        False,
    )
    if not accepted:
        return 0

    try:
        repository = OrderRepository()
        if feature == "Product Manager":
            window = ProductManagerWindow(repository)
        else:
            role = "admin" if feature == "Admin Order Management" else "customer"
            login_dialog = DesktopLoginDialog(repository, role)
            if login_dialog.exec() != QDialog.DialogCode.Accepted:
                return 0
            if role == "admin":
                window = AdminOrderManagerWindow(repository)
            else:
                window = CustomerOrderTrackingWindow(repository, login_dialog.user)
    except mysql.connector.Error as error:
        QMessageBox.critical(
            None,
            "Could not connect to the bouquet database",
            f"Start MySQL and check the root .env settings.\n\n{error}",
        )
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
