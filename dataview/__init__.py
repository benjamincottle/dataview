import logging

from flask import Flask, render_template
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix

cache = Cache()

DEFAULTS = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DIR": "/tmp/dataview-cache",
    # Serve cached data without asking FSANZ again for this many seconds.
    "NFR_CHECK_INTERVAL": 15 * 60,
    "NFR_DATA_TTL": 30 * 24 * 60 * 60,
    "NFR_PROCESSING_TIMEOUT": 15 * 60,
    # After a failed parse, wait this long before trying the PDF again.
    "NFR_RETRY_AFTER": 5 * 60,
    "NFR_HTTP_TIMEOUT": 30,
    "NFR_MAX_PDF_BYTES": 50 * 1024 * 1024,
    "SEND_FILE_MAX_AGE_DEFAULT": 24 * 60 * 60,
}

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DEFAULTS)
    # e.g. DATAVIEW_CACHE_TYPE=FileSystemCache, DATAVIEW_NFR_CHECK_INTERVAL=600
    app.config.from_prefixed_env("DATAVIEW")
    app.config.update(config or {})

    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)
        logging.getLogger("dataview").handlers = gunicorn_logger.handlers
        logging.getLogger("dataview").setLevel(gunicorn_logger.level)

    # Behind one reverse proxy that sets X-Forwarded-For/-Proto/-Host.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    cache.init_app(app)

    from .views import bp

    app.register_blueprint(bp)

    @app.after_request
    def set_security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", message="That page doesn't exist."), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("error.html", message="Something went wrong on our side."), 500

    return app


# Entry point for `gunicorn dataview:app` and `flask --app dataview`.
app = create_app()
