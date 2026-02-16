from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


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
