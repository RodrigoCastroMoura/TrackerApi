"""
WSGI entry point for DocSmart API production deployment.
"""
import os
import sys
import logging

os.environ.setdefault('FLASK_ENV', 'production')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_pid = os.getpid()

try:
    from main import create_app
    app = create_app()

    if app is None:
        logger.error(
            f"[pid={_pid}] create_app() returned None — falling back to stub app "
            f"(only '/' and '/health' respond, every other route returns a plain 404). "
            f"Check the logs above from verify_mongodb_connection()/create_app() in this same "
            f"worker's log for the real cause (usually MongoDB unreachable at boot)."
        )
        from flask import Flask
        app = Flask(__name__)

        @app.route('/')
        def error_page():
            return {"error": "Application failed to start. Check MONGODB_URI configuration.", "pid": _pid}, 500

        @app.route('/health')
        def health():
            return {"status": "unhealthy", "reason": "MongoDB not configured", "pid": _pid}, 503

except Exception as e:
    logger.error(f"[pid={_pid}] Failed to import application: {e}", exc_info=True)
    from flask import Flask
    app = Flask(__name__)

    @app.route('/')
    def error_page():
        return {"error": "Application failed to start", "details": str(e), "pid": _pid}, 500

    @app.route('/health')
    def health():
        return {"status": "unhealthy", "reason": str(e), "pid": _pid}, 503

if __name__ == '__main__':
    app.run()
