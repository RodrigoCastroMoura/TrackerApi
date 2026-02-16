from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class Vehicle:
    id: str
    plate: str
    model: str
    is_blocked: bool = False

    def __repr__(self):
        return f"Vehicle(id={self.id}, plate={self.plate}, model={self.model}, blocked={self.is_blocked})"


@dataclass
class ChatUser:
    id: str
    name: str
    email: str
    token: str
    vehicles: List[Vehicle] = field(default_factory=list)
    intrudution_shown: bool = False

    def __repr__(self):
        return f"ChatUser(id={self.id}, name={self.name}, vehicles={len(self.vehicles)})"


@dataclass
class Session:
    phone_number: str
    state: str = "UNAUTHENTICATED"
    user: Optional[ChatUser] = None
    selected_vehicle: Optional[Vehicle] = None
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        elapsed = (datetime.utcnow() - self.last_activity).total_seconds() / 60
        return elapsed > timeout_minutes

    def refresh(self):
        self.last_activity = datetime.utcnow()
