import os
import json
import redis
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)

class RedisVehicleCache:
    
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self.enabled = Config.REDIS_ENABLED
        self.ttl = Config.REDIS_VEHICLE_TTL
        self.location_ttl = Config.REDIS_LOCATION_TTL
        self._connect()
    
    def _connect(self):
        if not self.enabled:
            logger.info("Redis cache disabled")
            return
        
        try:
            redis_url = Config.REDIS_URL
            
            self.client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
           
            self.client.ping()
            logger.info(f"Redis connected successfully")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            self.client = None
            self.enabled = False
    
    def _vehicle_key(self, imei: str) -> str:
        return f"vehicle:{imei}"

    def _customer_key(self, customer_id: str) -> str:
        return f"customer:{customer_id}"

    def _vehicle_id_key(self, vehicle_id: str) -> str:
        return f"vehicle:id:{vehicle_id}"

    def _location_key(self, company_id: str, imei: str) -> str:
        return f"location:{company_id}:{imei}"

    # 🆕 Nova estrutura de chave para os contadores
    def _counter_key(self, counter_name: str) -> str:
        return f"counter:{counter_name}"

    def _serialize_vehicle(self, vehicle_data: Any) -> str:
        if hasattr(vehicle_data, 'to_mongo'):
            vehicle_data = vehicle_data.to_mongo().to_dict()
        
        elif not isinstance(vehicle_data, dict):
            vehicle_data = vars(vehicle_data)

        serializable = {}
        for k, v in vehicle_data.items():
            if k == '_id':  
                serializable[k] = str(v)
            elif isinstance(v, datetime):
                serializable[k] = v.isoformat()
            elif v is None:
                serializable[k] = None
            else:
                serializable[k] = str(v) if not isinstance(v, (str, int, float, bool, list, dict)) else v

        return json.dumps(serializable)
    
    def _deserialize_vehicle(self, data: str) -> Dict[str, Any]:
        vehicle = json.loads(data)
        date_fields = ['created_at', 'updated_at', 'ultimoalertabateria', 'tsusermanu']
        for field in date_fields:
            if field in vehicle and vehicle[field] and isinstance(vehicle[field], str):
                try:
                    vehicle[field] = datetime.fromisoformat(vehicle[field])
                except (ValueError, TypeError):
                    pass
        return vehicle
    
    def get_vehicle(self, imei: str) -> Optional[Dict[str, Any]]:
        if not self.enabled or not self.client:
            return None
        
        try:
            data = self.client.get(self._vehicle_key(imei))
            if data:
                logger.debug(f"Redis HIT for vehicle IMEI {imei}")
                return self._deserialize_vehicle(data)
            logger.debug(f"Redis MISS for vehicle IMEI {imei}")
            return None
        except Exception as e:
            logger.error(f"Redis get error for IMEI {imei}: {e}")
            return None
    
    def get_vehicle_by_id(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        if not self.enabled or not self.client:
            return None
        try:
            imei = self.client.get(self._vehicle_id_key(vehicle_id))
            if imei:
                logger.debug(f"Redis HIT vehicle by ID {vehicle_id} -> IMEI {imei}")
                return self.get_vehicle(imei)
            logger.debug(f"Redis MISS vehicle by ID {vehicle_id}")
            return None
        except Exception as e:
            logger.error(f"Redis get_by_id error for {vehicle_id}: {e}")
            return None

    def set_vehicle(self, imei: str, vehicle_data: Any, vehicle_id: str = None):
        if not self.enabled or not self.client:
            return

        try:
            serialized = self._serialize_vehicle(vehicle_data)
            self.client.setex(self._vehicle_key(imei), self.ttl, serialized)
            if vehicle_id:
                self.client.setex(self._vehicle_id_key(vehicle_id), self.ttl, imei)
            logger.debug(f"Redis SET vehicle IMEI {imei} (TTL: {self.ttl}s)")
        except Exception as e:
            logger.error(f"Redis set error for IMEI {imei}: {e}")

    def invalidate_vehicle(self, imei: str):
        if not self.enabled or not self.client:
            return

        try:
            self.client.delete(self._vehicle_key(imei))
            logger.debug(f"Redis INVALIDATE vehicle IMEI {imei}")
        except Exception as e:
            logger.error(f"Redis invalidate error for IMEI {imei}: {e}")

    def invalidate_vehicle_by_id(self, vehicle_id: str):
        if not self.enabled or not self.client:
            return

        try:
            self.client.delete(self._vehicle_id_key(vehicle_id))
            logger.debug(f"Redis INVALIDATE vehicle ID {vehicle_id}")
        except Exception as e:
            logger.error(f"Redis invalidate by ID error for {vehicle_id}: {e}")

    def update_vehicle_fields(self, imei: str, updates: Dict[str, Any]):
        if not self.enabled or not self.client:
            return
        
        try:
            existing = self.get_vehicle(imei)
            if existing:
                existing.update(updates)
                self.set_vehicle(imei, existing)
            else:
                self.invalidate_vehicle(imei)
        except Exception as e:
            logger.error(f"Redis update error for IMEI {imei}: {e}")

    def _serialize_location_response(self, response: Dict[str, Any]) -> str:
        data = dict(response)
        location = data.get('location')
        if location:
            location = dict(location)
            ts = location.get('timestamp')
            if isinstance(ts, datetime):
                location['timestamp'] = ts.isoformat()
            data['location'] = location
        return json.dumps(data)

    def _deserialize_location_response(self, data: str) -> Dict[str, Any]:
        response = json.loads(data)
        location = response.get('location')
        if location and isinstance(location.get('timestamp'), str):
            try:
                location['timestamp'] = datetime.fromisoformat(location['timestamp'])
            except (ValueError, TypeError):
                pass
        return response

    def get_location_response(self, company_id: str, imei: str) -> Optional[Dict[str, Any]]:
        if not self.enabled or not self.client:
            return None

        try:
            data = self.client.get(self._location_key(company_id, imei))
            if data:
                logger.debug(f"Redis HIT for location {company_id}:{imei}")
                return self._deserialize_location_response(data)
            logger.debug(f"Redis MISS for location {company_id}:{imei}")
            return None
        except Exception as e:
            logger.error(f"Redis get location error for {company_id}:{imei}: {e}")
            return None

    def set_location_response(self, company_id: str, imei: str, response: Dict[str, Any]):
        if not self.enabled or not self.client:
            return

        try:
            serialized = self._serialize_location_response(response)
            self.client.setex(self._location_key(company_id, imei), self.location_ttl, serialized)
            logger.debug(f"Redis SET location {company_id}:{imei} (TTL: {self.location_ttl}s)")
        except Exception as e:
            logger.error(f"Redis set location error for {company_id}:{imei}: {e}")

    def set_customer(self, customer_id: str, customer_data: Any):
        if not self.enabled or not self.client:
            return

        try:
            serialized = self._serialize_vehicle(customer_data)
            self.client.setex(self._customer_key(customer_id), self.ttl, serialized)
            logger.debug(f"Redis SET customer {customer_id} (TTL: {self.ttl}s)")
        except Exception as e:
            logger.error(f"Redis set error for customer {customer_id}: {e}")

    def invalidate_customer(self, customer_id: str):
        if not self.enabled or not self.client:
            return

        try:
            self.client.delete(self._customer_key(customer_id))
            logger.debug(f"Redis INVALIDATE customer {customer_id}")
        except Exception as e:
            logger.error(f"Redis invalidate error for customer {customer_id}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        if not self.enabled or not self.client:
            return {'enabled': False}
        
        try:
            info = self.client.info('stats')
            keyspace = self.client.info('keyspace')
            return {
                'enabled': True,
                'connected': True,
                'hits': info.get('keyspace_hits', 0),
                'misses': info.get('keyspace_misses', 0),
                'keys': keyspace.get('db0', {}).get('keys', 0) if keyspace.get('db0') else 0
            }
        except Exception as e:
            return {'enabled': True, 'connected': False, 'error': str(e)}
    
    def is_connected(self) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    # =========================================================================
    # 🆕 MÉTODOS NOVOS ADAPTADOS PARA O CONTADOR MENSAL
    # =========================================================================

    def _get_seconds_until_next_month(self) -> int:
        """Calcula de forma privada os segundos restantes até o dia 1 do próximo mês."""
        agora = datetime.now()
        if agora.month == 12:
            proximo_mes = datetime(agora.year + 1, 1, 1, 0, 0, 0)
        else:
            proximo_mes = datetime(agora.year, agora.month + 1, 1, 0, 0, 0)
        return int((proximo_mes - agora).total_seconds())

    def increment_monthly_counter(self, nome_do_contador : str) -> Optional[int]:
        """Incrementa de forma atômica o contador e define o TTL no primeiro acesso do mês."""
        # Se o Redis estiver desativado ou sem conexão, ignora silenciosamente (padrão da sua classe)
        if not self.enabled or not self.client:
            return None

        try:
            key = self._counter_key(nome_do_contador)
            
            # Executa o incremento atômico no Redis
            current_value = self.client.incr(key)
            
            # Se retornar 1, significa que a chave é nova (novo mês começou ou chave nunca existiu)
            if current_value == 1:
                ttl = self._get_seconds_until_next_month()
                self.client.expire(key, ttl)
                logger.info(f"Contador mensal '{nome_do_contador }' inicializado. TTL definido para {ttl}s.")
                
            logger.debug(f"Redis INCR para o contador '{nome_do_contador }': {current_value}")
            return current_value

        except Exception as e:
            # Captura erros de conexão/infraestrutura e gera o log sem derrubar sua aplicação
            logger.error(f"Erro no Redis ao incrementar o contador '{nome_do_contador }': {e}")
            return None

vehicle_cache = RedisVehicleCache()