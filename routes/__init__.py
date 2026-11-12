from routes.registry import register_routes as _register_routes
from routes import admin, customer


def register_routes(app):
    _register_routes(app, (customer, admin))
