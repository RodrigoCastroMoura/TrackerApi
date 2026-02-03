"""
WSGI entry point for DocSmart API production deployment.
"""
import os
import sys
import logging

os.environ.setdefault('FLASK_ENV', 'production')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from main import create_app
    app = create_app()
    
    if app is None:
        logger.error("Failed to create Flask application - check environment variables")
        from flask import Flask
        app = Flask(__name__)
        
        @app.route('/')
        def error_page():
            return {"error": "Application failed to start. Check MONGODB_URI configuration."}, 500
        
        @app.route('/health')
        def health():
            return {"status": "unhealthy", "reason": "MongoDB not configured"}, 503

except Exception as e:
    logger.error(f"Failed to import application: {e}")
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def error_page():
        return {"error": "Application failed to start", "details": str(e)}, 500

if __name__ == '__main__':
    app.run()
