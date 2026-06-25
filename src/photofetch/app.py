"""Flask application factory."""

from flask import Flask, jsonify, request


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    @app.before_request
    def check_origin():
        """CSRF: reject cross-origin POST requests."""
        if request.method == "POST":
            origin = request.headers.get("Origin", "")
            if origin and not origin.startswith("http://127.0.0.1:"):
                return jsonify({"error": "forbidden"}), 403

    from photofetch.routes import usb, icloud, main, batch
    app.register_blueprint(main.bp)
    app.register_blueprint(usb.bp, url_prefix="/api/usb")
    app.register_blueprint(icloud.bp, url_prefix="/api/icloud")
    app.register_blueprint(batch.bp, url_prefix="/api/batch")

    return app
