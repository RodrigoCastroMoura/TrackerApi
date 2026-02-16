import requests
import logging
from typing import Optional, Tuple
from config import Config
from app.infrastructure.whatsapp.models import ChatUser, ChatVehicle, ChatSession

logger = logging.getLogger(__name__)


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
