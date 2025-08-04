import os

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')
    MONGODB_URI = os.environ.get('MONGODB_URI')
    FIREBASE_BUCKET_NAME = os.environ.get('FIREBASE_BUCKET_NAME')
    PORT = int(os.environ.get('PORT', 8000))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max file size
    
    # Email Configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER','smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))  # Default to 587 for TLS
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    APP_URL = os.environ.get('APP_URL', 'http://localhost:3000')
    APP_URL_RECOVERY = os.environ.get('APP_URL_RECOVERY', 'http://localhost:3000')
    APP_URL_DOCUMENT_SIGNATURE = os.environ.get('APP_URL_DOCUMENT_SIGNATURE', 'http://localhost:3000')
