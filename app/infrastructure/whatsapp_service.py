import requests
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple, List
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


class WhatsAppClient:

    def __init__(self):
        self.api_url = Config.WHATSAPP_API_URL
        self.phone_number_id = Config.WHATSAPP_PHONE_NUMBER_ID
        self.token = Config.WHATSAPP_TOKEN

    @property
    def base_url(self):
        return f"{self.api_url}/{self.phone_number_id}/messages"

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def send_message(self, to: str, text: str) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }
        return self._send(payload)

    def send_interactive_buttons(self, to: str, body_text: str, buttons: list) -> bool:
        formatted_buttons = []
        for btn in buttons[:3]:
            formatted_buttons.append({
                "type": "reply",
                "reply": {
                    "id": btn["id"],
                    "title": btn["title"][:20]
                }
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {
                    "buttons": formatted_buttons
                }
            }
        }
        return self._send(payload)

    def send_list(self, to: str, body_text: str, button_text: str, sections: list) -> bool:
        formatted_sections = []
        for section in sections:
            rows = []
            for row in section.get("rows", [])[:10]:
                rows.append({
                    "id": str(row["id"])[:200],
                    "title": str(row["title"])[:24],
                    "description": str(row.get("description", ""))[:72]
                })
            formatted_sections.append({
                "title": section.get("title", "")[:24],
                "rows": rows
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text[:1024]},
                "action": {
                    "button": button_text[:20],
                    "sections": formatted_sections
                }
            }
        }
        return self._send(payload)

    def _send(self, payload: dict) -> bool:
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"Message sent to {payload.get('to', 'unknown')}")
                return True
            else:
                logger.error(f"WhatsApp API error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {str(e)}")
            return False


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


