from inspect import isfunction

from flask import Flask


def route(rule, **options):
    def decorate(view):
        if hasattr(view, "__route_options__"):
            raise RuntimeError(f"Route metadata already assigned to {view.__name__}.")
        view.__route_options__ = (rule, options.copy())
        return view

    return decorate


def register_routes(app: Flask, modules):
    registered = set()
    registered_rules = {}
    for module in modules:
        for view in vars(module).values():
            if not isfunction(view):
                continue
            route_options = getattr(view, "__route_options__", None)
            if route_options is None:
                continue
            rule, options = route_options
            endpoint = view.__name__
            methods = set(options.get("methods", ("GET",)))
            if endpoint in registered or endpoint in app.view_functions:
                raise RuntimeError(f"Duplicate Flask endpoint: {endpoint}")
            if registered_rules.get(rule, set()) & methods:
                raise RuntimeError(f"Duplicate Flask route: {rule}")
            app.add_url_rule(rule, endpoint=endpoint, view_func=view, **options)
            registered.add(endpoint)
            registered_rules.setdefault(rule, set()).update(methods)
