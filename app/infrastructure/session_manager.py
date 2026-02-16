import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class ChatVehicle:
    id: str
    plate: str
    model: str
    is_blocked: bool = False


@dataclass
class ChatUser:
    id: str
    name: str
    email: str
    token: str
    company_id: str = ""
    vehicles: List[ChatVehicle] = field(default_factory=list)
    intrudution_shown: bool = False


@dataclass
class ChatSession:
    phone_number: str
    state: str = "UNAUTHENTICATED"
    user: Optional[ChatUser] = None
    selected_vehicle: Optional[ChatVehicle] = None
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        elapsed = (datetime.utcnow() - self.last_activity).total_seconds() / 60
        return elapsed > timeout_minutes

    def refresh(self):
        self.last_activity = datetime.utcnow()


class SessionManager:

    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, phone_number: str) -> ChatSession:
        with self._lock:
            session = self._sessions.get(phone_number)

            if session:
                if session.is_expired(Config.SESSION_TIMEOUT_MINUTES):
                    logger.info(f"Session expired for {phone_number}, creating new one")
                    session = ChatSession(phone_number=phone_number)
                    self._sessions[phone_number] = session
                else:
                    session.refresh()
            else:
                session = ChatSession(phone_number=phone_number)
                self._sessions[phone_number] = session

            return session

    def remove(self, phone_number: str) -> None:
        with self._lock:
            self._sessions.pop(phone_number, None)

    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [
                phone for phone, session in self._sessions.items()
                if session.is_expired(Config.SESSION_TIMEOUT_MINUTES)
            ]
            for phone in expired:
                del self._sessions[phone]
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired sessions")
            return len(expired)


session_manager = SessionManager()
