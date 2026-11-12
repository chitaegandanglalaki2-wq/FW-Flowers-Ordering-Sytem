import re
import hashlib
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from unittest.mock import Mock, patch

from jinja2 import Environment, FileSystemLoader
from PIL import Image
import requests

from application import create_app, internal_server_error
from config import CUSTOM_BOUQUET_COLORS, LAGUNA_PROVINCE_CODE
from database import execute, query_all
from models import Order, User, Voucher
from services.common import (
    calculate_voucher_discount,
    classify_order_stage,
    delivery_address_is_in_laguna,
    delivery_fee_for_address,
    filter_orders_by_stage,
    philippine_location_data,
    resolve_delivery_address,
)
from services.workflows import (
    build_custom_confirmation_message,
    configured_shop_coordinates,
    estimate_delivery_distance_km,
    gcash_payment_details,
    normalize_gcash_number,
    parse_voucher_form,
    review_order_payment,
    save_payment_proof,
    update_gcash_number,
    update_gcash_qr,
    update_shop_location_settings,
)


ROOT_DIR = Path(__file__).resolve().parents[1]


class ApplicationStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def test_registered_endpoints_are_unique(self):
        endpoints = [
            rule.endpoint
            for rule in self.app.url_map.iter_rules()
            if rule.endpoint != "static"
        ]
        self.assertEqual(len(endpoints), len(set(endpoints)))
        self.assertEqual(len(endpoints), 100)

    def test_template_url_for_endpoints_exist(self):
        registered = set(self.app.view_functions)
        references = set()
        for path in (ROOT_DIR / "templates").rglob("*.html"):
            source = path.read_text(encoding="utf-8")
            references.update(
                re.findall(r"url_for\(['\"]([^'\"]+)['\"]", source)
            )
        self.assertEqual(references - registered, set())

    def test_templates_parse(self):
        environment = Environment(
            loader=FileSystemLoader(ROOT_DIR / "templates")
        )
        templates = list((ROOT_DIR / "templates").rglob("*.html"))
        for path in templates:
            with self.subTest(template=path.relative_to(ROOT_DIR)):
                environment.parse(path.read_text(encoding="utf-8"))

    def test_delivery_addresses_are_limited_to_valid_laguna_locations(self):
        locations = philippine_location_data()
        cavinti_code = "0403407000"
        outside_laguna = next(
            (city for city in locations["cities"].values()
             if city.get("provinceCode") != LAGUNA_PROVINCE_CODE),
            None,
        )
        self.assertIsNotNone(outside_laguna)
        self.assertTrue(
            delivery_address_is_in_laguna(LAGUNA_PROVINCE_CODE, cavinti_code)
        )
        self.assertFalse(
            delivery_address_is_in_laguna(
                outside_laguna["provinceCode"], outside_laguna["code"]
            )
        )
        self.assertFalse(delivery_address_is_in_laguna(None, cavinti_code))
        outside_province = next(
            province for province in locations["provinces"].values()
            if province["code"] != LAGUNA_PROVINCE_CODE
        )
        outside_city = next(
            city for city in locations["cities"].values()
            if city.get("provinceCode") == outside_province["code"]
        )
        with self.assertRaisesRegex(ValueError, "limited to Laguna"):
            resolve_delivery_address(
                {
                    "delivery_region_code": outside_province["regionCode"],
                    "delivery_province_code": outside_province["code"],
                    "delivery_city_code": outside_city["code"],
                    "delivery_street_address": "Main Street",
                }
            )

    def test_admin_can_view_pickup_map_pin_for_regular_and_custom_orders(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}

        regular_pickup = {
            "id": 31,
            "user_id": 8,
            "delivery_address": "Ancop Subdivision, Cavinti, Laguna",
            "fulfillment_method": "Pickup",
            "order_status": "Ready for Pickup",
            "updated_at": datetime(2026, 11, 11, 8, 0),
        }
        custom_pickup = {
            "id": 42,
            "user_id": 8,
            "delivery_address": "Ancop Subdivision, Cavinti, Laguna",
            "fulfillment_method": "Pickup",
            "status": "Ready for Pickup",
            "updated_at": datetime(2026, 11, 11, 8, 0),
        }
        with patch("routes.customer.ensure_fulfillment_schema"), patch(
            "routes.customer.ensure_gcash_schema"
        ), patch("routes.customer.ensure_delivery_map_schema"), patch(
            "routes.customer.query_one",
            side_effect=[regular_pickup, custom_pickup],
        ), patch(
            "routes.customer.configured_shop_location",
            return_value={
                "coordinates": (14.24, 121.5),
                "approximate": False,
                "shop_name": "Flower Shop",
                "address": "Unit 2, Cavinti, Laguna",
            },
        ), patch("routes.customer.get_delivery_route") as get_route, patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            regular_response = client.get("/api/orders/31/delivery-map")
            custom_response = client.get("/api/custom-orders/42/delivery-map")

        self.assertEqual(regular_response.status_code, 200)
        self.assertEqual(custom_response.status_code, 200)
        self.assertEqual(regular_response.json["fulfillment_method"], "Pickup")
        self.assertEqual(regular_response.json["start"], {
            "latitude": 14.24, "longitude": 121.5,
        })
        self.assertEqual(regular_response.json["route"], [])
        self.assertEqual(regular_response.json["shop_name"], "Flower Shop")
        self.assertEqual(
            regular_response.json["shop_address"], "Unit 2, Cavinti, Laguna"
        )
        self.assertEqual(custom_response.json["fulfillment_method"], "Pickup")
        get_route.assert_not_called()

    def test_customer_can_view_public_shop_pickup_location(self):
        client = self.app.test_client()
        location = {
            "shop_name": "Flower Shop",
            "address": "Unit 2, Barangay East, Cavinti, Laguna",
        }
        with patch("routes.customer.shop_location_details", return_value=location), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"):
            page = client.get("/shop-location")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Flower Shop", page.data)
        self.assertIn(b"Unit 2, Barangay East, Cavinti, Laguna", page.data)
        self.assertIn(b"Shop for Pick-up", page.data)

        with patch(
            "routes.customer.configured_shop_location",
            return_value={
                "coordinates": (14.24, 121.5),
                "approximate": False,
                "shop_name": "Flower Shop",
                "address": "Unit 2, Barangay East, Cavinti, Laguna",
            },
        ), patch("application.ensure_customer_verification_schema"), patch(
            "application.ensure_feedback_product_schema"
        ):
            location_response = client.get("/api/shop-location")
        self.assertEqual(location_response.status_code, 200)
        self.assertEqual(location_response.json["latitude"], 14.24)
        self.assertEqual(location_response.json["shop_name"], "Flower Shop")

    def test_checkout_continues_when_delivery_address_cannot_be_geocoded(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 7, "role": "customer"}
            user_session["cart"] = {"5": "1"}
            user_session["_csrf"] = "test-csrf"

        profile = {
            "full_name": "Customer",
            "email": "customer@example.test",
            "phone": "09171234567",
            "delivery_address": "",
            "delivery_region_code": None,
            "delivery_province_code": None,
            "delivery_city_code": None,
            "delivery_barangay_code": None,
            "delivery_barangay": "",
            "delivery_postal_code": "",
            "delivery_street_address": "",
        }
        address_fields = {
            "delivery_city_code": "0403406000",
            "delivery_province_code": LAGUNA_PROVINCE_CODE,
        }
        with patch("routes.customer.ensure_gcash_schema"), patch(
            "routes.customer.ensure_delivery_distance_schema"
        ), patch("routes.customer.ensure_fulfillment_schema"), patch(
            "routes.customer.ensure_delivery_slots_schema"
        ), patch("routes.customer.ensure_customer_profile_schema"), patch(
            "routes.customer.query_one", return_value=profile
        ), patch("routes.customer.load_customer_cart", return_value={"5": "1"}), patch(
            "routes.customer.delivery_address_is_in_laguna",
            side_effect=[False, True],
        ), patch(
            "routes.customer.resolve_delivery_address",
            return_value=("123 Main Street, Pagsanjan, Laguna", address_fields),
        ), patch(
            "routes.customer.estimate_delivery_distance_km",
            side_effect=ValueError("Address not found"),
        ), patch(
            "routes.customer.Order.create",
            return_value=(101, Decimal("500.00")),
        ) as create_order, patch("routes.customer.save_customer_cart"), patch(
            "routes.customer.send_order_email"
        ):
            response = client.post(
                "/checkout",
                data={
                    "_csrf": "test-csrf",
                    "customer_name": "Customer",
                    "phone": "09171234567",
                    "fulfillment_method": "Delivery",
                    "payment_method": "Cash",
                    "delivery_street_address": "123 Main Street",
                },
            )

        self.assertEqual(response.status_code, 302)
        order_form = create_order.call_args.args[2]
        self.assertEqual(
            order_form["delivery_address"],
            "123 Main Street, Pagsanjan, Laguna",
        )
        self.assertEqual(order_form["delivery_distance_km"], Decimal("0.00"))

    def test_admin_delivery_map_uses_approximate_line_when_road_route_fails(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}

        order = {
            "id": 31,
            "user_id": 8,
            "delivery_address": "Purok 1, Pagsanjan, Laguna",
            "fulfillment_method": "Delivery",
            "order_status": "Preparing",
            "updated_at": datetime(2026, 11, 11, 8, 0),
        }
        with patch("routes.customer.ensure_fulfillment_schema"), patch(
            "routes.customer.ensure_gcash_schema"
        ), patch("routes.customer.ensure_delivery_map_schema"), patch(
            "routes.customer.query_one", return_value=order
        ), patch(
            "routes.customer.configured_shop_location",
            return_value={
                "coordinates": (14.24, 121.5),
                "approximate": False,
                "shop_name": "Flower Shop",
                "address": "Unit 2, Cavinti, Laguna",
            },
        ), patch(
            "routes.customer.geocode_delivery_map_address",
            return_value=((14.28, 121.45), False),
        ), patch(
            "routes.customer.get_delivery_route",
            side_effect=requests.RequestException("routing offline"),
        ), patch("application.ensure_customer_verification_schema"), patch(
            "application.ensure_feedback_product_schema"
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/api/orders/31/delivery-map")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["route_is_approximate"])
        self.assertEqual(len(response.json["route"]), 2)
        self.assertGreater(response.json["route_distance_km"], 0)

    def test_delivery_fee_fallback_recognizes_cavinti_without_city_code(self):
        self.assertEqual(
            delivery_fee_for_address("Purok 1, Cavinti, Laguna"),
            Decimal("0.00"),
        )

    def test_map_geocoding_is_limited_to_laguna_and_adds_country(self):
        from services.workflows import geocode_address

        response = Mock()
        response.json.return_value = [{"lat": "14.24", "lon": "121.5"}]
        with self.app.app_context(), patch(
            "services.workflows.ensure_delivery_map_schema"
        ), patch("services.workflows.query_one", return_value=None), patch(
            "services.workflows.execute"
        ), patch("services.workflows.requests.get", return_value=response) as get, patch(
            "services.workflows.time.sleep"
        ):
            coordinates = geocode_address("Ancop Subdivision, Cavinti, Laguna")

        self.assertEqual(coordinates, (14.24, 121.5))
        query = get.call_args.kwargs["params"]
        self.assertIn("Philippines", query["q"])
        self.assertEqual(query["countrycodes"], "ph")
        self.assertTrue(query["bounded"])

    def test_map_geocoding_rejects_locations_outside_laguna(self):
        from services.workflows import geocode_address

        response = Mock()
        response.json.return_value = [{"lat": "14.24", "lon": "122.0"}]
        with self.app.app_context(), patch(
            "services.workflows.ensure_delivery_map_schema"
        ), patch("services.workflows.query_one", return_value=None), patch(
            "services.workflows.requests.get", return_value=response
        ), patch("services.workflows.time.sleep"):
            with self.assertRaisesRegex(ValueError, "within the Laguna"):
                geocode_address("An address in another province, Philippines")

    def test_delivery_map_uses_laguna_municipality_when_street_cannot_be_found(self):
        from services.workflows import geocode_delivery_map_address

        with patch(
            "services.workflows.geocode_address",
            side_effect=[ValueError("not found"), (14.28, 121.45)],
        ) as geocode:
            coordinates, approximate = geocode_delivery_map_address(
                "Street 1, Barangay Centro, Pagsanjan, Laguna, Calabarzon"
            )

        self.assertEqual(coordinates, (14.28, 121.45))
        self.assertTrue(approximate)
        self.assertEqual(
            geocode.call_args_list[1].args[0],
            "Pagsanjan, Laguna, Philippines",
        )

    def test_exact_shop_coordinates_must_be_configured_as_a_laguna_pair(self):
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_all", return_value=[]
        ), patch(
            "services.workflows.query_one", return_value=None
        ), patch("services.workflows.SHOP_LOCATION_LATITUDE", "14.24"), patch(
            "services.workflows.SHOP_LOCATION_LONGITUDE", "121.5"
        ):
            self.assertEqual(configured_shop_coordinates(), (14.24, 121.5))
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_all", return_value=[]
        ), patch(
            "services.workflows.query_one", return_value=None
        ), patch("services.workflows.SHOP_LOCATION_LATITUDE", "15.0"), patch(
            "services.workflows.SHOP_LOCATION_LONGITUDE", "121.5"
        ), self.assertRaisesRegex(ValueError, "within Laguna"):
            configured_shop_coordinates()
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_all", return_value=[]
        ), patch(
            "services.workflows.query_one", return_value=None
        ), patch("services.workflows.SHOP_LOCATION_LATITUDE", "14.24"), patch(
            "services.workflows.SHOP_LOCATION_LONGITUDE", ""
        ), self.assertRaisesRegex(ValueError, "Set both"):
            configured_shop_coordinates()

    def test_admin_shop_address_is_used_before_old_coordinates(self):
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_all",
            return_value=[
                {"setting_key": "shop_location_province_code", "setting_value": "0403400000"},
                {"setting_key": "shop_location_municipality_code", "setting_value": "0403407000"},
                {"setting_key": "shop_location_barangay", "setting_value": "Barangay East"},
                {"setting_key": "shop_location_name", "setting_value": "Flower Shop"},
                {"setting_key": "shop_location_address", "setting_value": "Unit 2, Main Road"},
            ],
        ), patch(
            "services.workflows.geocode_address", return_value=(14.31, 121.42)
        ) as geocode, patch("services.workflows.query_one", return_value=None):
            self.assertEqual(configured_shop_coordinates(), (14.31, 121.42))
        geocode.assert_called_once_with(
            "Unit 2, Main Road, Barangay East, Cavinti, Laguna, Philippines"
        )

    def test_shop_address_map_falls_back_to_approximate_municipality_pin(self):
        location_settings = [
            {"setting_key": "shop_location_province_code", "setting_value": "0403400000"},
            {"setting_key": "shop_location_municipality_code", "setting_value": "0403407000"},
            {"setting_key": "shop_location_barangay", "setting_value": "Barangay East"},
            {"setting_key": "shop_location_name", "setting_value": "Flower Shop"},
            {"setting_key": "shop_location_address", "setting_value": "Unit 2, Main Road"},
        ]
        from services.workflows import configured_shop_location

        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_all", return_value=location_settings
        ), patch("services.workflows.query_one", return_value=None), patch(
            "services.workflows.geocode_address",
            side_effect=[ValueError("not found"), (14.24, 121.5)],
        ) as geocode:
            location = configured_shop_location()
        self.assertTrue(location["approximate"])
        self.assertEqual(location["coordinates"], (14.24, 121.5))
        self.assertEqual(location["shop_name"], "Flower Shop")
        self.assertEqual(
            geocode.call_args_list[1].args[0],
            "Cavinti, Laguna, Philippines",
        )

    def test_delivery_distance_routes_from_the_configured_shop(self):
        response = Mock()
        response.json.return_value = {"routes": [{"distance": 12500}]}
        with patch(
            "services.workflows.configured_shop_coordinates",
            return_value=(14.24, 121.5),
        ), patch(
            "services.workflows.geocode_address",
            return_value=(14.28, 121.45),
        ), patch(
            "services.workflows.requests.get", return_value=response
        ) as route_request:
            distance = estimate_delivery_distance_km("Pagsanjan, Laguna")
        self.assertEqual(distance, Decimal("12.50"))
        self.assertIn("121.5,14.24;121.45,14.28", route_request.call_args.args[0])

    def test_shop_location_saves_valid_laguna_address_fields(self):
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.execute"
        ) as execute_setting, patch(
            "services.workflows.query_all",
            return_value=[
                {"setting_key": "shop_location_province_code", "setting_value": "0403400000"},
                {"setting_key": "shop_location_municipality_code", "setting_value": "0403407000"},
                {"setting_key": "shop_location_barangay", "setting_value": "Barangay East"},
                {"setting_key": "shop_location_name", "setting_value": "Flower Shop"},
                {"setting_key": "shop_location_address", "setting_value": "Unit 2, Main Road"},
            ],
        ):
            result = update_shop_location_settings(
                "0403400000", "0403407000", "Barangay East",
                "Flower Shop", "Unit 2, Main Road", 1, "14.24", "121.5", False,
            )
        self.assertEqual(result["municipality"], "Cavinti")
        self.assertEqual(result["shop_name"], "Flower Shop")
        self.assertIn("Unit 2, Main Road", result["address"])
        execute_setting.assert_called_once()
        self.assertIn("shop_location_address", execute_setting.call_args.args[1])
        self.assertIn("0403407000", execute_setting.call_args.args[1])

        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.execute"
        ) as execute_setting, self.assertRaisesRegex(ValueError, "municipality in Laguna"):
            update_shop_location_settings(
                "0403400000", "outside-laguna", "Barangay East",
                "Flower Shop", "Unit 2, Main Road", 1, "14.24", "121.5", False,
            )
        execute_setting.assert_not_called()

    def test_shop_location_marker_must_be_inside_laguna(self):
        with patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.execute"
        ) as execute_setting, self.assertRaisesRegex(ValueError, "within Laguna"):
            update_shop_location_settings(
                "0403400000", "0403407000", "Barangay East",
                "Flower Shop", "Unit 2, Main Road", 1, "15.0", "121.5", False,
            )
        execute_setting.assert_not_called()

    def test_custom_order_confirmation_includes_selected_color(self):
        message = build_custom_confirmation_message(
            {
                "id": 15,
                "flower_type": "Gerbera",
                "flower_color": "Blue",
                "bouquet_size": "Medium",
                "wrapping_style": "Kraft",
                "quantity": 1,
                "total_price": Decimal("520.00"),
                "fulfillment_method": "Pickup",
                "delivery_address": None,
                "payment_method": "Cash at Pickup",
            }
        )
        self.assertIn("Design: Gerbera, Blue, Medium, Kraft", message)

    def test_custom_order_has_flower_color_selection_and_laguna_address_filter(self):
        custom_template = (
            ROOT_DIR / "templates" / "custom_order.html"
        ).read_text(encoding="utf-8")
        address_template = (
            ROOT_DIR / "templates" / "_philippine_address_fields.html"
        ).read_text(encoding="utf-8")
        self.assertIn('name="flower_color"', custom_template)
        self.assertEqual(
            CUSTOM_BOUQUET_COLORS, ("Blue", "Pink", "Violet White", "Green")
        )
        self.assertIn("for color in bouquet_colors", custom_template)
        self.assertIn("data-allowed-province-code", address_template)

    def test_fifo_details_are_hidden_from_interface_templates(self):
        for path in (ROOT_DIR / "templates").rglob("*.html"):
            with self.subTest(template=path.relative_to(ROOT_DIR)):
                self.assertNotRegex(
                    path.read_text(encoding="utf-8"),
                    re.compile(r"\bfifo\b", re.IGNORECASE),
                )
        desktop_source = (ROOT_DIR / "oop_desktop" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(
            desktop_source, re.compile(r"\bfifo\b", re.IGNORECASE)
        )

    def test_manual_order_history_name_is_used_in_admin_interface(self):
        menu = (ROOT_DIR / "templates" / "admin" / "base_admin.html").read_text(
            encoding="utf-8"
        )
        form = (
            ROOT_DIR / "templates" / "admin" / "manual_order_form.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Add Order", menu)
        self.assertIn("Add an old order", form)

    def test_auth_pages_do_not_require_database_for_get(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/login").status_code, 200)
        self.assertEqual(client.get("/admin/login").status_code, 200)

    def test_account_recovery_pages_use_designed_auth_shell(self):
        client = self.app.test_client()
        with patch("routes.customer.ensure_password_reset_schema"), patch(
            "routes.customer.reset_token_is_valid", return_value=False
        ):
            forgot_response = client.get("/forgot-password")
            reset_response = client.get("/reset-password/expired-token")

        self.assertEqual(forgot_response.status_code, 200)
        self.assertIn(b'class="login-page-container"', forgot_response.data)
        self.assertIn(b"class=\"login-brand-panel recovery-brand-panel\"", forgot_response.data)
        self.assertNotIn(b"class=\"site-navbar", forgot_response.data)
        self.assertNotIn(b"site-footer", forgot_response.data)
        self.assertIn(b"Send reset code", forgot_response.data)
        self.assertIn(b'name="email"', forgot_response.data)

        self.assertEqual(reset_response.status_code, 200)
        self.assertNotIn(b'class="login-page-container"', reset_response.data)
        self.assertIn(b'class="navbar navbar-expand-lg navbar-light bg-white sticky-top site-navbar"', reset_response.data)
        self.assertIn(b'class="account-recovery-card"', reset_response.data)
        self.assertIn(b"site-footer", reset_response.data)
        self.assertIn(b"This reset link is no longer valid", reset_response.data)
        self.assertIn(b"Request a new code", reset_response.data)

    def test_customer_registration_shows_terms_checkbox_and_password_eye_controls(self):
        client = self.app.test_client()
        with patch("routes.customer.ensure_customer_verification_schema"):
            response = client.get("/register")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Terms and Conditions", response.data)
        self.assertIn(b"data-registration-terms required", response.data)
        self.assertIn(b"I agree to the Terms and Conditions.", response.data)
        self.assertIn(b"data-password-toggle=\"registerPassword\"", response.data)
        self.assertIn(b"data-password-toggle=\"registerConfirmPassword\"", response.data)
        self.assertIn(b"data-eye-open", response.data)
        self.assertIn(b"data-eye-closed", response.data)
        self.assertIn(b"passwordRequirements", response.data)
        self.assertIn(b"data-rule=\"uppercase\"", response.data)
        self.assertIn(b"data-rule=\"special\"", response.data)

    def test_login_font_and_admin_sidebar_use_responsive_system_styles(self):
        stylesheet = (ROOT_DIR / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        admin_template = (
            ROOT_DIR / "templates" / "admin" / "base_admin.html"
        ).read_text(encoding="utf-8")
        admin_script = (ROOT_DIR / "static" / "js" / "site.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('body.login-page-body * { font-family: "Segoe UI"', stylesheet)
        self.assertIn(".admin-floating-menu {\n  position: fixed;", stylesheet)
        self.assertIn(".admin-app .admin-layout { --bs-gutter-x: 1.35rem; padding-left: 300px; }", stylesheet)
        self.assertIn("@media (max-width: 991.98px)", stylesheet)
        self.assertIn('id="adminMenu">', admin_template)
        self.assertIn('classList.toggle("is-open"', admin_script)

    def test_admin_review_pages_show_key_details_and_confirm_risky_actions(self):
        verification = (
            ROOT_DIR / "templates" / "admin" / "verifications.html"
        ).read_text(encoding="utf-8")
        customers = (
            ROOT_DIR / "templates" / "admin" / "customers.html"
        ).read_text(encoding="utf-8")
        order_detail = (
            ROOT_DIR / "templates" / "admin" / "order_detail.html"
        ).read_text(encoding="utf-8")
        dashboard = (
            ROOT_DIR / "templates" / "admin" / "dashboard.html"
        ).read_text(encoding="utf-8")

        self.assertIn("Approve ID", verification)
        self.assertIn("Reject ID", verification)
        self.assertIn('name="rejection_reason"', verification)
        self.assertIn("data-confirm-message", verification)
        self.assertIn("Deactivate {{ c.full_name }}", customers)
        self.assertIn("admin-customer-status-summary", customers)
        self.assertIn("admin-detail-summary-grid", order_detail)
        self.assertIn("data-confirm-on-cancel", order_detail)
        self.assertIn('name="rejection_reason"', dashboard)

    def test_admin_flash_messages_are_offset_from_the_fixed_sidebar(self):
        base_template = (ROOT_DIR / "templates" / "base.html").read_text(
            encoding="utf-8"
        )
        stylesheet = (ROOT_DIR / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("admin-flashes", base_template)
        self.assertIn(".admin-app .admin-flashes { margin-left: 300px; }", stylesheet)
        self.assertIn(".admin-app .admin-flashes { margin-left: 0; }", stylesheet)

    def test_development_live_reload_is_enabled_only_when_requested(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/__dev/live-reload").status_code, 404)

        with patch("application.development_source_version", return_value="version-1"):
            self.app.config["DEV_AUTO_RELOAD"] = True
            try:
                response = client.get("/__dev/live-reload")
                page = client.get("/login")
            finally:
                self.app.config["DEV_AUTO_RELOAD"] = False

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"version": "version-1"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(b"live-reload.js", page.data)
        self.assertIn(b'version-1', page.data)

    def test_original_terms_page_is_public_and_linked_from_registration(self):
        client = self.app.test_client()
        terms_response = client.get("/terms")
        with patch("routes.customer.ensure_customer_verification_schema"):
            registration_response = client.get("/register")

        self.assertEqual(terms_response.status_code, 200)
        self.assertIn(b"FuzzyWire Bouquet", terms_response.data)
        self.assertIn(b"Personal information", terms_response.data)
        self.assertIn(b"Changes, cancellations, and concerns", terms_response.data)
        self.assertIn(b"/terms", registration_response.data)
        self.assertNotIn(b"flowershopmanila.com", registration_response.data)

    def test_privacy_page_is_public_and_describes_essential_cookies(self):
        client = self.app.test_client()
        privacy_response = client.get("/privacy")
        self.assertEqual(privacy_response.status_code, 200)
        self.assertIn(b"Privacy &amp; Cookie Policy", privacy_response.data)
        self.assertIn(b"Essential cookies", privacy_response.data)
        self.assertIn(b"session", privacy_response.data)
        self.assertIn(b"_csrf", privacy_response.data)
        self.assertIn(b"FuzzyWire Bouquet", privacy_response.data)

    def test_cookie_privacy_notification_is_rendered_in_base_template(self):
        client = self.app.test_client()
        response = client.get("/terms")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="cookiePrivacyBanner"', response.data)
        self.assertIn(b'id="cookiePrivacyDismiss"', response.data)
        self.assertIn(b"Got it", response.data)
        self.assertIn(b"Essential Cookies &amp; Privacy", response.data)
        self.assertIn(b"/privacy", response.data)

    def test_registration_rejects_missing_terms_acceptance_on_the_server(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.User.create"
        ) as create_user:
            response = client.post(
                "/register",
                data={
                    "_csrf": "test-csrf",
                    "full_name": "Ari Santos",
                    "email": "ari@example.com",
                    "password": "FlowerPass123!",
                    "confirm_password": "FlowerPass123!",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"agree to the terms and conditions", response.data.lower())
        create_user.assert_not_called()

    def test_new_customer_model_record_is_created_unverified(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (1,)
        cursor.lastrowid = 41
        with patch("models.ensure_customer_verification_schema"), patch(
            "models.db", return_value=connection
        ):
            user_id = User.create("Ari Santos", "ari@example.com", "FlowerPass123")
        self.assertEqual(user_id, 41)
        insert_sql, insert_values = cursor.execute.call_args_list[1].args
        self.assertIn("email_verified", insert_sql)
        self.assertEqual(insert_values[4], 0)
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_customer_login_has_no_admin_entry_point_and_has_password_toggle(self):
        response = self.app.test_client().get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"/admin/login", response.data)
        self.assertNotIn(b"admin_login", response.data)
        self.assertIn(b"data-password-toggle=\"loginPassword\"", response.data)
        self.assertIn(b"resend-verification-email", response.data)

    def test_admin_login_route_rejects_customer_accounts(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_customer_verification_schema"), patch(
            "routes.admin.User.authenticate",
            return_value={
                "id": 7, "role": "customer", "email_verified": True,
            },
        ):
            response = client.post(
                "/admin/login",
                data={"_csrf": "test-csrf", "email": "customer@example.com", "password": "password"},
            )
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)

    def test_admin_login_route_accepts_only_admin_role(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_customer_verification_schema"), patch(
            "routes.admin.User.authenticate",
            return_value={
                "id": 2, "role": "admin", "email_verified": True,
            },
        ), patch("routes.admin.trusted_device_exists", return_value=True):
            response = client.post(
                "/admin/login",
                data={"_csrf": "test-csrf", "email": "admin@example.com", "password": "AdminPass123"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")
        with client.session_transaction() as user_session:
            self.assertEqual(user_session["user"]["role"], "admin")
            self.assertEqual(user_session["auth_user_id"], 2)

    def test_admin_new_device_login_requires_email_otp(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        admin = {
            "id": 2,
            "role": "admin",
            "email": "admin@example.test",
            "full_name": "Admin",
            "email_verified": True,
        }

        def queue_admin_otp(user, purpose):
            from flask import session

            session["pending_otp"] = {
                "challenge_id": 2,
                "user_id": user["id"],
                "purpose": purpose,
            }

        with patch("routes.admin.ensure_customer_verification_schema"), patch(
            "routes.admin.User.authenticate", return_value=admin
        ), patch("routes.admin.trusted_device_exists", return_value=False), patch(
            "routes.admin.queue_email_otp", side_effect=queue_admin_otp
        ) as send_otp:
            response = client.post(
                "/admin/login",
                data={"_csrf": "test-csrf", "email": admin["email"], "password": "Secret123"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_called_once_with(admin, "new_device")
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)
            self.assertEqual(user_session["pending_otp"]["purpose"], "new_device")

    def test_admin_rejection_saves_reason_and_sends_customer_email(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
            user_session["user"] = {"id": 1, "role": "admin"}
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {"id": 31},
            {
                "id": 31,
                "full_name": "Ari Santos",
                "email": "ari@example.test",
                "email_verified": 1,
                "verification_status": "Pending",
                "verification_id_front_filename": "front.jpg",
                "verification_id_back_filename": "back.jpg",
                "verification_selfie_filename": "selfie.jpg",
            },
        ]
        cursor.rowcount = 1
        with patch("routes.admin.ensure_customer_verification_schema"), patch(
            "routes.admin.ensure_notification_link_schema"
        ), patch("routes.admin.db", return_value=connection), patch(
            "routes.admin.ensure_admin_operations_schema"
        ), patch("routes.admin.remove_verification_images"), patch(
            "routes.admin.log_admin_activity"
        ), patch("routes.admin.send_order_email", return_value=True) as send_email:
            response = client.post(
                "/admin/verifications/31/reject",
                data={
                    "_csrf": "test-csrf",
                    "rejection_reason": "The ID image is blurry.",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/verifications")
        update = next(
            call
            for call in cursor.execute.call_args_list
            if "verification_rejection_reason=%s" in call.args[0]
        )
        self.assertEqual(
            update.args[1], ("Rejected", "Rejected", "The ID image is blurry.", 31)
        )
        self.assertIn("The ID image is blurry.", send_email.call_args.args[2])

    def test_customer_registration_sends_otp_and_stays_signed_out(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        form_data = {
            "_csrf": "test-csrf",
            "full_name": "Ari Santos",
            "email": "ari@example.com",
            "password": "FlowerPass123!",
            "confirm_password": "FlowerPass123!",
            "terms_accepted": "on",
        }
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.query_one", return_value=None
        ), patch("routes.customer.resolve_delivery_address", return_value=("", {})), patch(
            "routes.customer.save_verification_image",
            side_effect=["front.jpg", "back.jpg", "selfie.jpg"],
        ), patch("routes.customer.User.create", return_value=77), patch(
            "routes.customer.queue_email_otp"
        ) as send_otp, patch(
            "routes.customer.notify"
        ), patch("routes.customer.notify_admins"):
            response = client.post("/register", data=form_data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_called_once_with(
            {"id": 77, "full_name": "Ari Santos", "email": "ari@example.com"},
            "registration",
        )
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)

    def test_registration_rejects_passwords_missing_requirements(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"

        test_cases = [
            ("short", "Short1!", "at least 8 characters"),
            ("no_upper", "flowerpass123!", "uppercase letter"),
            ("no_lower", "FLOWERPASS123!", "lowercase letter"),
            ("no_number", "FlowerPass!", "number"),
            ("no_special", "FlowerPass123", "special character"),
        ]

        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.query_one", return_value=None
        ), patch("routes.customer.User.create") as create_user:
            for case_name, pwd, expected_msg in test_cases:
                response = client.post(
                    "/register",
                    data={
                        "_csrf": "test-csrf",
                        "full_name": "Ari Santos",
                        "email": f"{case_name}@example.com",
                        "password": pwd,
                        "confirm_password": pwd,
                        "terms_accepted": "on",
                    },
                )
                self.assertEqual(response.status_code, 200, f"Failed on case: {case_name}")
                self.assertIn(
                    expected_msg.lower().encode("utf-8"),
                    response.data.lower(),
                    f"Expected message '{expected_msg}' not found for case: {case_name}",
                )
            create_user.assert_not_called()

    def test_customer_cannot_login_until_email_is_verified(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.User.authenticate",
            return_value={
                "id": 11,
                "role": "customer",
                "email_verified": False,
                "verification_status": "Approved",
            },
        ), patch("routes.customer.queue_email_otp") as send_otp:
            response = client.post(
                "/login",
                data={
                    "_csrf": "test-csrf",
                    "email": "unverified@example.com",
                    "password": "FlowerPass123",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_called_once()
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)

    def test_new_device_login_requires_email_code_before_creating_session(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        user = {
            "id": 21,
            "role": "customer",
            "email": "ari@example.test",
            "full_name": "Ari Santos",
            "email_verified": True,
            "account_status": "Active",
            "verification_status": "Approved",
        }
        def queue_new_device_otp(_user, purpose):
            from flask import session

            session["pending_otp"] = {
                "challenge_id": 1,
                "user_id": _user["id"],
                "purpose": purpose,
            }

        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.User.authenticate", return_value=user
        ), patch("routes.customer.trusted_device_exists", return_value=False), patch(
            "routes.customer.queue_email_otp", side_effect=queue_new_device_otp
        ) as send_otp:
            response = client.post(
                "/login",
                data={"_csrf": "test-csrf", "email": user["email"], "password": "Secret123"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_called_once_with(user, "new_device")
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)
            self.assertEqual(user_session["pending_otp"]["purpose"], "new_device")

    def test_password_reset_code_verification_issues_one_time_change_token(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
            user_session["pending_otp"] = {
                "challenge_id": 8,
                "user_id": 12,
                "purpose": "password_reset",
            }
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.verify_email_otp_challenge", return_value=True
        ) as verify_code, patch(
            "routes.customer.ensure_password_reset_schema"
        ), patch(
            "routes.customer.create_password_reset_token",
            return_value=("one-time-token", "hashed-token"),
        ):
            response = client.post(
                "/verify-otp",
                data={"_csrf": "test-csrf", "otp": "123456"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reset-password/one-time-token")
        verify_code.assert_called_once_with(8, 12, "password_reset", "123456")
        with client.session_transaction() as user_session:
            self.assertEqual(user_session["reset_token_hash"], "hashed-token")
            self.assertNotIn("pending_otp", user_session)

    def test_email_otp_is_numeric_hmac_hashed_and_single_use(self):
        from datetime import timedelta, timezone

        from services.workflows import (
            _email_otp_digest,
            create_email_otp_challenge,
            verify_email_otp_challenge,
        )

        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 91
        with self.app.app_context(), patch(
            "services.workflows.query_one", return_value=None
        ) as recent_challenge, patch(
            "services.workflows.db", return_value=connection
        ):
            challenge_id, code = create_email_otp_challenge(21, "registration")
            self.assertIn("used_at IS NULL", recent_challenge.call_args.args[0])
            stored_hash = cursor.execute.call_args.args[1][2]
            self.assertEqual(challenge_id, 91)
            self.assertRegex(code, r"^\d{6}$")
            self.assertEqual(stored_hash, _email_otp_digest(21, "registration", code))
            self.assertNotEqual(stored_hash, code)

            cursor.fetchone.return_value = {
                "id": 91,
                "code_hash": stored_hash,
                "attempts": 0,
                "expires_at": (
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    + timedelta(minutes=5)
                ),
                "verified_at": None,
            }
            cursor.rowcount = 1
            self.assertTrue(
                verify_email_otp_challenge(91, 21, "registration", code)
            )
        self.assertIn("used_at=UTC_TIMESTAMP()", cursor.execute.call_args.args[0])

    def test_password_change_token_is_not_usable_without_verified_otp_session(self):
        client = self.app.test_client()
        token = "one-time-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with patch("routes.customer.ensure_password_reset_schema"), patch(
            "routes.customer.reset_token_is_valid", return_value=True
        ):
            response = client.get(f"/reset-password/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"This reset link is no longer valid", response.data)

        with client.session_transaction() as user_session:
            user_session["reset_token_hash"] = token_hash
        with patch("routes.customer.ensure_password_reset_schema"), patch(
            "routes.customer.reset_token_is_valid", return_value=True
        ):
            response = client.get(f"/reset-password/{token}")
        self.assertIn(b"Choose a new password", response.data)
        self.assertIn(b"resetPasswordRequirements", response.data)

    def test_reset_password_enforces_strength_rules(self):
        client = self.app.test_client()
        token = "one-time-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
            user_session["reset_token_hash"] = token_hash

        with patch("routes.customer.ensure_password_reset_schema"), patch(
            "routes.customer.reset_token_is_valid", return_value=True
        ), patch("routes.customer.save_password_reset") as save_reset:
            # 1. Reject weak password
            weak_res = client.post(
                f"/reset-password/{token}",
                data={
                    "_csrf": "test-csrf",
                    "password": "weak",
                    "confirm_password": "weak",
                },
            )
            self.assertEqual(weak_res.status_code, 200)
            self.assertIn(b"at least 8 characters", weak_res.data.lower())
            save_reset.assert_not_called()

            # 2. Reject password missing special character
            no_spec_res = client.post(
                f"/reset-password/{token}",
                data={
                    "_csrf": "test-csrf",
                    "password": "WeakPassword123",
                    "confirm_password": "WeakPassword123",
                },
            )
            self.assertEqual(no_spec_res.status_code, 200)
            self.assertIn(b"special character", no_spec_res.data.lower())
            save_reset.assert_not_called()

            # 3. Accept strong password
            save_reset.return_value = True
            valid_res = client.post(
                f"/reset-password/{token}",
                data={
                    "_csrf": "test-csrf",
                    "password": "StrongPassword123!",
                    "confirm_password": "StrongPassword123!",
                },
            )
            self.assertEqual(valid_res.status_code, 302)
            self.assertEqual(valid_res.headers["Location"], "/login")
            save_reset.assert_called_once()

    def test_unknown_forgot_password_request_uses_same_otp_page(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.query_one", return_value=None
        ):
            response = client.post(
                "/forgot-password",
                data={"_csrf": "test-csrf", "email": "nobody@example.test"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        with client.session_transaction() as user_session:
            self.assertTrue(user_session["pending_otp"]["dummy"])

    def test_new_device_otp_verification_creates_a_trusted_device_and_session(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
            user_session["cart"] = {"3": 1}
            user_session["pending_otp"] = {
                "challenge_id": 9,
                "user_id": 21,
                "purpose": "new_device",
            }
        user = {
            "id": 21,
            "role": "customer",
            "email": "ari@example.test",
            "full_name": "Ari Santos",
            "account_status": "Active",
            "verification_status": "Approved",
        }
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.verify_email_otp_challenge", return_value=True
        ), patch("routes.customer.User.by_id", return_value=user), patch(
            "routes.customer.trust_device"
        ) as trust_new_device, patch(
            "services.workflows.load_customer_cart", return_value={}
        ), patch(
            "services.workflows.save_customer_cart"
        ):
            response = client.post(
                "/verify-otp",
                data={"_csrf": "test-csrf", "otp": "123456"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertIn("fuzzywire_device=", response.headers["Set-Cookie"])
        trust_new_device.assert_called_once()
        with client.session_transaction() as user_session:
            self.assertEqual(user_session["user"]["id"], 21)
            self.assertEqual(user_session["auth_user_id"], 21)
            self.assertEqual(user_session["cart"], {"3": 1})

    def test_account_status_shows_id_rejection_reason_and_resubmit_form(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 21,
                "role": "customer",
                "account_status": "Rejected",
                "verification_status": "Rejected",
                "email_verified": True,
                "verification_rejection_reason": "The ID photo is blurry.",
            }
        response = client.get("/account-status")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The ID photo is blurry.", response.data)
        self.assertIn(b"Re-submit ID", response.data)
        self.assertIn(b"verification/resubmit", response.data)

    def test_login_displays_deactivated_account_message(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.User.authenticate",
            return_value={
                "id": 22,
                "role": "customer",
                "email_verified": True,
                "account_status": "Deactivated",
                "verification_status": "Approved",
            },
        ):
            response = client.post(
                "/login",
                data={"_csrf": "test-csrf", "email": "ari@example.test", "password": "Secret123"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"account has been deactivated", response.data)
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)

    def test_customer_and_admin_roles_are_separated(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 4, "role": "admin"}
        with patch("application.query_one", return_value={"count": 0}), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"):
            response = client.get("/profile")
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Access denied", response.data)

        customer_client = self.app.test_client()
        with customer_client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 9,
                "role": "customer",
                "account_status": "Active",
                "verification_status": "Approved",
                "email_verified": True,
            }
        response = customer_client.get("/admin")
        self.assertEqual(response.status_code, 403)

    def test_logout_clears_the_entire_authenticated_session(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
            user_session["user"] = {"id": 4, "role": "admin"}
            user_session["auth_user_id"] = 4
            user_session["cart"] = {"1": 2}
            user_session["pending_otp"] = {"challenge_id": 1}
        with patch("application.ensure_customer_verification_schema"), patch(
            "application.query_one",
            return_value={
                "id": 4,
                "full_name": "Admin",
                "email": "admin@example.test",
                "role": "admin",
                "account_status": "Active",
                "verification_status": "Approved",
                "email_verified": 1,
            },
        ):
            response = client.post("/logout", data={"_csrf": "test-csrf"})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)
            self.assertNotIn("auth_user_id", user_session)
            self.assertNotIn("cart", user_session)
            self.assertNotIn("pending_otp", user_session)
            self.assertEqual(dict(user_session), {})

    def test_custom_404_and_500_pages_do_not_expose_debug_errors(self):
        client = self.app.test_client()
        not_found = client.get("/this-route-does-not-exist")
        self.assertEqual(not_found.status_code, 404)
        self.assertIn(b"Back to Home", not_found.data)

        error_app = create_app()
        error_app.config.update(TESTING=True)
        error_app.logger.disabled = True

        def fail_request():
            raise RuntimeError("private traceback detail")

        error_app.add_url_rule("/forced-error", view_func=fail_request)
        error_response = error_app.test_client().get("/forced-error")
        self.assertEqual(error_response.status_code, 500)
        self.assertIn(b"Something went wrong", error_response.data)
        self.assertNotIn(b"private traceback detail", error_response.data)

        with self.app.test_request_context("/"):
            response, status = internal_server_error(None)
        self.assertEqual(status, 500)
        self.assertIn(b"Something went wrong", response.encode())

    def test_verification_link_is_single_use_and_expiration_is_enforced_by_service(self):
        from services.workflows import verify_customer_email_token

        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {"id": 3, "user_id": 12}
        cursor.rowcount = 1
        with patch("services.workflows.db", return_value=connection):
            self.assertTrue(verify_customer_email_token("a" * 64))
        self.assertEqual(cursor.execute.call_count, 3)
        self.assertIn("email_verified=1", cursor.execute.call_args.args[0])
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()

        connection.reset_mock()
        cursor.reset_mock()
        cursor.fetchone.return_value = None
        with patch("services.workflows.db", return_value=connection):
            self.assertFalse(verify_customer_email_token("b" * 64))
        connection.rollback.assert_called_once_with()

    def test_customer_email_verification_route_consumes_only_secure_tokens(self):
        client = self.app.test_client()
        token = "A" * 43
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.verify_customer_email_token", return_value=True
        ) as verify_token:
            response = client.get(f"/verify-email/{token}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        verify_token.assert_called_once()

        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.verify_customer_email_token"
        ) as verify_token:
            response = client.get("/verify-email/invalid")
        self.assertEqual(response.status_code, 200)
        verify_token.assert_not_called()

    def test_resend_verification_email_does_not_disclose_account_existence(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.query_one", return_value=None
        ), patch("routes.customer.queue_email_otp") as send_otp:
            response = client.post(
                "/resend-verification-email",
                data={"_csrf": "test-csrf", "email": "nobody@example.com"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_not_called()

    def test_verification_email_is_branded_html_with_a_prominent_button_and_inline_logo(self):
        from services.workflows import send_customer_verification_email

        smtp = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp)
        smtp_context.__exit__ = Mock(return_value=False)
        with self.app.app_context(), patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.test",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_FROM": "flowers@example.test",
            },
        ), patch("services.workflows.smtplib.SMTP", return_value=smtp_context):
            send_customer_verification_email(
                "customer@example.test",
                "Ari Santos",
                "https://shop.example.test/verify-email/secure-token",
            )
        smtp.send_message.assert_called_once()
        email = smtp.send_message.call_args.args[0]
        html_part = email.get_body(preferencelist=("html",))
        self.assertIsNotNone(html_part)
        html = html_part.get_content()
        self.assertIn("FuzzyWire Bouquet", html)
        self.assertIn("Ari Santos", html)
        self.assertIn("VERIFY MY ACCOUNT", html)
        self.assertIn("cid:fuzzywire-logo", html)
        self.assertEqual(email.get_content_type(), "multipart/related")
        self.assertIsNone(email.get_body(preferencelist=("plain",)))

    def test_password_reset_email_is_branded_html_with_a_prominent_button_and_inline_logo(self):
        from services.workflows import send_password_reset_email

        smtp = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp)
        smtp_context.__exit__ = Mock(return_value=False)
        reset_url = "https://shop.example.test/reset-password/secure-token"
        with self.app.app_context(), patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.test",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_FROM": "flowers@example.test",
            },
        ), patch("services.workflows.smtplib.SMTP", return_value=smtp_context):
            send_password_reset_email("customer@example.test", reset_url)

        smtp.send_message.assert_called_once()
        email = smtp.send_message.call_args.args[0]
        self.assertEqual(email.get_content_type(), "multipart/related")
        self.assertIsNone(email.get_body(preferencelist=("plain",)))
        html_part = email.get_body(preferencelist=("html",))
        self.assertIsNotNone(html_part)
        html = html_part.get_content()
        self.assertIn("FuzzyWire Bouquet", html)
        self.assertIn(reset_url, html)
        self.assertIn("RESET MY PASSWORD", html)
        self.assertIn("30 minutes", html)
        self.assertIn("cid:fuzzywire-logo", html)

    def test_verification_code_email_uses_branded_html_without_a_plain_text_body(self):
        from services.workflows import send_customer_email_otp

        smtp = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp)
        smtp_context.__exit__ = Mock(return_value=False)
        with self.app.app_context(), patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.test",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_FROM": "flowers@example.test",
            },
        ), patch("services.workflows.smtplib.SMTP", return_value=smtp_context):
            send_customer_email_otp(
                "customer@example.test", "Ari Santos", "012345", "new_device"
            )

        email = smtp.send_message.call_args.args[0]
        html = email.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Ari Santos", html)
        self.assertIn("012345", html)
        self.assertIn("new sign-in device", html)
        self.assertIn("10 minutes", html)
        self.assertEqual(email.get_content_type(), "multipart/related")
        self.assertIsNone(email.get_body(preferencelist=("plain",)))

    def test_order_email_uses_branded_html_and_escapes_customer_content(self):
        from services.workflows import send_order_email

        smtp = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp)
        smtp_context.__exit__ = Mock(return_value=False)
        with self.app.app_context(), patch(
            "services.workflows.password_reset_email_settings",
            return_value=(
                "smtp.example.test",
                587,
                "flowers@example.test",
                "",
                "",
            ),
        ), patch("services.workflows.smtplib.SMTP", return_value=smtp_context):
            result = send_order_email(
                "customer@example.test",
                "FuzzyWire order #10 update",
                "Your order is ready.\nCustomer note: <script>alert(1)</script>",
            )

        self.assertTrue(result)
        email = smtp.send_message.call_args.args[0]
        html = email.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Your order is ready.", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertEqual(email.get_content_type(), "multipart/related")
        self.assertIsNone(email.get_body(preferencelist=("plain",)))

    def test_gmail_smtp_settings_remove_app_password_display_spaces(self):
        from services.workflows import smtp_email_settings

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "flowers@example.test",
                "SMTP_PASSWORD": "abcd efgh ijkl mnop",
                "SMTP_FROM": "flowers@example.test",
            },
        ):
            settings = smtp_email_settings()
        self.assertEqual(settings[4], "abcdefghijklmnop")

    def test_gmail_smtp_requires_account_credentials(self):
        from services.workflows import smtp_email_settings

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_FROM": "flowers@example.test",
            },
        ), patch("services.workflows.dotenv_values", return_value={}), self.assertRaisesRegex(
            ValueError, "Gmail SMTP requires"
        ):
            smtp_email_settings()

    def test_smtp_settings_fall_back_to_project_env_when_process_values_are_blank(self):
        from services.workflows import smtp_email_settings

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "",
                "SMTP_PORT": "",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_FROM": "",
            },
        ), patch(
            "services.workflows.dotenv_values",
            return_value={
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "flowers@example.test",
                "SMTP_PASSWORD": "abcdefghijklmnop",
                "SMTP_FROM": "flowers@example.test",
            },
        ):
            settings = smtp_email_settings()
        self.assertEqual(settings, (
            "smtp.gmail.com",
            587,
            "flowers@example.test",
            "flowers@example.test",
            "abcdefghijklmnop",
        ))

    def test_smtp_settings_ignore_whitespace_process_values(self):
        from services.workflows import smtp_email_settings

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": " ",
                "SMTP_PORT": " ",
                "SMTP_USERNAME": "\t",
                "SMTP_PASSWORD": " ",
                "SMTP_FROM": " ",
            },
        ), patch(
            "services.workflows.dotenv_values",
            return_value={
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "flowers@example.test",
                "SMTP_PASSWORD": "abcdefghijklmnop",
                "SMTP_FROM": "flowers@example.test",
            },
        ):
            settings = smtp_email_settings()
        self.assertEqual(settings[0:2], ("smtp.gmail.com", 587))
        self.assertEqual(settings[2:4], ("flowers@example.test", "flowers@example.test"))

    def test_forgot_password_sends_otp_before_allowing_password_reset(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["_csrf"] = "test-csrf"
        with patch("routes.customer.ensure_customer_verification_schema"), patch(
            "routes.customer.query_one",
            return_value={
                "id": 7,
                "full_name": "Ari Santos",
                "email": "customer@example.test",
            },
        ), patch("routes.customer.queue_email_otp"
        ) as send_otp:
            response = client.post(
                "/forgot-password",
                data={"_csrf": "test-csrf", "email": "customer@example.test"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-otp")
        send_otp.assert_called_once_with(
            {
                "id": 7,
                "full_name": "Ari Santos",
                "email": "customer@example.test",
            },
            "password_reset",
        )

    def test_customer_verification_urls_use_the_configured_https_site(self):
        from services.workflows import customer_email_verification_url

        with self.app.test_request_context("/"):
            original_base_url = self.app.config["BASE_URL"]
            self.app.config["BASE_URL"] = "https://flowers.example.test"
            self.assertEqual(
                customer_email_verification_url("A" * 43),
                "https://flowers.example.test/verify-email/" + "A" * 43,
            )
            self.app.config["BASE_URL"] = "http://untrusted.example.test"
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                customer_email_verification_url("A" * 43)
            self.app.config["BASE_URL"] = original_base_url

    def test_customer_and_admin_routes_require_authentication(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/profile").status_code, 302)
        self.assertEqual(client.get("/admin").status_code, 302)
        self.assertEqual(client.get("/admin/payments").status_code, 302)
        self.assertEqual(client.get("/admin/vouchers").status_code, 302)
        self.assertEqual(client.get("/admin/settings/gcash").status_code, 302)
        self.assertEqual(client.get("/admin/settings/shop-location").status_code, 302)
        self.assertEqual(client.get("/vouchers").status_code, 302)

    def test_post_requires_csrf_token(self):
        response = self.app.test_client().post("/logout")
        self.assertEqual(response.status_code, 400)

    def test_order_stage_classification_and_filtering(self):
        orders = [
            {
                "order_status": "Pending",
                "payment_method": "GCash",
                "payment_status": "Pending",
            },
            {"order_status": "Delivered", "payment_method": "Cash on Delivery"},
            {"order_status": "Cancelled", "payment_method": "Cash on Delivery"},
        ]
        self.assertEqual(classify_order_stage(orders[0]), "to_pay")
        selected, counts, active_stage = filter_orders_by_stage(orders, "delivered")
        self.assertEqual(active_stage, "delivered")
        self.assertEqual(len(selected), 1)
        self.assertEqual(counts["all"], 3)
        self.assertEqual(counts["cancelled"], 1)
        open_orders, counts, active_stage = filter_orders_by_stage(orders, "open")
        self.assertEqual(active_stage, "open")
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(counts["open"], 1)

    def test_percent_voucher_discount(self):
        voucher = {
            "is_active": 1,
            "expires_at": None,
            "max_uses": None,
            "used_count": 0,
            "minimum_subtotal": Decimal("10.00"),
            "discount_type": "Percent",
            "discount_value": Decimal("15.00"),
        }
        self.assertEqual(
            calculate_voucher_discount(voucher, Decimal("100.00"), False),
            Decimal("15.00"),
        )

    def test_voucher_form_parses_name_discount_claim_quantity_and_expiry(self):
        parsed = parse_voucher_form({
            "name": "  Welcome Gift ",
            "code": " welcome-15 ",
            "discount_type": "Percent",
            "discount_value": "15",
            "minimum_subtotal": "500",
            "max_claims": "25",
            "expires_at": "2030-12-31T23:59",
        })
        self.assertEqual(parsed[:6], (
            "Welcome Gift", "WELCOME-15", "Percent", Decimal("15"),
            Decimal("500"), 25,
        ))
        self.assertEqual(parsed[6], datetime(2030, 12, 31, 23, 59))

    def test_voucher_claim_is_transactional_and_prevents_duplicates_or_overclaim(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {
                "id": 9, "is_active": 1, "expires_at": None, "max_claims": 2,
                "max_uses": None, "used_count": 0,
            },
            {"count": 1},
            None,
        ]
        with patch("models.ensure_promo_schema"), patch(
            "models.db", return_value=connection
        ):
            Voucher.claim(9, 7)
        self.assertEqual(cursor.execute.call_count, 4)
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()

        connection.reset_mock()
        cursor.reset_mock()
        cursor.fetchone.side_effect = [
            {
                "id": 9, "is_active": 1, "expires_at": None, "max_claims": 2,
                "max_uses": None, "used_count": 0,
            },
            {"count": 2},
        ]
        with patch("models.ensure_promo_schema"), patch(
            "models.db", return_value=connection
        ):
            with self.assertRaisesRegex(ValueError, "fully claimed"):
                Voucher.claim(9, 8)
        self.assertEqual(cursor.execute.call_count, 2)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_voucher_edit_respects_claim_counts_and_delete_preserves_used_records(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {"id": 9, "is_active": 0, "used_count": 0},
            {"count": 2},
            {"count": 1},
        ]
        voucher_data = {
            "name": "Welcome Gift", "code": "WELCOME15",
            "discount_type": "Percent", "discount_value": Decimal("15"),
            "minimum_subtotal": Decimal("0"), "max_claims": 25,
            "expires_at": None, "is_active": 1,
        }
        with patch("models.ensure_promo_schema"), patch(
            "models.db", return_value=connection
        ):
            self.assertFalse(Voucher.update(9, voucher_data))
        self.assertIn("UPDATE promo_vouchers", cursor.execute.call_args.args[0])
        connection.commit.assert_called_once_with()

        connection.reset_mock()
        cursor.reset_mock()
        cursor.fetchone.side_effect = [
            {"id": 9, "code": "WELCOME15", "used_count": 0},
            {"count": 1},
        ]
        with patch("models.ensure_promo_schema"), patch(
            "models.db", return_value=connection
        ):
            with self.assertRaisesRegex(ValueError, "customer activity"):
                Voucher.delete_if_unused(9)
        self.assertEqual(cursor.execute.call_count, 2)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_delivery_fee_is_free_in_cavinti_and_flat_outside(self):
        self.assertEqual(
            delivery_fee_for_address(
                "Purok 1, Cavinti, Laguna", city_code="0403407000"
            ),
            Decimal("0.00"),
        )
        self.assertEqual(
            delivery_fee_for_address("Purok 1, Pagsanjan, Laguna"),
            Decimal("100.00"),
        )
        pagsanjan_code = next(
            city["code"]
            for city in philippine_location_data()["cities"].values()
            if city["name"].strip().casefold() == "pagsanjan"
        )
        self.assertEqual(
            delivery_fee_for_address(
                "Purok 1, Pagsanjan, Laguna", city_code=pagsanjan_code
            ),
            Decimal("100.00"),
        )
        self.assertEqual(
            delivery_fee_for_address("Shop pickup", fulfillment_method="Pickup"),
            Decimal("0.00"),
        )

    def test_regular_order_saves_flat_delivery_fee_into_order_total(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (1,)
        cursor.lastrowid = 404
        product = {
            "id": 5, "name": "Rose Bouquet", "flower_type": "Rose",
            "color": "Red", "price": Decimal("500"), "stock": 10, "is_active": 1,
        }
        order_form = {
            "fulfillment_method": "Delivery",
            "delivery_address": "Purok 1, Pagsanjan, Laguna",
            "delivery_city_code": "0403406000",
            "delivery_distance_km": Decimal("5"),
            "customer_name": "Customer",
            "phone": "09171234567",
            "delivery_notes": "",
            "gift_message": "",
            "payment_method": "Cash on Delivery",
            "promo_code": "",
            "delivery_slot_id": "",
        }
        with self.app.test_request_context("/"), patch(
            "models.ensure_gift_message_schema"
        ), patch("models.ensure_promo_schema"), patch(
            "models.ensure_delivery_slots_schema"
        ), patch("models.ensure_order_queue_schema"), patch(
            "models.ensure_notification_link_schema"
        ), patch("models.Product.get", return_value=product), patch(
            "models.delivery_fee_for_address", return_value=Decimal("100.00")
        ) as fee_for_address, patch("models.db", return_value=connection):
            order_id, total = Order.create(
                {"id": 7, "email": "customer@example.test"},
                {"5": "1"},
                order_form,
            )
        self.assertEqual(order_id, 404)
        self.assertEqual(total, Decimal("600.00"))
        fee_for_address.assert_called_once_with(
            order_form["delivery_address"], order_form["delivery_city_code"], "Delivery"
        )
        order_insert = next(
            call for call in cursor.execute.call_args_list
            if "INSERT INTO orders" in call.args[0]
        )
        self.assertEqual(order_insert.args[1][10], Decimal("100.00"))
        connection.commit.assert_called_once_with()

    def test_address_data_loads_from_project_static_directory(self):
        data = philippine_location_data()
        self.assertIn("regions", data)
        self.assertIn("provinces", data)
        self.assertIn("cities", data)
        self.assertTrue(data["cities"])

    def test_gcash_details_include_optional_qr_filename(self):
        with patch.dict(
            "os.environ",
            {"GCASH_ACCOUNT_NAME": "Shop Name", "GCASH_NUMBER": "09123456789"},
        ), patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_one",
            side_effect=[
                {"setting_value": "09987654321"},
                {"setting_value": "gcash-qr-current.png"},
            ],
        ):
            details = gcash_payment_details()
        self.assertEqual(
            details,
            {
                "name": "Shop Name",
                "number": "09987654321",
                "qr_filename": "gcash-qr-current.png",
            },
        )

    def test_gcash_number_validation_normalizes_common_ph_formats(self):
        self.assertEqual(normalize_gcash_number("+63 917-123-4567"), "09171234567")
        self.assertEqual(normalize_gcash_number("639171234567"), "09171234567")
        with self.assertRaisesRegex(ValueError, "valid Philippine"):
            normalize_gcash_number("091234")

    def test_gcash_number_update_persists_normalized_database_value(self):
        with patch("services.workflows.ensure_gcash_settings_schema") as ensure_schema, patch(
            "services.workflows.execute"
        ) as execute_setting:
            result = update_gcash_number("+63 917-123-4567", 12)
        self.assertEqual(result, "09171234567")
        ensure_schema.assert_called_once_with()
        self.assertEqual(
            execute_setting.call_args.args[1],
            ("09171234567", 12),
        )

    def test_gcash_qr_upload_validates_image_and_persists_unique_filename(self):
        image_bytes = BytesIO()
        Image.new("RGB", (20, 20), "white").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        with tempfile.TemporaryDirectory() as upload_dir, patch(
            "services.workflows.UPLOAD_DIR", Path(upload_dir)
        ), patch("services.workflows.ensure_gcash_settings_schema"), patch(
            "services.workflows.query_one", return_value=None
        ), patch("services.workflows.execute") as save_setting:
            filename = update_gcash_qr(
                Mock(filename="qr.png", stream=image_bytes), 12
            )
            self.assertTrue((Path(upload_dir) / filename).is_file())
        self.assertRegex(filename, r"^gcash-qr-[0-9a-f]{32}\.png$")
        self.assertEqual(save_setting.call_args.args[1], (filename, 12))

    def test_gcash_qr_upload_rejects_non_image_files(self):
        with self.assertRaisesRegex(ValueError, "PNG, JPG"):
            update_gcash_qr(
                Mock(filename="qr.pdf", stream=BytesIO(b"not an image")), 12
            )

    def test_admin_gcash_setting_saves_valid_number_and_confirms_success(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.update_gcash_number", return_value="09171234567"
        ) as update_number, patch("routes.admin.log_admin_activity"), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/settings/gcash",
                data={"_csrf": "test-csrf", "gcash_number": "+639171234567"},
            )
        self.assertEqual(response.status_code, 302)
        update_number.assert_called_once_with("+639171234567", 1)
        with client.session_transaction() as user_session:
            self.assertIn(
                ("success", "GCash number updated successfully."),
                user_session["_flashes"],
            )

    def test_admin_gcash_settings_accepts_qr_image_upload(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.update_gcash_number", return_value="09171234567"
        ), patch(
            "routes.admin.update_gcash_qr", return_value="gcash-qr-test.png"
        ) as update_qr, patch("routes.admin.log_admin_activity"), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/settings/gcash",
                data={
                    "_csrf": "test-csrf",
                    "gcash_number": "09171234567",
                    "gcash_qr": (BytesIO(b"image bytes"), "qr.png"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        update_qr.assert_called_once()
        with client.session_transaction() as user_session:
            self.assertIn(
                ("success", "GCash number and QR code updated successfully."),
                user_session["_flashes"],
            )

    def test_admin_gcash_settings_shows_prominent_number_input_and_full_floating_menu(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.query_one",
            return_value={"setting_value": "09171234567"},
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/admin/settings/gcash")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="gcash_name"', response.data)
        self.assertIn(b'name="gcash_number"', response.data)
        self.assertIn(b'name="gcash_qr"', response.data)
        self.assertIn(b'enctype="multipart/form-data"', response.data)
        self.assertIn(b'value="09171234567"', response.data)
        self.assertIn(b"Payment number and QR code", response.data)
        self.assertIn(b'id="adminMenu"', response.data)
        self.assertIn(b"Products &amp; Stock", response.data)
        self.assertIn(b"Payment Settings", response.data)
        self.assertIn(b"Shop Location", response.data)
        self.assertIn(b"Customer Messages", response.data)

    def test_admin_gcash_settings_saves_account_name(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.normalize_gcash_number", return_value="09171234567"
        ), patch(
            "routes.admin.update_gcash_name", return_value="Jayvee Sandoval"
        ) as update_name, patch(
            "routes.admin.update_gcash_number", return_value="09171234567"
        ), patch("routes.admin.log_admin_activity"), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/settings/gcash",
                data={
                    "_csrf": "test-csrf",
                    "gcash_name": "Jayvee Sandoval",
                    "gcash_number": "+639171234567",
                },
            )
        self.assertEqual(response.status_code, 302)
        update_name.assert_called_once_with("Jayvee Sandoval", 1)

    def test_static_directory_guard_and_security_headers(self):
        client = self.app.test_client()
        response = client.get("/static/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_admin_shop_location_page_shows_address_fields_without_coordinates(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        location = {
            "province_code": "0403400000",
            "province": "Laguna",
            "municipality_code": "0403407000",
            "municipality": "Cavinti",
            "barangay": "Barangay East",
            "shop_name": "Flower Shop",
            "complete_address": "Unit 2, Main Road",
            "address": "Unit 2, Main Road, Barangay East, Cavinti, Laguna",
        }
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.shop_location_details", return_value=location
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/admin/settings/shop-location")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"leaflet.css", response.data)
        self.assertIn(b"admin-shop-location.js", response.data)
        self.assertIn(b'id="shopLocationMap"', response.data)
        self.assertIn(b'name="province_code"', response.data)
        self.assertIn(b'name="municipality_code"', response.data)
        self.assertIn(b'name="barangay"', response.data)
        self.assertIn(b'name="shop_name"', response.data)
        self.assertIn(b'name="complete_address"', response.data)
        self.assertIn(b"Choose barangay", response.data)
        self.assertNotIn(b'name="latitude"', response.data)
        self.assertNotIn(b'name="longitude"', response.data)
        self.assertIn(b"Save shop location", response.data)

    def test_admin_can_save_shop_address_from_settings(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        location = {
            "province_code": "0403400000",
            "province": "Laguna",
            "municipality_code": "0403407000",
            "municipality": "Cavinti",
            "barangay": "Barangay East",
            "shop_name": "Flower Shop",
            "complete_address": "Unit 2, Main Road",
            "address": "Unit 2, Main Road, Barangay East, Cavinti, Laguna",
        }
        with patch("routes.admin.ensure_gcash_settings_schema"), patch(
            "routes.admin.shop_location_details", return_value=location.copy()
        ), patch(
            "routes.admin.update_shop_location_settings", return_value=location
        ) as update_location, patch("routes.admin.log_admin_activity"), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/settings/shop-location",
                data={
                    "_csrf": "test-csrf",
                    "province_code": "0403400000",
                    "municipality_code": "0403407000",
                    "barangay": "Barangay East",
                    "shop_name": "Flower Shop",
                    "complete_address": "Unit 2, Main Road",
                    "map_latitude": "14.31",
                    "map_longitude": "121.42",
                    "map_approximate": "false",
                },
            )
        self.assertEqual(response.status_code, 302)
        update_location.assert_called_once_with(
            "0403400000", "0403407000", "Barangay East",
            "Flower Shop", "Unit 2, Main Road", 1, "14.31", "121.42", False,
        )
        with client.session_transaction() as user_session:
            self.assertIn(
                (
                    "success",
                    "Shop location saved. Customer maps and pickup details are updated.",
                ),
                user_session["_flashes"],
            )

    def test_admin_shop_location_preview_returns_address_coordinates(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        with patch(
            "routes.admin.geocode_shop_location_address",
            return_value=((14.31, 121.42), False),
        ) as geocode, patch("application.query_one", return_value={"count": 0}):
            response = client.get(
                "/admin/settings/shop-location/preview",
                query_string={
                    "province_code": "0403400000",
                    "municipality_code": "0403407000",
                    "barangay": "Barangay East",
                    "complete_address": "Unit 2, Main Road",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {"latitude": 14.31, "longitude": 121.42, "approximate": False},
        )
        geocode.assert_called_once_with(
            "0403400000", "0403407000", "Barangay East", "Unit 2, Main Road"
        )

    def test_admin_dashboard_surfaces_gcash_number_input(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        admin_query_one_results = [
            {"setting_value": "09171234567"},
            None,
            {"orders": 0, "pending": 0, "sales": Decimal("0")},
            {"orders": 0, "pending": 0, "sales": Decimal("0")},
            {"value": 0},
            {"value": 0},
            {"value": 0},
        ]
        with patch("routes.admin.ensure_manual_order_schema"), patch(
            "routes.admin.ensure_gcash_settings_schema"
        ), patch("routes.admin.ensure_customer_delivery_confirmation_schema"), patch(
            "routes.admin.ensure_custom_materials_schema"
        ), patch("routes.admin.ensure_admin_operations_schema"), patch(
            "routes.admin.ensure_customer_verification_schema"
        ), patch("routes.admin.ensure_notification_link_schema"), patch(
            "routes.admin.ensure_order_queue_schema"
        ), patch("routes.admin.query_one", side_effect=admin_query_one_results), patch(
            "routes.admin.query_all",
            side_effect=[[], [], [], [], [], []],
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="adminMenu"', response.data)
        self.assertIn(b"Customer GCash number", response.data)
        self.assertIn(b'name="gcash_number"', response.data)
        self.assertIn(b'value="09171234567"', response.data)

    def test_admin_can_create_voucher_and_notify_customers(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        expiry = datetime(2030, 12, 31, 23, 59)
        with patch("routes.admin.ensure_promo_schema"), patch(
            "routes.admin.parse_voucher_form",
            return_value=(
                "Welcome Gift", "WELCOME-15", "Percent", Decimal("15"),
                Decimal("500"), 25, expiry,
            ),
        ), patch("routes.admin.execute") as save_voucher, patch(
            "routes.admin.log_admin_activity"
        ), patch("routes.admin.notify_customers_about_voucher") as announce, patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/vouchers",
                data={"_csrf": "test-csrf", "name": "Welcome Gift"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("INSERT INTO promo_vouchers", save_voucher.call_args.args[0])
        self.assertEqual(save_voucher.call_args.args[1][-3:], (25, 25, expiry))
        announce.assert_called_once()

    def test_admin_voucher_page_shows_claim_and_use_counts(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        voucher = {
            "id": 8, "name": "Welcome Gift", "code": "WELCOME15",
            "discount_type": "Percent", "discount_value": Decimal("15"),
            "minimum_subtotal": Decimal("500"), "max_claims": 25,
            "expires_at": datetime(2030, 12, 31, 23, 59),
            "is_active": 1, "claimed_count": 8, "redeemed_count": 3,
        }
        with patch("routes.admin.ensure_promo_schema"), patch(
            "routes.admin.query_all", return_value=[voucher]
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/admin/vouchers")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Claimed:", response.data)
        self.assertIn(b"8 / 25", response.data)
        self.assertIn(b"Used:", response.data)
        self.assertIn(b"3", response.data)

    def test_admin_materials_supports_custom_category_input(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"

        with patch("routes.admin.ensure_custom_materials_schema"), patch(
            "routes.admin.query_all",
            return_value=[
                {
                    "id": 1,
                    "name": "Red Rose",
                    "category": "Flower",
                    "unit": "stem",
                    "unit_price": Decimal("50.00"),
                    "stock": Decimal("100"),
                    "low_stock_threshold": Decimal("10"),
                    "is_active": 1,
                }
            ],
        ), patch("application.query_one", return_value={"count": 0}):
            get_res = client.get("/admin/materials")
        self.assertEqual(get_res.status_code, 200)
        self.assertIn(b"toggleNewCategoryBtn", get_res.data)
        self.assertIn(b"custom_category", get_res.data)
        self.assertIn(b"categorySuggestions", get_res.data)

        # 2. Add material with custom category
        with patch("routes.admin.ensure_custom_materials_schema"), patch(
            "routes.admin.execute"
        ) as mock_execute, patch("routes.admin.log_admin_activity"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            post_res = client.post(
                "/admin/materials",
                data={
                    "_csrf": "test-csrf",
                    "name": "Satin Ribbon Pink",
                    "category": "__other__",
                    "custom_category": "Ribbon",
                    "unit": "meter",
                    "unit_price": "25.00",
                    "stock": "50",
                    "low_stock_threshold": "5",
                },
            )
        self.assertEqual(post_res.status_code, 302)
        self.assertIn("INSERT INTO custom_materials", mock_execute.call_args.args[0])
        self.assertEqual(mock_execute.call_args.args[1][0], "Satin Ribbon Pink")
        self.assertEqual(mock_execute.call_args.args[1][1], "Ribbon")

        # 3. Edit material with custom category
        with patch("routes.admin.ensure_custom_materials_schema"), patch(
            "routes.admin.execute"
        ) as mock_edit_exec, patch("routes.admin.log_admin_activity"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            edit_res = client.post(
                "/admin/materials/1/edit",
                data={
                    "_csrf": "test-csrf",
                    "name": "Red Rose",
                    "category": "__other__",
                    "custom_category": "Fresh Flower",
                    "unit": "stem",
                    "unit_price": "55.00",
                    "stock": "80",
                    "low_stock_threshold": "10",
                    "is_active": "1",
                },
            )
        self.assertEqual(edit_res.status_code, 302)
        self.assertIn("UPDATE custom_materials", mock_edit_exec.call_args.args[0])
        self.assertEqual(mock_edit_exec.call_args.args[1][1], "Fresh Flower")

    def test_customer_home_shows_floating_collect_voucher_popup(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 7, "role": "customer", "verification_status": "Approved",
            }
        voucher = {
            "id": 3, "name": "Welcome Gift", "code": "WELCOME15",
            "discount_type": "Percent", "discount_value": Decimal("15"),
            "expires_at": None,
        }
        with patch("routes.customer.Product.all", return_value=[]), patch(
            "routes.customer.ensure_promo_schema"
        ), patch("routes.customer.query_all", return_value=[voucher]), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Claim Your FREE Voucher! Limited Time Only!", response.data)
        self.assertIn(b"voucher-float-trigger", response.data)
        self.assertIn(b"Claim this voucher", response.data)

    def test_customer_can_view_available_and_claimed_vouchers(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 7, "role": "customer", "verification_status": "Approved",
            }
        available = [{
            "id": 3, "name": "Welcome Gift", "code": "WELCOME15",
            "discount_type": "Percent", "discount_value": Decimal("15"),
            "minimum_subtotal": Decimal("0"), "max_claims": None,
            "expires_at": None, "claimed_count": 1,
        }]
        claimed = [{
            **available[0], "claimed_at": datetime(2026, 11, 1),
            "redeemed_at": None, "order_id": None, "redeemed_discount": None,
            "is_active": 1,
        }]
        with patch("routes.customer.ensure_promo_schema"), patch(
            "routes.customer.query_all", side_effect=[available, claimed]
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/vouchers")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Available vouchers", response.data)
        self.assertIn(b"My claimed vouchers", response.data)
        self.assertIn(b"WELCOME15", response.data)

    def test_voucher_preview_requires_customer_to_claim_first(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 7, "role": "customer", "verification_status": "Approved",
            }
        voucher = {
            "id": 3, "is_active": 1, "expires_at": None, "max_uses": None,
            "used_count": 0, "minimum_subtotal": Decimal("0"),
            "discount_type": "Percent", "discount_value": Decimal("15"),
        }
        with patch("routes.customer.load_customer_cart", return_value={"1": "1"}), patch(
            "routes.customer.Product.get",
            return_value={"id": 1, "price": Decimal("100"), "stock": 5, "is_active": 1},
        ), patch("routes.customer.ensure_promo_schema"), patch(
            "routes.customer.query_one", side_effect=[voucher, None]
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/vouchers/preview?code=WELCOME15")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Claim this voucher", response.json["message"])

    def test_payment_rejection_requires_a_reason_before_database_access(self):
        with patch("services.workflows.ensure_gcash_schema"), patch(
            "services.workflows.db"
        ) as connect:
            with self.assertRaisesRegex(ValueError, "Provide a reason"):
                review_order_payment("Order", 1, "reject", 2, "  ")
        connect.assert_not_called()

    def test_payment_proof_rejects_malformed_image(self):
        upload = Mock(filename="receipt.png", stream=BytesIO(b"\x89PNG\r\n\x1a\ninvalid"))
        with self.assertRaisesRegex(ValueError, "not a valid image"):
            save_payment_proof(upload)

    def test_payment_proof_saves_a_validated_image_privately(self):
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image_bytes, format="PNG")
        upload = Mock(filename="receipt.png", stream=BytesIO(image_bytes.getvalue()))
        with tempfile.TemporaryDirectory() as directory, patch(
            "services.workflows.PAYMENT_PROOF_DIR", Path(directory)
        ):
            filename = save_payment_proof(upload)
            saved_path = Path(directory) / filename
            self.assertTrue(saved_path.is_file())
            self.assertTrue(filename.startswith("payment-proof-"))
            self.assertEqual(saved_path.read_bytes(), image_bytes.getvalue())

    def test_gcash_status_macro_uses_customer_facing_labels(self):
        environment = Environment(loader=FileSystemLoader(ROOT_DIR / "templates"))
        template = environment.from_string(
            "{% from '_payment_status.html' import payment_status_badge %}"
            "{{ payment_status_badge('Pending', 'GCash') }}"
            "{{ payment_status_badge('Paid', 'GCash') }}"
            "{{ payment_status_badge('Failed', 'GCash') }}"
        )
        rendered = template.render()
        self.assertIn("Pending Verification", rendered)
        self.assertIn("Verified", rendered)
        self.assertIn("Rejected", rendered)

    def test_admin_payment_queue_displays_submitted_receipt(self):
        payment = {
            "order_type": "Order",
            "order_id": 41,
            "customer_name": "Customer Test",
            "customer_email": "customer@example.test",
            "amount": Decimal("1250.00"),
            "payment_status": "Pending",
            "payment_proof_filename": "payment-proof-test.png",
            "payment_date": "2025-01-01 10:30:00",
            "payment_review_note": None,
        }
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
        with patch("routes.admin.ensure_gcash_schema"), patch(
            "routes.admin.query_all", return_value=[payment]
        ), patch("routes.admin.query_one", return_value={"count": 1}), patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get("/admin/payments")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Customer Test", response.data)
        self.assertIn(b"1250.00", response.data)
        self.assertIn(b"Approve payment", response.data)
        self.assertIn(b"Why are you rejecting it?", response.data)

    def test_admin_can_view_payment_proof_image_inline(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}

        with tempfile.TemporaryDirectory() as directory:
            proof_path = Path(directory) / "payment-proof.png"
            Image.new("RGB", (4, 4), color="white").save(proof_path, format="PNG")
            with patch("routes.customer.payment_proof_path", return_value=proof_path):
                response = client.get("/payment-proof/Custom/5")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                response.close()

    def test_admin_can_save_a_manual_order_with_the_entered_date_and_total(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_manual_order_schema"), patch(
            "routes.admin.ensure_gcash_schema"
        ), patch(
            "routes.admin.ensure_admin_operations_schema"
        ), patch("routes.admin.Order.create_manual", return_value=503) as create_order, patch(
            "routes.admin.log_admin_activity"
        ), patch("application.ensure_customer_verification_schema"), patch(
            "application.ensure_feedback_product_schema"
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.post(
                "/admin/orders/add",
                data={
                    "_csrf": "test-csrf",
                    "order_date": "2025-01-15",
                    "customer_name": "Juan Dela Cruz",
                    "phone": "09171234567",
                    "delivery_address": "Manila",
                    "product_name": "Rose Bouquet",
                    "quantity": "2",
                    "unit_price": "1000",
                    "delivery_fee": "100",
                    "payment_status": "Paid",
                    "order_status": "Delivered",
                    "notes": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/admin/orders/503"))
        order_data = create_order.call_args.args[0]
        self.assertEqual(order_data["order_date"].isoformat(), "2025-01-15")
        self.assertEqual(order_data["total"], Decimal("2100.00"))

    def test_manual_order_model_writes_order_items_queue_and_delivery_atomically(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 503
        data = {
            "order_date": datetime(2025, 1, 15).date(),
            "customer_name": "Juan Dela Cruz",
            "phone": "09171234567",
            "delivery_address": "Manila",
            "notes": "Leave at the gate",
            "subtotal": Decimal("2000.00"),
            "delivery_fee": Decimal("100.00"),
            "total": Decimal("2100.00"),
            "payment_status": "Paid",
            "order_status": "Delivered",
            "product_name": "Rose Bouquet",
            "quantity": 2,
            "unit_price": Decimal("1000.00"),
        }
        with patch("models.ensure_manual_order_schema"), patch(
            "models.ensure_order_queue_schema"
        ), patch("models.db", return_value=connection):
            self.assertEqual(Order.create_manual(data), 503)
        self.assertEqual(cursor.execute.call_count, 4)
        order_insert = cursor.execute.call_args_list[0]
        self.assertIn("INSERT INTO orders", order_insert.args[0])
        self.assertIn("(user_id,customer_name", order_insert.args[0].replace("\n", ""))
        self.assertIn("VALUES (NULL,%s", order_insert.args[0])
        self.assertEqual(order_insert.args[1][0], data["customer_name"])
        self.assertEqual(order_insert.args[1][-1], data["order_date"])
        self.assertIn("INSERT INTO order_items", cursor.execute.call_args_list[1].args[0])
        self.assertIn("INSERT INTO order_queue", cursor.execute.call_args_list[2].args[0])
        self.assertIn("INSERT INTO deliveries", cursor.execute.call_args_list[3].args[0])
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_manual_order_rejects_invalid_quantity_before_saving(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}
            user_session["_csrf"] = "test-csrf"
        with patch("routes.admin.ensure_manual_order_schema"), patch(
            "routes.admin.ensure_admin_operations_schema"
        ), patch("routes.admin.Order.create_manual") as create_order, patch(
            "application.ensure_customer_verification_schema"
        ), patch("application.ensure_feedback_product_schema"), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/admin/orders/add",
                data={
                    "_csrf": "test-csrf", "order_date": "2025-01-15",
                    "customer_name": "Customer", "phone": "09171234567",
                    "delivery_address": "Manila", "product_name": "Rose Bouquet",
                    "quantity": "0", "unit_price": "1000", "delivery_fee": "100",
                    "payment_status": "Paid", "order_status": "Delivered",
                },
            )
        self.assertEqual(response.status_code, 400)
        create_order.assert_not_called()

    def test_cart_quantity_update_persists_customer_cart_and_returns_line_total(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 7, "role": "customer", "verification_status": "Approved",
            }
            user_session["_csrf"] = "test-csrf"
            user_session["cart"] = {"1": 2}
        product = {"id": 1, "name": "Rose Bouquet", "price": Decimal("500"), "stock": 10, "is_active": 1}
        with patch("routes.customer.Product.get", return_value=product), patch(
            "routes.customer.load_customer_cart", return_value={"1": 2}
        ), patch("routes.customer.save_customer_cart") as save_cart, patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/cart/update/1",
                data={"_csrf": "test-csrf", "quantity": "3"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["line_total"], "1500.00")
        save_cart.assert_called_once_with(7, {"1": 3})

    def test_cart_quantity_update_rejects_quantity_above_stock(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 7, "role": "customer", "verification_status": "Approved"}
            user_session["_csrf"] = "test-csrf"
        product = {"id": 1, "price": Decimal("500"), "stock": 2, "is_active": 1}
        with patch("routes.customer.Product.get", return_value=product), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/cart/update/1",
                data={"_csrf": "test-csrf", "quantity": "3"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only 2", response.json["message"])

    def test_selected_cart_checkout_rechecks_current_stock(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {
                "id": 7, "role": "customer", "verification_status": "Approved",
            }
            user_session["_csrf"] = "test-csrf"
            user_session["cart"] = {"1": 3}
        product = {"id": 1, "name": "Rose Bouquet", "stock": 2, "is_active": 1}
        with patch("routes.customer.Product.get", return_value=product), patch(
            "application.query_one", return_value={"count": 0}
        ):
            response = client.post(
                "/cart/checkout-selected",
                data={"_csrf": "test-csrf", "product_ids": "1"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/cart"))
        with client.session_transaction() as user_session:
            self.assertNotIn("selected_checkout_cart", user_session)

    def test_analytics_uses_order_date_filters_without_separate_sales_sources(self):
        client = self.app.test_client()
        with client.session_transaction() as user_session:
            user_session["user"] = {"id": 1, "role": "admin"}

        def analytics_summary(sql, params=()):
            if "COUNT(DISTINCT buyer_id)" in sql:
                return {"total": 0}
            if "AVG(rating)" in sql:
                return {"avg_rating": 0, "total": 0}
            if "FROM orders o WHERE" in sql:
                return {
                    "total": 1, "delivered": 1, "cancelled": 0,
                    "open": 0, "sales": Decimal("2100.00"),
                }
            if "FROM custom_orders c WHERE" in sql:
                return {
                    "total": 0, "delivered": 0, "cancelled": 0,
                    "open": 0, "sales": Decimal("0"),
                }
            self.fail(f"Unexpected analytics summary query: {sql}")

        with patch("routes.admin.ensure_manual_order_schema"), patch(
            "routes.admin.ensure_feedback_product_schema"
        ), patch("routes.admin.ensure_customer_delivery_confirmation_schema"), patch(
            "routes.admin.ensure_admin_operations_schema"
        ), patch("routes.admin.query_one", side_effect=analytics_summary) as query_one_mock, patch(
            "routes.admin.query_all", return_value=[]
        ) as query_all_mock, patch(
            "application.ensure_customer_verification_schema"
        ), patch(
            "application.ensure_feedback_product_schema"
        ), patch("application.query_one", return_value={"count": 0}):
            response = client.get(
                "/admin/analytics?start_date=2025-01-01&end_date=2025-01-31"
            )
        self.assertEqual(response.status_code, 200)
        statements = [call.args[0] for call in query_one_mock.call_args_list]
        self.assertTrue(any("o.created_at >= %s" in sql for sql in statements))
        analytics_sql = statements + [
            call.args[0] for call in query_all_mock.call_args_list
        ]
        self.assertFalse(any("historical_sales" in sql for sql in analytics_sql))
        self.assertTrue(any(
            "o.order_status IN ('Delivered','Picked Up')" in sql
            and "o.order_status NOT IN ('Delivered','Picked Up','Cancelled')" in sql
            for sql in statements
        ))
        self.assertTrue(any(
            "c.status IN ('Delivered','Picked Up')" in sql
            and "c.status NOT IN ('Delivered','Picked Up','Cancelled')" in sql
            for sql in statements
        ))
        self.assertEqual(
            sum("o.order_status IN ('Delivered','Picked Up')" in sql
                for sql in analytics_sql),
            3,
        )
        self.assertTrue(any(
            "c.status IN ('Delivered','Picked Up')" in sql
            for sql in analytics_sql
        ))
        self.assertIn(b"Customers with orders", response.data)
        self.assertIn(b'data-summary-toggle', response.data)
        self.assertIn(b'aria-controls="analyticsSummaryPanel"', response.data)
        self.assertIn(b'href="/admin/orders?stage=open"', response.data)
        self.assertIn(b'href="/admin/orders?stage=delivered"', response.data)
        self.assertIn(b'href="/admin/orders?stage=cancelled"', response.data)
        self.assertIn(b'href="/admin/customers"', response.data)
        self.assertIn(b"event.key === 'Escape'", response.data)
        self.assertIn(b'data-summary-close', response.data)
        self.assertIn(b"do not subtract product costs", response.data)
        self.assertIn(b"confirm cash received outside", response.data)
        self.assertIn(b"2100.00", response.data)

    @patch("database.db")
    def test_database_helpers_close_connections(self, connect):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [{"id": 7}]
        connect.return_value = connection

        self.assertEqual(query_all("SELECT id FROM products"), [{"id": 7}])
        connection.cursor.assert_called_once_with(dictionary=True)
        cursor.execute.assert_called_once_with("SELECT id FROM products", ())
        connection.close.assert_called_once_with()

        connection.reset_mock()
        cursor.reset_mock()
        cursor.lastrowid = 12
        self.assertEqual(execute("INSERT INTO products(name) VALUES (%s)", ("Rose",)), 12)
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_admin_can_access_chat_messages_api_without_html_error(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "role": "admin", "full_name": "Admin User"}
        with patch("routes.customer.query_one", return_value={"id": 10}), \
             patch("routes.customer.query_all", return_value=[]), \
             patch("routes.customer.execute"), \
             patch("routes.customer.ensure_chat_schema"):
            response = client.get("/chat/api/messages?conversation_id=10")
        self.assertEqual(response.status_code, 200)
        self.assertIn("messages", response.json)

    def test_custom_order_template_has_pricelist_flowers_and_schedule(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 2, "role": "customer", "email": "customer@example.com"}
        with patch("routes.customer.query_one", return_value={
            "full_name": "Test Customer",
            "phone": "09123456789",
            "delivery_address": "Poblacion, Cavinti, Laguna",
            "delivery_province_code": "0403400000",
            "delivery_city_code": "0403407000",
        }), patch("routes.customer.query_all", return_value=[]):
            response = client.get("/custom-order")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Tulips", html)
        self.assertIn("Gerbera", html)
        self.assertIn("Lily", html)
        self.assertIn("Rose", html)
        self.assertIn("Sunflower", html)
        self.assertIn("1 pc", html)
        self.assertIn("3 pcs", html)
        self.assertIn("4 pcs", html)
        self.assertIn("6-7 pcs", html)
        self.assertIn("8-12 pcs", html)
        self.assertIn("15 pcs above", html)
        self.assertIn("Round Bouquet", html)
        self.assertIn("Weekdays", html)
        self.assertIn("Weekends", html)

    def test_checkout_template_has_weekdays_and_weekends_schedule(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 2, "role": "customer", "email": "customer@example.com"}
            sess["cart"] = {"1": 1}
        with patch("routes.customer.query_one", return_value={
            "full_name": "Test Customer",
            "email": "customer@example.com",
            "phone": "09123456789",
            "delivery_address": "Poblacion, Cavinti, Laguna",
            "delivery_province_code": "0403400000",
            "delivery_city_code": "0403407000",
        }), patch("routes.customer.load_customer_cart", return_value={"1": 1}), \
           patch("routes.customer.Product.get", return_value={
               "id": 1,
               "name": "Rose",
               "description": "Beautiful Rose",
               "price": Decimal("100.00"),
               "image": "rose.jpg",
               "stock": 10,
               "is_active": 1,
           }), patch("routes.customer.query_all", return_value=[]):
            response = client.get("/checkout")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<option value="">Deliver anytime / No preferred time</option>', html)
        self.assertIn('<option value="Weekdays">Weekdays</option>', html)
        self.assertIn('<option value="Weekends">Weekends</option>', html)

    def test_feedback_page_displays_bouquet_photo_and_item_details(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 2, "role": "customer", "email": "customer@example.com"}
        mock_delivered_items = [{
            "order_id": 5,
            "product_id": 12,
            "item_name": "black/white Tulip Bouquet",
            "quantity": 1,
            "unit_price": Decimal("350.00"),
            "line_total": Decimal("350.00"),
            "image_filename": "black_white_tulip.jpg",
            "flower_type": "Tulip",
            "color": "black/white",
            "order_created_at": datetime(2026, 11, 10, 14, 30),
        }]
        with patch("routes.customer.query_all", side_effect=[mock_delivered_items, []]), \
             patch("routes.customer.ensure_feedback_product_schema"):
            response = client.get("/feedback")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("black/white Tulip Bouquet", html)
        self.assertIn("black_white_tulip.jpg", html)
        self.assertIn("Order #5", html)
        self.assertIn("350.00", html)
        self.assertIn("feedback-product-card", html)

    def test_admin_update_shop_location_settings_saves_with_coordinates(self):
        from services.workflows import update_shop_location_settings
        executed_queries = []
        def mock_execute(query, params=None):
            executed_queries.append((query, params))

        with patch("services.workflows.execute", side_effect=mock_execute), \
             patch("services.workflows.ensure_gcash_settings_schema"), \
             patch("services.workflows.shop_location_details", return_value={"is_configured": True}):
            result = update_shop_location_settings(
                province_code="0403400000",
                municipality_code="0403407000",
                barangay="Poblacion",
                shop_name="FuzzyWire Main Shop",
                complete_address="123 Rizal St",
                admin_id=1,
                map_latitude=14.2464,
                map_longitude=121.5074,
                map_approximate=False,
            )
        self.assertTrue(result["is_configured"])
        self.assertTrue(any("INSERT INTO app_settings" in q[0] for q in executed_queries))
        insert_call = next(q for q in executed_queries if "INSERT INTO app_settings" in q[0])
        # 7 rows * 3 params per row = 21 values
        self.assertEqual(len(insert_call[1]), 21)

    def test_customer_menu_renders_badges_for_active_counts(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 2, "role": "customer", "email": "customer@example.com"}
            sess["cart"] = {"1": 2}
            sess["wishlist"] = [1, 2, 3]

        with patch("application.query_one") as mock_qo:
            # unread_notifications, unread_chat_count, orders_res, custom_res, vouchers_res, feedback_res
            mock_qo.side_effect = [
                {"count": 9},  # unread_notifications
                {"count": 4},  # unread_chat_count
                {"count": 2},  # orders_res
                {"count": 1},  # custom_res
                {"count": 5},  # vouchers_res
                {"count": 3},  # pending_feedback_count
            ]
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Cart <span class="customer-menu-count">2</span>', html)
        self.assertIn('Notifications <span class="customer-menu-count" data-notification-count>9</span>', html)
        self.assertIn('Vouchers <span class="customer-menu-count">5</span>', html)
        self.assertIn('Wishlist <span class="customer-menu-count">3</span>', html)
        self.assertIn('Chat with us <span class="customer-menu-count">4</span>', html)
        self.assertIn('My Orders <span class="customer-menu-count">3</span>', html)
        self.assertIn('Feedback <span class="customer-menu-count">3</span>', html)

    def test_chat_api_messages_admin_handles_missing_or_invalid_id_as_json(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "role": "admin", "email": "admin@example.com"}

        with patch("routes.customer.ensure_chat_schema"):
            response = client.get("/chat/api/messages")
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertIn("error", data)

    def test_delivery_address_service_area_only_allows_6_towns(self):
        from services.workflows import delivery_address_is_in_service_area
        laguna = "0403400000"
        # Cavinti, Lumban, Kalayaan, Pagsanjan, Santa Cruz, Pila
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403407000"))  # Cavinti
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403413000"))  # Lumban
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403409000"))  # Kalayaan
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403419000"))  # Pagsanjan
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403426000"))  # Santa Cruz
        self.assertTrue(delivery_address_is_in_service_area(laguna, "0403422000"))  # Pila

        # Outside Laguna or other Laguna towns not in the 6
        self.assertFalse(delivery_address_is_in_service_area("0402100000", "0403407000"))  # Cavite
        self.assertFalse(delivery_address_is_in_service_area(laguna, "0403404000"))  # Calamba (not in 6)
        self.assertFalse(delivery_address_is_in_service_area(laguna, "0403403000"))  # Cabuyao (not in 6)

    def test_admin_menu_renders_badges_for_active_counts(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "role": "admin", "email": "admin@example.com"}

        with patch("application.query_one") as mock_qo, \
             patch("routes.admin.Product.all", return_value=[]):
            # notifications, order_res, notif_orders_res, fb_res, ver_res, chat_res, pay_res, prod_res, mat_res
            mock_qo.side_effect = [
                {"count": 7},  # admin_unread_notifications
                {"count": 3},  # pending orders
                {"count": 2},  # notif orders
                {"count": 4},  # admin_new_feedback_count
                {"count": 2},  # pending_verification_count
                {"count": 6},  # admin_unread_chat_count
                {"count": 5},  # admin_pending_payments_count
                {"count": 1},  # admin_low_stock_products_count
                {"count": 8},  # admin_low_stock_materials_count
            ]
            response = client.get("/admin/products")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-admin-notification-count>7</span>', html)
        self.assertIn('data-admin-order-count>3</span>', html)
        self.assertIn('data-admin-feedback-count>4</span>', html)
        self.assertIn('data-admin-verification-count>2</span>', html)
        self.assertIn('data-admin-chat-count>6</span>', html)
        self.assertIn('data-admin-payment-count>5</span>', html)
        self.assertIn('data-admin-product-count>1</span>', html)
        self.assertIn('data-admin-material-count>8</span>', html)
        # Total: 7 + 3 + 4 + 2 + 6 + 5 + 1 + 8 = 36
        self.assertIn('data-admin-total-count>36</span>', html)

    def test_admin_notification_unread_count_api_returns_all_badge_keys(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "role": "admin", "email": "admin@example.com"}

        with patch("routes.admin.query_one", return_value={
            "notifications": 3,
            "notif_orders": 2,
            "pending_orders": 4,
            "feedback": 1,
            "chats": 5,
            "verifications": 2,
            "payments": 3,
            "low_stock_products": 2,
            "low_stock_materials": 4,
        }):
            response = client.get("/admin/notifications/unread-count")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["notifications"], 3)
        self.assertEqual(data["orders"], 4)  # max(4, 2)
        self.assertEqual(data["feedback"], 1)
        self.assertEqual(data["chats"], 5)
        self.assertEqual(data["verifications"], 2)
        self.assertEqual(data["payments"], 3)
        self.assertEqual(data["low_stock_products"], 2)
        self.assertEqual(data["low_stock_materials"], 4)
        self.assertEqual(data["total"], 24)


if __name__ == "__main__":
    unittest.main()
