import logging
from typing import Optional, Tuple
from datetime import timezone, timedelta
from config import Config
from app.domain.models import Customer, Vehicle
from app.infrastructure.session_manager import ChatUser, ChatVehicle, ChatSession
from app.infrastructure.geocoding_service import (
    get_google_geocoding_service,
    get_geocoding_service
)

logger = logging.getLogger(__name__)


def _get_best_geocoding_service():
    try:
        return get_google_geocoding_service()
    except (ValueError, ImportError) as e:
        logger.warning(f"Google Maps not available ({str(e)}), using Nominatim fallback")
        return get_geocoding_service()


class BusinessService:

    def authenticate_by_phone(self, phone: str, salt: str) -> Optional[ChatUser]:
        try:
            logger.info(f"[BIZ] Authenticating by phone: {phone}")

            customer = Customer.objects(phone=phone).first()

            if not customer:
                logger.warning(f"[BIZ] Customer not found for phone: {phone}")
                return None

            

            if customer.status != 'active':
                logger.warning(f"[BIZ] Inactive customer: {customer.document}")
                return None

            vehicles = self._get_customer_vehicles(customer)

            user = ChatUser(
                id=str(customer.id),
                name=customer.name,
                email=customer.email,
                token="",
                company_id=str(customer.company_id.id) if customer.company_id else "",
                vehicles=vehicles
            )

            logger.info(f"[BIZ] Auth success: {user.name}, {len(vehicles)} vehicles")
            return user

        except Exception as e:
            logger.error(f"[BIZ] Auth by phone error: {str(e)}")
            return None

    def authenticate_by_credentials(self, identifier: str, password: str) -> Optional[ChatUser]:
        try:
            logger.info(f"[BIZ] Authenticating by credentials: {identifier}")

            customer = Customer.objects(email=identifier).first()
            if not customer:
                customer = Customer.objects(document=identifier).first()
            if not customer:
                customer = Customer.objects(phone=identifier).first()

            if not customer:
                logger.warning(f"[BIZ] Customer not found: {identifier}")
                return None

            if not customer.check_password(password):
                logger.warning(f"[BIZ] Invalid password for: {identifier}")
                return None

            if customer.status != 'active':
                logger.warning(f"[BIZ] Inactive customer: {customer.document}")
                return None

            vehicles = self._get_customer_vehicles(customer)

            user = ChatUser(
                id=str(customer.id),
                name=customer.name,
                email=customer.email,
                token="",
                company_id=str(customer.company_id.id) if customer.company_id else "",
                vehicles=vehicles
            )

            logger.info(f"[BIZ] Auth success: {user.name}, {len(vehicles)} vehicles")
            return user

        except Exception as e:
            logger.error(f"[BIZ] Auth by credentials error: {str(e)}")
            return None

    def _get_customer_vehicles(self, customer: Customer) -> list:
        try:
            vehicles_db = Vehicle.objects(
                customer_id=customer.id,
                visible=True,
                company_id=customer.company_id
            )

            vehicles = []
            for v in vehicles_db:
                vehicle = ChatVehicle(
                    id=str(v.id),
                    plate=v.dsplaca or "N/A",
                    model=v.dsmodelo or "N/A",
                    is_blocked=v.bloqueado or False
                )
                vehicles.append(vehicle)

            return vehicles

        except Exception as e:
            logger.error(f"[BIZ] Error getting vehicles: {str(e)}")
            return []

    def get_vehicle_location(self, chat_vehicle: ChatVehicle, session: ChatSession) -> Optional[dict]:
        try:
            vehicle = Vehicle.objects.get(
                id=chat_vehicle.id,
                visible=True,
                customer_id=session.user.id,
                company_id=session.user.company_id
            )

            lat = float(vehicle.latitude) if vehicle.latitude else 0.0
            lng = float(vehicle.longitude) if vehicle.longitude else 0.0

            address = "Endereco nao disponivel"
            if lat != 0.0 and lng != 0.0:
                try:
                    geocoding = _get_best_geocoding_service()
                    address = geocoding.get_address_or_fallback(lat, lng)
                except Exception as e:
                    logger.warning(f"[BIZ] Geocoding failed: {str(e)}")

            last_update = "Nao disponivel"
            if vehicle.tsusermanu:
                try:
                    br_tz = timezone(timedelta())
                    dt_br = vehicle.tsusermanu.replace(tzinfo=timezone.utc).astimezone(br_tz)
                    last_update = dt_br.strftime("%d/%m/%Y as %H:%M")
                except Exception:
                    last_update = vehicle.tsusermanu.strftime("%d/%m/%Y as %H:%M")

            return {
                "latitude": lat,
                "longitude": lng,
                "address": address,
                "speed": 0,
                "last_update": last_update
            }

        except Exception as e:
            logger.error(f"[BIZ] Location error: {str(e)}")
            return None

    def block_vehicle(self, chat_vehicle: ChatVehicle, session: ChatSession) -> Tuple[bool, str]:
        return self._send_block_command(chat_vehicle, session, "bloquear")

    def unblock_vehicle(self, chat_vehicle: ChatVehicle, session: ChatSession) -> Tuple[bool, str]:
        return self._send_block_command(chat_vehicle, session, "desbloquear")

    def _send_block_command(self, chat_vehicle: ChatVehicle, session: ChatSession, comando: str) -> Tuple[bool, str]:
        try:
            vehicle = Vehicle.objects.get(
                id=chat_vehicle.id,
                visible=True,
                customer_id=session.user.id,
                company_id=session.user.company_id
            )

            if comando == "bloquear":
                vehicle.comandobloqueo = False
                vehicle.numberSendMessageWhatsApp = session.phone_number
                vehicle.save()
                chat_vehicle.is_blocked = True
                return True, f"Comando de bloqueio enviado para {chat_vehicle.plate}."
            else:
                vehicle.comandobloqueo = True
                vehicle.numberSendMessageWhatsApp = session.phone_number
                vehicle.save()
                chat_vehicle.is_blocked = False
                return True, f"Comando de desbloqueio enviado para {chat_vehicle.plate}."

        except Exception as e:
            logger.error(f"[BIZ] Block command error: {str(e)}")
            return False, f"Erro ao enviar comando para {chat_vehicle.plate}."



business_service = BusinessService()
