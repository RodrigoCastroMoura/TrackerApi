"""
WSGI entry point for DocSmart API.

This file is used by production WSGI servers (like Gunicorn) to run the application.

Usage with Gunicorn:
    gunicorn -c gunicorn_config.py wsgi:app
"""

from main import create_app
import logging

# Create the application instance
app = create_app()

if app is None:
    logging.error("Failed to create application. Exiting.")
    import sys
    sys.exit(1)

if __name__ == '__main__':
    # This block is only executed when running directly with Python
    # For production, use: gunicorn -c gunicorn_config.py wsgi:app
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
