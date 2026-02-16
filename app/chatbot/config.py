import os


class ChatbotConfig:
    WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
    VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "meu_token_secreto_123")
    PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
    PASSWORD_CHATBOT_SALT = os.getenv("PASSWORD_CHATBOT_SALT", "")
    WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "https://graph.facebook.com/v18.0")
    API_BASE_URL = os.getenv("API_BASE_URL", "")
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    SESSION_SECRET = os.getenv("SESSION_SECRET", "")
