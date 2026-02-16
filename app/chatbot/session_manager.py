import logging
import threading
from typing import Optional
from datetime import datetime
from app.chatbot.models import Session
from app.chatbot.config import ChatbotConfig

logger = logging.getLogger(__name__)


class SessionManager:

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, phone_number: str) -> Session:
        with self._lock:
            session = self._sessions.get(phone_number)

            if session:
                if session.is_expired(ChatbotConfig.SESSION_TIMEOUT_MINUTES):
                    logger.info(f"Session expired for {phone_number}, creating new one")
                    session = Session(phone_number=phone_number)
                    self._sessions[phone_number] = session
                else:
                    session.refresh()
            else:
                session = Session(phone_number=phone_number)
                self._sessions[phone_number] = session

            return session

    def remove(self, phone_number: str) -> None:
        with self._lock:
            self._sessions.pop(phone_number, None)

    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [
                phone for phone, session in self._sessions.items()
                if session.is_expired(ChatbotConfig.SESSION_TIMEOUT_MINUTES)
            ]
            for phone in expired:
                del self._sessions[phone]
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired sessions")
            return len(expired)


session_manager = SessionManager()
