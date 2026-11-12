import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PAYMENT_PROOF_DIR = BASE_DIR / "private_uploads" / "payment_proofs"
PAYMENT_PROOF_DIR.mkdir(parents=True, exist_ok=True)
VERIFICATION_UPLOAD_DIR = BASE_DIR / "private_uploads" / "customer_verification"
VERIFICATION_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
PAYMENT_PROOF_MAX_BYTES = 5 * 1024 * 1024
VERIFICATION_IMAGE_MAX_BYTES = 5 * 1024 * 1024
DELIVERY_RATE_PER_KM = Decimal("20.00")
LOCAL_DELIVERY_CITY_CODE = "0403407000"
LAGUNA_PROVINCE_CODE = "0403400000"
LAGUNA_REGION_CODE = "0400000000"

# Covered delivery municipalities in Laguna (Cavinti, Lumban, Kalayaan, Pagsanjan, Santa Cruz, Pila)
ALLOWED_DELIVERY_MUNICIPALITIES = [
    {"code": "0403407000", "name": "Cavinti", "lat": 14.2464, "lng": 121.5074, "fee": Decimal("0.00"), "is_local": True},
    {"code": "0403419000", "name": "Pagsanjan", "lat": 14.2736, "lng": 121.4528, "fee": Decimal("100.00"), "is_local": False},
    {"code": "0403413000", "name": "Lumban", "lat": 14.2981, "lng": 121.4589, "fee": Decimal("100.00"), "is_local": False},
    {"code": "0403409000", "name": "Kalayaan", "lat": 14.3547, "lng": 121.5583, "fee": Decimal("100.00"), "is_local": False},
    {"code": "0403426000", "name": "Santa Cruz", "lat": 14.2789, "lng": 121.4156, "fee": Decimal("100.00"), "is_local": False},
    {"code": "0403422000", "name": "Pila", "lat": 14.2344, "lng": 121.3642, "fee": Decimal("100.00"), "is_local": False},
]
ALLOWED_DELIVERY_CITY_CODES = {m["code"] for m in ALLOWED_DELIVERY_MUNICIPALITIES}
ALLOWED_DELIVERY_CITY_CODES_CSV = ",".join(sorted(ALLOWED_DELIVERY_CITY_CODES))
ALLOWED_DELIVERY_CITY_NAMES = {
    "cavinti",
    "lumban",
    "kalayaan",
    "pagsanjan",
    "santa cruz",
    "sta. cruz",
    "sta cruz",
    "pila",
}
CUSTOM_BOUQUET_COLORS = ("Blue", "Pink", "Violet White", "Green")
CUSTOM_FLOWERS = ["Tulips", "Gerbera", "Lily", "Rose", "Sunflower"]
CUSTOM_TIERS = {
    "1 pc": Decimal("120.00"),
    "3 pcs": Decimal("220.00"),
    "4 pcs": Decimal("270.00"),
    "6-7 pcs": Decimal("420.00"),
    "8-12 pcs": Decimal("520.00"),
    "15 pcs above": Decimal("520.00"),
}
CUSTOM_ARRANGEMENTS = ["Standard Bouquet", "Round Bouquet"]
OUTSIDE_LOCAL_DELIVERY_FEE = Decimal("100.00")
DELIVERY_DISTANCE_MAX_KM = Decimal("9999.99")
PICKUP_ADDRESS = "Ancop Subdivision, store tapat ng court, 2nd to the last housing"
SHOP_LOCATION_ADDRESS = os.getenv("SHOP_LOCATION_ADDRESS", PICKUP_ADDRESS).strip()
SHOP_LOCATION_LATITUDE = os.getenv("SHOP_LOCATION_LATITUDE", "").strip()
SHOP_LOCATION_LONGITUDE = os.getenv("SHOP_LOCATION_LONGITUDE", "").strip()
GEOCODER_URL = os.getenv(
    "GEOCODER_URL", "https://nominatim.openstreetmap.org/search"
).strip()
ROUTING_URL = os.getenv("ROUTING_URL", "https://router.project-osrm.org").rstrip("/")
SIMULATED_DELIVERY_SPEED = 10
FLOWER_BASE = {
    "Red Rose": 50,
    "Sunflower": 40,
    "Tulip": 60,
    "Lily": 70,
    "Mixed Flowers": 55,
}
SIZE_MULTIPLIER = {
    "Small": 1,
    "Medium": 1.5,
    "Large": 2,
    "1 pc": 1,
    "3 pcs": 1,
    "4 pcs": 1,
    "6-7 pcs": 1,
    "8-12 pcs": 1,
    "15 pcs above": 1,
}
WRAP_EXTRA = {"Kraft": 0, "Matte": 50, "Premium": 100}
ORDER_STATUSES = [
    "Pending",
    "Confirmed",
    "Preparing",
    "Ready for Delivery",
    "Ready for Pickup",
    "Out for Delivery",
    "Delivered",
    "Picked Up",
    "Cancelled",
]
ORDER_STAGES = [
    ("all", "All"),
    ("open", "Open"),
    ("to_pay", "To Pay"),
    ("to_ship", "To Ship"),
    ("to_receive", "To Receive"),
    ("delivered", "Delivered"),
    ("cancelled", "Cancelled"),
]


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-before-deployment")
    BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").strip().rstrip("/")
    DEV_AUTO_RELOAD = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    DEBUG = os.getenv("FLASK_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    PROPAGATE_EXCEPTIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
