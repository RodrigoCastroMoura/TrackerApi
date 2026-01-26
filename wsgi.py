"""
WSGI entry point for DocSmart API production deployment.
"""
import os
import sys

os.environ.setdefault('FLASK_ENV', 'production')

from main import create_app

app = create_app()

if app is None:
    print("ERROR: Failed to create Flask application", file=sys.stderr)
    sys.exit(1)

if __name__ == '__main__':
    app.run()