class BusinessService:

    def __init__(self):
        self.base_url = Config.API_BASE_URL

    def authenticate_user(self, identifier: str, password: str, endpoint: str) -> Optional[ChatUser]:
        try:
            url = f"{self.base_url}/{endpoint}"
            payload = {
                "identifier": identifier,
                "password": password
            }

            logger.info(f"[BIZ] Authenticating {identifier} via {endpoint}")
            response = requests.post(url, json=payload, timeout=15)

            if response.status_code == 200:
                data = response.json()
                user_data = data.get("user", {})

                token = data.get("access_token", "")
                user = ChatUser(
                    id=user_data.get("id", ""),
                    name=user_data.get("name", ""),
                    email=user_data.get("email", ""),
                    token=token
                )

                vehicles = self._get_vehicles(token)
                user.vehicles = vehicles

                logger.info(f"[BIZ] Auth success: {user.name}, {len(vehicles)} vehicles")
                return user
            else:
                logger.warning(f"[BIZ] Auth failed: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"[BIZ] Auth error: {str(e)}")
            return None

    def _get_vehicles(self, token: str) -> list:
        try:
            url = f"{self.base_url}/tracking/vehicles"
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, timeout=15)

            if response.status_code == 200:
                data = response.json()
                vehicles_data = data.get("vehicles", [])
                vehicles = []
                for v in vehicles_data:
                    vehicle = ChatVehicle(
                        id=v.get("id", ""),
                        plate=v.get("plate", "N/A"),
                        model=v.get("model", "N/A"),
                        is_blocked=v.get("block", "") == "bloqueado"
                    )
                    vehicles.append(vehicle)
                return vehicles
            else:
                logger.warning(f"[BIZ] Failed to get vehicles: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"[BIZ] Error getting vehicles: {str(e)}")
            return []

    def get_vehicle_location(self, vehicle: ChatVehicle, session: ChatSession) -> Optional[dict]:
        try:
            url = f"{self.base_url}/tracking/vehicles/{vehicle.id}/location"
            headers = {"Authorization": f"Bearer {session.user.token}"}
            response = requests.get(url, headers=headers, timeout=15)

            if response.status_code == 200:
                data = response.json()
                location = data.get("location", {})
                return {
                    "latitude": location.get("lat", 0),
                    "longitude": location.get("lng", 0),
                    "address": location.get("address", "N/A"),
                    "speed": location.get("speed", 0),
                    "last_update": location.get("timestamp", "N/A")
                }
            else:
                logger.warning(f"[BIZ] Location failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"[BIZ] Location error: {str(e)}")
            return None

    def block_vehicle(self, vehicle: ChatVehicle, session: ChatSession) -> Tuple[bool, str]:
        return self._send_block_command(vehicle, session, "bloquear")

    def unblock_vehicle(self, vehicle: ChatVehicle, session: ChatSession) -> Tuple[bool, str]:
        return self._send_block_command(vehicle, session, "desbloquear")

    def _send_block_command(self, vehicle: ChatVehicle, session: ChatSession, comando: str) -> Tuple[bool, str]:
        try:
            url = f"{self.base_url}/vehicles/{vehicle.id}/block"
            headers = {"Authorization": f"Bearer {session.user.token}"}
            payload = {"comando": comando}

            response = requests.post(url, json=payload, headers=headers, timeout=15)

            if response.status_code == 200:
                if comando == "bloquear":
                    vehicle.is_blocked = True
                    return True, f"Comando de bloqueio enviado para {vehicle.plate}."
                else:
                    vehicle.is_blocked = False
                    return True, f"Comando de desbloqueio enviado para {vehicle.plate}."
            else:
                logger.warning(f"[BIZ] Block command failed: {response.status_code}")
                return False, f"Nao foi possivel enviar o comando para {vehicle.plate}."
        except Exception as e:
            logger.error(f"[BIZ] Block command error: {str(e)}")
            return False, f"Erro ao enviar comando para {vehicle.plate}."


class MessageHandler:

    def __init__(self, whatsapp: WhatsAppClient, business: BusinessService):
        self.whatsapp = whatsapp
        self.business = business
        self.handlers = {
            "UNAUTHENTICATED": self._handle_unauthenticated,
            "AUTHENTICATED": self._handle_authenticated,
            "VEHICLE_SELECTED": self._handle_vehicle_action
        }

    def handle(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        handler = self.handlers.get(session.state)

        if handler:
            handler(session, message, message_type)
        else:
            logger.error(f"Estado desconhecido: {session.state}")
            self._reset_session(session)

    def _handle_unauthenticated(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        msg_lower = message.lower().strip()

        logger.info(f"[UNAUTH] {session.phone_number}: '{message}'")

        phone_number = self.remover_caracteres_esquerda(session.phone_number)

        user = self.business.authenticate_user(
            phone_number,
            Config.PASSWORD_CHATBOT_SALT,
            "auth/customer/chatbot/login"
        )

        if user:
            session.user = user
            session.user.intrudution_shown = False
            session.state = "AUTHENTICATED"
            logger.info(f"[AUTH] Usuario autenticado: {user.name}, {len(user.vehicles)} veiculos")
            self._show_vehicles(session)
        else:
            if "," in message:
                parts = [p.strip() for p in message.split(",")]
                if len(parts) >= 2:
                    identifier = parts[0]
                    password = parts[1]

                    user = self.business.authenticate_user(identifier, password, "auth/login")

                    if user and len(user.vehicles) > 0:
                        session.user = user
                        session.user.intrudution_shown = False
                        session.state = "AUTHENTICATED"
                        logger.info(f"[AUTH] Usuario autenticado: {user.name}, {len(user.vehicles)} veiculos")
                        self._show_vehicles(session)
                    else:
                        self.whatsapp.send_message(
                            session.phone_number,
                            "Credenciais invalidas ou nenhum veiculo encontrado.\n\n"
                            "Envie: CPF,SENHA"
                        )
            else:
                self.whatsapp.send_message(
                    session.phone_number,
                    "Bem-vindo ao Sistema de Rastreamento!\n\n"
                    "Para acessar, envie:\nCPF,SENHA"
                )

    def _handle_authenticated(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        msg_lower = message.lower().strip()

        logger.info(f"[AUTH] {session.phone_number} | Tipo: {message_type} | Msg: '{message}'")

        if msg_lower in ["sair", "exit", "quit"]:
            self._reset_session(session)
            return

        vehicle = None

        if message_type == "interactive":
            logger.info(f"[AUTH] Buscando veiculo por ID: '{message}'")
            vehicle = self._get_vehicle_by_id(session, message)
            if vehicle:
                logger.info(f"[AUTH] Encontrado por ID: {vehicle.plate}")

        if not vehicle:
            logger.info(f"[AUTH] Buscando veiculo por placa/modelo: '{msg_lower}'")
            vehicle = self._get_vehicle_by_plate(session, msg_lower)
            if vehicle:
                logger.info(f"[AUTH] Encontrado por placa/modelo: {vehicle.plate}")

        if vehicle:
            logger.info(f"[AUTH] SELECIONANDO VEICULO: {vehicle.plate} (ID: {vehicle.id})")
            session.state = "VEHICLE_SELECTED"
            session.selected_vehicle = vehicle
            self._show_vehicle_options(session)
        else:
            logger.warning(f"[AUTH] Veiculo nao encontrado para: '{message}'")
            self.whatsapp.send_message(
                session.phone_number,
                "Veiculo nao encontrado."
            )
            self._show_vehicles(session)

    def _show_vehicles(self, session: ChatSession) -> None:
        session.selected_vehicle = None

        if not session.user or not session.user.vehicles:
            self.whatsapp.send_message(
                session.phone_number,
                "Nenhum veiculo cadastrado."
            )
            return

        greeting = ""
        if not session.user.intrudution_shown:
            greeting = f"Ola, {session.user.name}!\n"
            session.user.intrudution_shown = True

        if len(session.user.vehicles) == 1:
            vehicle = session.user.vehicles[0]
            session.state = "VEHICLE_SELECTED"
            session.selected_vehicle = vehicle

            self.whatsapp.send_interactive_buttons(
                session.phone_number,
                f"{greeting}Voce esta no sistema de Rastreamento!\n\n"
                f"Veiculo: {vehicle.plate}\n"
                f"Modelo: {vehicle.model}\n"
                f"Status: {'Bloqueado' if vehicle.is_blocked else 'Desbloqueado'}",
                [
                    {"id": "localizacao", "title": "Localizacao"},
                    {"id": "bloquear" if not vehicle.is_blocked else "desbloquear",
                     "title": "Bloquear" if not vehicle.is_blocked else "Desbloquear"},
                    {"id": "sair", "title": "Sair"}
                ]
            )
        else:
            sections = [{
                "title": "Seus Veiculos",
                "rows": [
                    {
                        "id": v.id,
                        "title": v.plate,
                        "description": v.model
                    } for v in session.user.vehicles
                ]
            }]

            self.whatsapp.send_list(
                session.phone_number,
                f"{greeting}Voce esta no sistema de Rastreamento!\n\n"
                f"Selecione um veiculo para ver opcoes:",
                "Ver Veiculos",
                sections
            )

    def _show_vehicle_options(self, session: ChatSession) -> None:
        vehicle = session.selected_vehicle

        if not vehicle:
            logger.error("[OPTIONS] selected_vehicle e None!")
            self._show_vehicles(session)
            return

        logger.info(f"[OPTIONS] Mostrando opcoes para: {vehicle.plate}")

        buttons = [
            {"id": "localizacao", "title": "Localizacao"},
            {"id": "bloquear" if not vehicle.is_blocked else "desbloquear",
             "title": "Bloquear" if not vehicle.is_blocked else "Desbloquear"}
        ]

        if len(session.user.vehicles) > 1:
            buttons.append({"id": "menu", "title": "Menu"})

        buttons.append({"id": "sair", "title": "Sair"})

        self.whatsapp.send_interactive_buttons(
            session.phone_number,
            f"Voce esta no sistema de Rastreamento!\n\n"
            f"Veiculo: {vehicle.plate}\n"
            f"Modelo: {vehicle.model}\n"
            f"Status: {'Bloqueado' if vehicle.is_blocked else 'Desbloqueado'}\n\n"
            f"Escolha uma opcao:",
            buttons
        )

    def _handle_vehicle_action(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        msg_lower = message.lower().strip()
        vehicle = session.selected_vehicle

        if not vehicle:
            logger.error(f"[ACTION] selected_vehicle e None! Estado inconsistente.")
            self._show_vehicles(session)
            return

        logger.info(f"[ACTION] {session.phone_number} | Veiculo: {vehicle.plate} | Acao: '{msg_lower}'")

        buttons = [
            {"id": "voltar", "title": "Voltar"}
        ]

        if len(session.user.vehicles) > 1:
            buttons.append({"id": "menu", "title": "Menu"})

        buttons.append({"id": "sair", "title": "Sair"})

        if msg_lower in ["localizacao", "loc", "l"]:
            logger.info(f"[ACTION] Buscando localizacao para {vehicle.plate}")
            location = self.business.get_vehicle_location(vehicle, session)

            if location:
                self.whatsapp.send_interactive_buttons(
                    session.phone_number,
                    f"Localizacao do veiculo modelo {vehicle.model} de placa {vehicle.plate}:\n\n"
                    f"Endereco: {location['address']}\n"
                    f"Velocidade: {location['speed']} km/h\n"
                    f"Ultima atualizacao: {location['last_update']}\n\n"
                    f"Maps: https://maps.google.com/?q={location['latitude']},{location['longitude']}",
                    buttons
                )
            else:
                self.whatsapp.send_interactive_buttons(
                    session.phone_number,
                    f"Nao foi possivel obter a localizacao do veiculo {vehicle.plate}.",
                    buttons
                )

        elif msg_lower in ["bloquear", "block", "b"]:
            logger.info(f"[ACTION] Bloqueando {vehicle.plate}")
            success, message_text = self.business.block_vehicle(vehicle, session)
            self.whatsapp.send_interactive_buttons(
                session.phone_number,
                message_text,
                buttons
            )

        elif msg_lower in ["desbloquear", "unblock", "d"]:
            logger.info(f"[ACTION] Desbloqueando {vehicle.plate}")
            success, message_text = self.business.unblock_vehicle(vehicle, session)
            self.whatsapp.send_interactive_buttons(
                session.phone_number,
                message_text,
                buttons
            )

        elif msg_lower in ["voltar", "back"]:
            logger.info(f"[ACTION] Voltar para opcoes de {vehicle.plate}")
            self._show_vehicle_options(session)

        elif msg_lower in ["menu"]:
            logger.info(f"[ACTION] Voltando para menu principal")
            session.state = "AUTHENTICATED"
            session.selected_vehicle = None
            self._show_vehicles(session)

        elif msg_lower in ["sair", "exit", "quit"]:
            logger.info(f"[ACTION] Saindo do sistema")
            self._reset_session(session)

        else:
            logger.warning(f"[ACTION] Comando nao reconhecido: '{msg_lower}'")
            self._show_vehicle_options(session)

    def _get_vehicle_by_plate(self, session: ChatSession, plate: str) -> Optional[ChatVehicle]:
        for vehicle in session.user.vehicles:
            if vehicle.plate.lower().strip() == plate:
                return vehicle
            if vehicle.model.lower().strip() == plate:
                return vehicle
        return None

    def _get_vehicle_by_id(self, session: ChatSession, vehicle_id: str) -> Optional[ChatVehicle]:
        logger.debug(f"[ID_SEARCH] Buscando ID: '{vehicle_id}'")

        for vehicle in session.user.vehicles:
            if str(vehicle.id).strip() == str(vehicle_id).strip():
                logger.debug(f"[ID_SEARCH] MATCH: {vehicle.plate} (ID: {vehicle.id})")
                return vehicle
            else:
                logger.debug(f"[ID_SEARCH] No match: {vehicle.plate} (ID: {vehicle.id})")

        logger.warning(f"[ID_SEARCH] Nenhum veiculo encontrado com ID: '{vehicle_id}'")
        return None

    def _reset_session(self, session: ChatSession) -> None:
        logger.info(f"[RESET] Resetando sessao de {session.phone_number}")

        session.user = None
        session.state = "UNAUTHENTICATED"
        session.selected_vehicle = None

        self.whatsapp.send_message(
            session.phone_number,
            "Ate logo!"
        )

    def remover_caracteres_esquerda(self, numero_str, quantidade=2):
        return numero_str[quantidade:]


whatsapp_client = WhatsAppClient()
session_manager = SessionManager()
business_service = BusinessService()
message_handler = MessageHandler(whatsapp_client, business_service)
