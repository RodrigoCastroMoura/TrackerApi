import os
import sys

class Config:
    # Critical Security: SECRET_KEY must be set
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
    if not SECRET_KEY:
        print("ERROR: FLASK_SECRET_KEY environment variable must be set for security")
        print("Generate a secure key with: python -c 'import secrets; print(secrets.token_hex(32))'")
        sys.exit(1)
    
    # Database Configuration
    MONGODB_URI = os.environ.get('MONGODB_URI')
    if not MONGODB_URI:
        print("ERROR: MONGODB_URI environment variable must be set")
        sys.exit(1)
    
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
