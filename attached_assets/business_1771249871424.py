import logging
from typing import Optional, Tuple
from models.entities import Session, User, Vehicle
from clients.tracker_api import tracker_api

logger = logging.getLogger(__name__)

class BusinessService:
    
    def __init__(self):
        self.api = tracker_api
    
    def authenticate_user(self, cpf: str, password: str, url: str) -> Optional[User]:
        return self.api.authenticate(cpf, password, url)
    
    def get_vehicle_location(self, vehicle: Vehicle, session: Session) -> Optional[dict]:
        return self.api.get_vehicle_location(vehicle.id, session.user.token)
    
    def block_vehicle(self, vehicle: Vehicle, session: Session) -> Tuple[bool, str]:
        logger.error(f"!!! BLOQUEANDO VEICULO !!!")
        logger.error(f"Placa: {vehicle.plate}")
        logger.error(f"ID: {vehicle.id}")
        logger.error(f"Modelo: {vehicle.model}")
        
        success = self.api.block_vehicle(vehicle.id, session.user.token)
        if success:
            vehicle.is_blocked = True
            return True, f"Comando de bloqueio enviado com sucesso para o veiculo {vehicle.plate}.\n Aguarde em breve avisaremos o bloqueio."
        return False, "Erro ao bloquear veiculo. Tente novamente."

    def unblock_vehicle(self, vehicle: Vehicle, session: Session) -> Tuple[bool, str]:
        logger.error(f"!!! DESBLOQUEANDO VEICULO !!!")
        logger.error(f"Placa: {vehicle.plate}")
        logger.error(f"ID: {vehicle.id}")
        logger.error(f"Modelo: {vehicle.model}")
        
        success = self.api.unblock_vehicle(vehicle.id, session.user.token)
        if success:
            vehicle.is_blocked = False
            return True, f"Comando de desbloqueio enviado com sucesso para o veiculo {vehicle.plate}.\n Aguarde em breve avisaremos o desbloqueio. "
        return False, "Erro ao desbloquear veiculo. Tente novamente."

business_service = BusinessService()