import logging
from typing import Optional
from app.chatbot.models import Session, Vehicle
from app.chatbot.business_service import business_service
from app.chatbot.whatsapp_client import whatsapp_client
from app.chatbot.config import ChatbotConfig

logger = logging.getLogger(__name__)


class MessageHandler:

    def __init__(self):
        self.handlers = {
            "UNAUTHENTICATED": self._handle_unauthenticated,
            "AUTHENTICATED": self._handle_authenticated,
            "VEHICLE_SELECTED": self._handle_vehicle_action
        }

    def handle(self, session: Session, message: str, message_type: str = "text") -> None:
        handler = self.handlers.get(session.state)

        if handler:
            handler(session, message, message_type)
        else:
            logger.error(f"Estado desconhecido: {session.state}")
            self._reset_session(session)

    def _handle_unauthenticated(self, session: Session, message: str, message_type: str = "text") -> None:
        msg_lower = message.lower().strip()

        logger.info(f"[UNAUTH] {session.phone_number}: '{message}'")

        phone_number = self.remover_caracteres_esquerda(session.phone_number)

        user = business_service.authenticate_user(
            phone_number,
            ChatbotConfig.PASSWORD_CHATBOT_SALT,
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

                    user = business_service.authenticate_user(identifier, password, "auth/login")

                    if user and len(user.vehicles) > 0:
                        session.user = user
                        session.user.intrudution_shown = False
                        session.state = "AUTHENTICATED"
                        logger.info(f"[AUTH] Usuario autenticado: {user.name}, {len(user.vehicles)} veiculos")
                        self._show_vehicles(session)
                    else:
                        whatsapp_client.send_message(
                            session.phone_number,
                            "Credenciais invalidas ou nenhum veiculo encontrado.\n\n"
                            "Envie: CPF,SENHA"
                        )
            else:
                whatsapp_client.send_message(
                    session.phone_number,
                    "Bem-vindo ao Sistema de Rastreamento!\n\n"
                    "Para acessar, envie:\nCPF,SENHA"
                )

    def _handle_authenticated(self, session: Session, message: str, message_type: str = "text") -> None:
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
            whatsapp_client.send_message(
                session.phone_number,
                "Veiculo nao encontrado."
            )
            self._show_vehicles(session)

    def _show_vehicles(self, session: Session) -> None:
        session.selected_vehicle = None

        if not session.user or not session.user.vehicles:
            whatsapp_client.send_message(
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

            whatsapp_client.send_interactive_buttons(
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

            whatsapp_client.send_list(
                session.phone_number,
                f"{greeting}Voce esta no sistema de Rastreamento!\n\n"
                f"Selecione um veiculo para ver opcoes:",
                "Ver Veiculos",
                sections
            )

    def _show_vehicle_options(self, session: Session) -> None:
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

        whatsapp_client.send_interactive_buttons(
            session.phone_number,
            f"Voce esta no sistema de Rastreamento!\n\n"
            f"Veiculo: {vehicle.plate}\n"
            f"Modelo: {vehicle.model}\n"
            f"Status: {'Bloqueado' if vehicle.is_blocked else 'Desbloqueado'}\n\n"
            f"Escolha uma opcao:",
            buttons
        )

    def _handle_vehicle_action(self, session: Session, message: str, message_type: str = "text") -> None:
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
            location = business_service.get_vehicle_location(vehicle, session)

            if location:
                whatsapp_client.send_interactive_buttons(
                    session.phone_number,
                    f"Localizacao do veiculo modelo {vehicle.model} de placa {vehicle.plate}:\n\n"
                    f"Endereco: {location['address']}\n"
                    f"Velocidade: {location['speed']} km/h\n"
                    f"Ultima atualizacao: {location['last_update']}\n\n"
                    f"Maps: https://maps.google.com/?q={location['latitude']},{location['longitude']}",
                    buttons
                )
            else:
                whatsapp_client.send_interactive_buttons(
                    session.phone_number,
                    f"Nao foi possivel obter a localizacao do veiculo {vehicle.plate}.",
                    buttons
                )

        elif msg_lower in ["bloquear", "block", "b"]:
            logger.info(f"[ACTION] Bloqueando {vehicle.plate}")
            success, message_text = business_service.block_vehicle(vehicle, session)
            whatsapp_client.send_interactive_buttons(
                session.phone_number,
                message_text,
                buttons
            )

        elif msg_lower in ["desbloquear", "unblock", "d"]:
            logger.info(f"[ACTION] Desbloqueando {vehicle.plate}")
            success, message_text = business_service.unblock_vehicle(vehicle, session)
            whatsapp_client.send_interactive_buttons(
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

    def _get_vehicle_by_plate(self, session: Session, plate: str) -> Optional[Vehicle]:
        for vehicle in session.user.vehicles:
            if vehicle.plate.lower().strip() == plate:
                return vehicle
            if vehicle.model.lower().strip() == plate:
                return vehicle
        return None

    def _get_vehicle_by_id(self, session: Session, vehicle_id: str) -> Optional[Vehicle]:
        logger.debug(f"[ID_SEARCH] Buscando ID: '{vehicle_id}'")

        for vehicle in session.user.vehicles:
            if str(vehicle.id).strip() == str(vehicle_id).strip():
                logger.debug(f"[ID_SEARCH] MATCH: {vehicle.plate} (ID: {vehicle.id})")
                return vehicle
            else:
                logger.debug(f"[ID_SEARCH] No match: {vehicle.plate} (ID: {vehicle.id})")

        logger.warning(f"[ID_SEARCH] Nenhum veiculo encontrado com ID: '{vehicle_id}'")
        return None

    def _reset_session(self, session: Session) -> None:
        logger.info(f"[RESET] Resetando sessao de {session.phone_number}")

        session.user = None
        session.state = "UNAUTHENTICATED"
        session.selected_vehicle = None

        whatsapp_client.send_message(
            session.phone_number,
            "Ate logo!"
        )

    def remover_caracteres_esquerda(self, numero_str, quantidade=2):
        return numero_str[quantidade:]


message_handler = MessageHandler()
