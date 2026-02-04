import os
import sys
import secrets

class Config:
    # Critical Security: SECRET_KEY must be set
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or os.environ.get('SESSION_SECRET')
    if not SECRET_KEY:
        SECRET_KEY = secrets.token_hex(32)
        print("WARNING: FLASK_SECRET_KEY not set, using generated key (not recommended for production)")
    
    # Database Configuration
    MONGODB_URI = os.environ.get('MONGODB_URI')
    if not MONGODB_URI:
        print("ERROR: MONGODB_URI environment variable must be set")
        MONGODB_URI = None
    
    # Optional: Firebase Configuration
    FIREBASE_BUCKET_NAME = os.environ.get('FIREBASE_BUCKET_NAME')
    
    # Server Configuration
    PORT = int(os.environ.get('PORT', 8000))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max file size
    
    # Email Configuration (optional for development)
    MAIL_SERVER = os.environ.get('MAIL_SERVER','smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))  # Default to 587 for TLS
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    # Use MAIL_USERNAME as default sender if MAIL_DEFAULT_SENDER is not set
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', os.environ.get('MAIL_USERNAME'))

    TEMPLATE_EMAIL_PATH = os.environ.get('TEMPLATE_EMAIL', 'templates/email/sampleTemplate.txt')
    TEMPLATE_PASSWORD_PATH = os.environ.get('TEMPLATE_REENVIO_EMAIL', 'templates/email/sampleReenvioEmail.txt')
    TEMPLATE_URL = os.environ.get('TEMPLATE_URL', 'http://192.168.15.7:8000/')
    
    # Application URLs
    APP_URL = os.environ.get('APP_URL', 'http://localhost:3000')
    APP_URL_RECOVERY = os.environ.get('APP_URL_RECOVERY', 'http://localhost:3000')
    APP_URL_DOCUMENT_SIGNATURE = os.environ.get('APP_URL_DOCUMENT_SIGNATURE', 'http://localhost:3000')
    
    # CORS Configuration
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*').split(',')
    
    # Rate Limiting Configuration
    # For production, use Redis: RATELIMIT_STORAGE_URL = "redis://localhost:6379"
    RATELIMIT_STORAGE_URL = os.environ.get('RATELIMIT_STORAGE_URL', 'memory://')
    
    # Mercado Pago Webhook Security
    # IMPORTANT: Configure this in production to validate webhook signatures
    MERCADOPAGO_WEBHOOK_SECRET = os.environ.get('MERCADOPAGO_WEBHOOK_SECRET')
    MERCADOPAGO_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN')

    PASSWORG_CHATBOT_SALT = os.environ.get('PASSWORG_CHATBOT_SALT', 'default_salt_for_chatbot')
