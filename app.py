import argparse

from application import app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FuzzyWire Bouquet.")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Reload the app and open browser pages when project files change.",
    )
    args = parser.parse_args()

    if args.dev:
        app.config.update(
            DEBUG=False,
            DEV_AUTO_RELOAD=True,
            TEMPLATES_AUTO_RELOAD=True,
            SEND_FILE_MAX_AGE_DEFAULT=0,
        )
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=True)
    else:
        app.run(host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
