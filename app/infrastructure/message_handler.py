import logging
from typing import Optional
from config import Config
from app.infrastructure.session_manager import ChatSession, ChatVehicle
from app.infrastructure.whatsapp_client import WhatsAppClient
from app.infrastructure.business_service import BusinessService

logger = logging.getLogger(__name__)


class MessageHandler:

    def __init__(self, whatsapp: WhatsAppClient, business: BusinessService):
        self.whatsapp = whatsapp
        self.business = business
        self.handlers = {
            "UNAUTHENTICATED": self._handle_unauthenticated,
            "WAITING_PASSWORD": self._handle_waiting_password,
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
        logger.info(f"[UNAUTH] {session.phone_number}: '{message}'")

        phone_number = self._remover_caracteres_esquerda(session.phone_number)

        user = self.business.authenticate_by_phone(
            phone_number,
            Config.PASSWORD_CHATBOT_SALT
        )

        if user:
            session.user = user
            session.user.intrudution_shown = False
            session.state = "AUTHENTICATED"
            logger.info(f"[AUTH] Usuario autenticado por telefone: {user.name}, {len(user.vehicles)} veiculos")
            self._show_vehicles(session)
        else:
            msg_clean = message.strip()
            if len(msg_clean) >= 11 and msg_clean.replace(".", "").replace("-", "").replace("/", "").isdigit():
                session.state = "WAITING_PASSWORD"
                session.pending_identifier = msg_clean
                logger.info(f"[UNAUTH] CPF recebido, aguardando senha: {session.pending_identifier}")
                self.whatsapp.send_message(
                    session.phone_number,
                    "Agora, por favor, digite sua *senha*:"
                )
            else:
                self.whatsapp.send_message(
                    session.phone_number,
                    "Bem-vindo ao Sistema de Rastreamento! \n\n"
                    "Para acessar, por favor, digite seu *CPF*:"
                )

    def _handle_waiting_password(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        logger.info(f"[WAITING_PWD] {session.phone_number}: senha recebida")

        password = message.strip()
        identifier = session.pending_identifier or ""

        user = self.business.authenticate_by_credentials(identifier, password)

        if user and len(user.vehicles) > 0:
            session.user = user
            session.user.intrudution_shown = False
            session.state = "AUTHENTICATED"
            session.pending_identifier = None
            logger.info(f"[AUTH] Usuario autenticado por credenciais: {user.name}, {len(user.vehicles)} veiculos")
            self._show_vehicles(session)
        else:
            session.state = "UNAUTHENTICATED"
            session.pending_identifier = None
            logger.warning(f"[WAITING_PWD] Credenciais invalidas para: {identifier}")
            self.whatsapp.send_message(
                session.phone_number,
                "CPF ou senha incorretos, ou nenhum veiculo encontrado.\n\n"
                "Por favor, digite seu *CPF* para tentar novamente:"
            )

    def _handle_authenticated(self, session: ChatSession, message: str, message_type: str = "text") -> None:
        msg_lower = message.lower().strip()

        logger.info(f"[AUTH] {session.phone_number} | Tipo: {message_type} | Msg: '{message}'")

        if msg_lower in ["sair", "exit", "quit"]:
            self._reset_session(session)
            return

        action_commands = ["localizacao", "loc", "l", "bloquear", "block", "b",
                           "desbloquear", "unblock", "d", "voltar", "back", "menu"]
        if msg_lower in action_commands and session.selected_vehicle:
            logger.info(f"[AUTH] Comando de acao com veiculo ja selecionado, redirecionando para action handler")
            session.state = "VEHICLE_SELECTED"
            self._handle_vehicle_action(session, message, message_type)
            return

        if msg_lower in action_commands and len(session.user.vehicles) == 1:
            vehicle = session.user.vehicles[0]
            logger.info(f"[AUTH] Comando de acao com 1 veiculo, auto-selecionando: {vehicle.plate}")
            session.state = "VEHICLE_SELECTED"
            session.selected_vehicle = vehicle
            self._handle_vehicle_action(session, message, message_type)
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
            session.state = "AUTHENTICATED"
            self._show_vehicles(session)
            return

        logger.info(f"[ACTION] {session.phone_number} | Veiculo: {vehicle.plate} | Acao: '{msg_lower}'")

        if message_type == "interactive":
            new_vehicle = self._get_vehicle_by_id(session, message)
            if new_vehicle and new_vehicle.id != vehicle.id:
                logger.info(f"[ACTION] Trocando veiculo para: {new_vehicle.plate}")
                session.selected_vehicle = new_vehicle
                self._show_vehicle_options(session)
                return

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
        session.pending_identifier = None

        self.whatsapp.send_message(
            session.phone_number,
            "Ate logo!"
        )

    def _remover_caracteres_esquerda(self, numero_str, quantidade=2):
        return numero_str[quantidade:]
