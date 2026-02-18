import logging
import json
import threading
from dataclasses import dataclass, field, asdict
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
    pending_identifier: Optional[str] = None
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        elapsed = (datetime.utcnow() - self.last_activity).total_seconds() / 60
        return elapsed > timeout_minutes

    def refresh(self):
        self.last_activity = datetime.utcnow()

    def to_dict(self) -> dict:
        data = {
            "phone_number": self.phone_number,
            "state": self.state,
            "pending_identifier": self.pending_identifier,
            "last_activity": self.last_activity.isoformat(),
        }
        if self.user:
            vehicles_list = []
            for v in self.user.vehicles:
                vehicles_list.append({
                    "id": v.id,
                    "plate": v.plate,
                    "model": v.model,
                    "is_blocked": v.is_blocked,
                })
            data["user"] = {
                "id": self.user.id,
                "name": self.user.name,
                "email": self.user.email,
                "token": self.user.token,
                "company_id": self.user.company_id,
                "vehicles": vehicles_list,
                "intrudution_shown": self.user.intrudution_shown,
            }
        else:
            data["user"] = None

        if self.selected_vehicle:
            data["selected_vehicle"] = {
                "id": self.selected_vehicle.id,
                "plate": self.selected_vehicle.plate,
                "model": self.selected_vehicle.model,
                "is_blocked": self.selected_vehicle.is_blocked,
            }
        else:
            data["selected_vehicle"] = None

        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ChatSession":
        user = None
        if data.get("user"):
            u = data["user"]
            vehicles = [
                ChatVehicle(
                    id=v["id"],
                    plate=v["plate"],
                    model=v["model"],
                    is_blocked=v.get("is_blocked", False),
                )
                for v in u.get("vehicles", [])
            ]
            user = ChatUser(
                id=u["id"],
                name=u["name"],
                email=u["email"],
                token=u["token"],
                company_id=u.get("company_id", ""),
                vehicles=vehicles,
                intrudution_shown=u.get("intrudution_shown", False),
            )

        selected_vehicle = None
        if data.get("selected_vehicle"):
            sv = data["selected_vehicle"]
            selected_vehicle = ChatVehicle(
                id=sv["id"],
                plate=sv["plate"],
                model=sv["model"],
                is_blocked=sv.get("is_blocked", False),
            )

        last_activity = datetime.utcnow()
        if data.get("last_activity"):
            try:
                last_activity = datetime.fromisoformat(data["last_activity"])
            except (ValueError, TypeError):
                pass

        return cls(
            phone_number=data["phone_number"],
            state=data.get("state", "UNAUTHENTICATED"),
            user=user,
            selected_vehicle=selected_vehicle,
            pending_identifier=data.get("pending_identifier"),
            last_activity=last_activity,
        )


class RedisSessionManager:

    def __init__(self, redis_url: str):
        import redis as redis_lib
        self._redis = redis_lib.from_url(redis_url, decode_responses=True)
        self._prefix = "chatbot:session:"
        self._ttl = Config.SESSION_TIMEOUT_MINUTES * 60
        self._redis.ping()
        logger.info("Redis session manager initialized successfully")

    def _key(self, phone_number: str) -> str:
        return f"{self._prefix}{phone_number}"

    def get_or_create(self, phone_number: str) -> ChatSession:
        try:
            key = self._key(phone_number)
            raw = self._redis.get(key)

            if raw:
                try:
                    data = json.loads(raw)
                    session = ChatSession.from_dict(data)
                    if session.is_expired(Config.SESSION_TIMEOUT_MINUTES):
                        logger.info(f"Session expired for {phone_number}, creating new one")
                        session = ChatSession(phone_number=phone_number)
                    else:
                        session.refresh()
                    self._save(session)
                    return session
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(f"Failed to deserialize session for {phone_number}: {e}")

            session = ChatSession(phone_number=phone_number)
            self._save(session)
            return session
        except Exception as e:
            logger.error(f"Redis error in get_or_create: {e}")
            return ChatSession(phone_number=phone_number)

    def _save(self, session: ChatSession) -> None:
        try:
            key = self._key(session.phone_number)
            self._redis.setex(key, self._ttl, json.dumps(session.to_dict()))
        except Exception as e:
            logger.error(f"Redis error saving session: {e}")

    def save(self, session: ChatSession) -> None:
        self._save(session)

    def remove(self, phone_number: str) -> None:
        try:
            self._redis.delete(self._key(phone_number))
        except Exception as e:
            logger.error(f"Redis error removing session: {e}")

    def cleanup_expired(self) -> int:
        return 0


class InMemorySessionManager:

    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        logger.info("In-memory session manager initialized (sessions will not persist across restarts)")

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

    def save(self, session: ChatSession) -> None:
        with self._lock:
            self._sessions[session.phone_number] = session

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


def _create_session_manager():
    redis_url = Config.REDIS_URL
    if redis_url:
        try:
            return RedisSessionManager(redis_url)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.warning("Falling back to in-memory session manager")
    return InMemorySessionManager()


session_manager = _create_session_manager()
