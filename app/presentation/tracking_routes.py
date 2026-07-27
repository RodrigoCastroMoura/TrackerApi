from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Vehicle, VehicleData
from app.presentation.auth_routes import token_required, require_permission, require_valid_subscription
from app.infrastructure.geocoding_service import (
    get_google_geocoding_service,
    get_geocoding_service
)
from mongoengine.errors import DoesNotExist
import logging
from bson.objectid import ObjectId
from app.infrastructure.redis_cache import vehicle_cache
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_best_geocoding_service():
    """
    Get the best available geocoding service.
    Tries Google Maps first (premium), falls back to Nominatim (free).
    """
    try:
        return get_google_geocoding_service()
    except (ValueError, ImportError) as e:
        logger.warning(f"Google Maps not available ({str(e)}), using Nominatim fallback")
        return get_geocoding_service()

api = Namespace('tracking', description='Vehicle tracking operations')

location_model = api.model('Location', {
    'lat': fields.Float(description='Latitude'),
    'lng': fields.Float(description='Longitude'),
    'address': fields.String(description='Endereço'),
    'speed': fields.Float(description='Velocidade em km/h'),
    'heading': fields.Float(description='Direção em graus'),
    'timestamp': fields.DateTime(description='Timestamp da localização')
})

vehicle_tracking_location_model = api.model('VehicleTrackingLocation', {
    'lat': fields.Float(description='Latitude'),
    'lng': fields.Float(description='Longitude')
})

vehicle_tracking_model = api.model('VehicleTracking', {
    'id': fields.String(readonly=True),
    'plate': fields.String(description='Placa do veículo'),
    'model': fields.String(description='Modelo do veículo'),
    'type': fields.String(description='Tipo do veículo'),
    'block': fields.String(description='Status do veículo'),
    'location': fields.Nested(vehicle_tracking_location_model, allow_null=True),
    'tsusermanu': fields.DateTime(description='Timestamp da última atualização de localização')
})

vehicle_location_response_model = api.model('VehicleLocationResponse', {
    'vehicle_id': fields.String(description='ID do veículo'),
    'plate': fields.String(description='Placa do veículo'),
    'location': fields.Nested(location_model, allow_null=True),
    'type': fields.String(description='Tipo do veículo'),
    'block': fields.Boolean(description='Status de bloqueio do veículo'),
    'ignition': fields.Boolean(description='Status da ignição')



})

location_point_model = api.model('LocationPoint', {
    'lat': fields.Float(description='Latitude'),
    'lng': fields.Float(description='Longitude'),
    'speed': fields.Float(description='Velocidade'),
    'heading': fields.Float(description='Direção'),
    'timestamp': fields.DateTime(description='Timestamp')
})

vehicle_history_model = api.model('VehicleHistory', {
    'vehicle_id': fields.String(description='ID do veículo'),
    'plate': fields.String(description='Placa do veículo'),
    'period': fields.Raw(description='Período consultado'),
    'locations': fields.List(fields.Nested(location_point_model)),
    'total_distance': fields.Float(description='Distância total em km'),
    'total_time_moving': fields.Integer(description='Tempo em movimento (segundos)'),
    'max_speed': fields.Float(description='Velocidade máxima'),
    'avg_speed': fields.Float(description='Velocidade média')
})

stop_model = api.model('Stop', {
    'lat': fields.Float(),
    'lng': fields.Float(),
    'address': fields.String(),
    'arrival': fields.DateTime(),
    'departure': fields.DateTime(),
    'duration': fields.Integer(description='Duração da parada em segundos')
})

route_model = api.model('Route', {
    'points': fields.List(fields.List(fields.Float)),
    'polyline': fields.String(description='Polyline codificada'),
    'total_distance': fields.Float(description='Distância total'),
    'duration': fields.Integer(description='Duração total'),
    'stops': fields.List(fields.Nested(stop_model))
})

vehicle_route_model = api.model('VehicleRoute', {
    'vehicle_id': fields.String(),
    'route': fields.Nested(route_model)
})

tracking_pagination_model = api.model('TrackingPagination', {
    'vehicles': fields.List(fields.Nested(vehicle_tracking_model)),
    'total': fields.Integer(),
    'page': fields.Integer(),
    'per_page': fields.Integer()
})


@api.route('/vehicles')
class VehicleTrackingList(Resource):
    
    @api.doc('list_vehicle_tracking',
             params={
                 'status': {'type': 'string', 'enum': ['active', 'blocked', 'idle'], 'description': 'Filtrar por status'},
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 20}
             })
    @api.marshal_with(tracking_pagination_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user):
        """Lista todos os veículos com última localização conhecida para visualização no mapa"""
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 20))))
            
            # Build query - filter by company
            query = {'visible': True, 'company_id': current_user.company_id}
            
            # Se o usuário autenticado é um cliente, filtrar automaticamente por customer_id
            if hasattr(current_user, 'role') and current_user.role == 'customer':
                query['customer_id'] = current_user.id
            
            # Additional filters
            status_filter = request.args.get('status')
            if status_filter == 'blocked':
                query['bloqueado'] = True
            elif status_filter == 'active':
                query['status'] = 'active'
                query['bloqueado'] = False
            
            # Execute query
            total = Vehicle.objects(**query).count()
            vehicles = Vehicle.objects(**query).order_by('-created_at').skip(
                (page - 1) * per_page).limit(per_page)
            
            # Get last location for each vehicle
            result_vehicles = []
            for vehicle in vehicles:

                location = None
                if vehicle.latitude and vehicle.longitude and vehicle.tsusermanu \
                        and vehicle.tsusermanu.date() == datetime.now().date():
                    location = {
                        'lat': float(vehicle.latitude),
                        'lng': float(vehicle.longitude)
                    }

                vehicle_data = {
                    'id': str(vehicle.IMEI),
                    'plate': vehicle.dsplaca or 'N/A',
                    'model': vehicle.dsmodelo or 'N/A',
                    'type': vehicle.tipo,
                    'block': 'bloqueado' if vehicle.bloqueado else "desbloqueado",
                    'location': location,
                    'tsusermanu': vehicle.tsusermanu
                }
                
                result_vehicles.append(vehicle_data)
            
            return {
                'vehicles': result_vehicles,
                'total': total,
                'page': page,
                'per_page': per_page
            }, 200
            
        except Exception as e:
            logger.error(f"Error listing vehicle tracking: {str(e)}")
            return {'message': 'Erro ao listar rastreamento de veículos'}, 500

@api.route('/vehicles/<imei>/location')
@api.param('id', 'Vehicle identifier')
class VehicleCurrentLocation(Resource):
    
    @api.doc('get_vehicle_current_location')
    @api.marshal_with(vehicle_location_response_model)
    @token_required
    @require_permission('customer', 'read')
    @require_valid_subscription
    def get(self, current_user, imei):
        """Retorna a localização atual de um veículo específico"""
        try:
            if not imei:
                return {'message': 'IMEI do veículo não fornecido'}, 400
            
            vehicle_obj = None
            vehicle_dict = vehicle_cache.get_vehicle(imei)  # tenta cache

            if not vehicle_dict:
                # Busca no banco
                vehicle_obj = Vehicle.objects.get(IMEI=imei, visible=True, company_id=current_user.company_id)

                if not vehicle_obj:
                    return {'message': 'Veículo não encontrado'}, 404
                
                # Tenta salvar no cache (se Redis estiver up)
                vehicle_cache.set_vehicle(imei, vehicle_obj)

            if vehicle_dict:
                vehicle = vehicle_dict
            elif vehicle_obj:
                vehicle = vehicle_obj.to_mongo().to_dict()

            tsusermanu = vehicle.get('tsusermanu')

            location = None
            if vehicle.get('latitude') and vehicle.get('longitude'):
                lat = float(vehicle.get('latitude'))
                lng = float(vehicle.get('longitude'))

                # Get best geocoding service (Google Maps with Nominatim fallback)
                geocoding = get_best_geocoding_service()
                address = geocoding.get_address_or_fallback(lat, lng)

                location = {
                    'lat': lat,
                    'lng': lng,
                    'address': address,
                    'speed': 0.0,
                    'heading': 0.0,
                    'altitude': float(vehicle.get('altitude')) if vehicle.get('altitude') else 0.0,
                    'accuracy': 10.0,
                    'timestamp': tsusermanu
                }

            response = {
                        'vehicle_id': str(vehicle.get('_id', 'N/A')),
                        'plate': vehicle.get('dsplaca') or 'N/A',
                        'type': vehicle.get('tipo'),
                        'block': vehicle.get('bloqueado'),
                        'ignition': vehicle.get('ignicao'),
                        'model': vehicle.get('dsmodelo') or 'N/A',
                        'location': location
                    }

            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle location: {str(e)}")
            return {'message': 'Erro ao buscar localização do veículo'}, 500

@api.route('/vehicles/history/<imei>')
@api.param('imei', 'Vehicle IMEI')
class VehicleHistory(Resource):
    @api.doc('get_vehicle_location',
             params={
                 'date': {'type': 'string', 'description': 'Data a ser consultada (ISO format, ex: 2026-07-25)'}
             })
    @token_required
    @require_permission('customer', 'read')
    @require_valid_subscription
    def get(self, current_user, imei):
        """Obter histórico de localização do veículo"""
        try:

            # Build query for vehicle data
            query = {'imei': imei}

            # Date filter - busca todos os registros do dia informado
            if request.args.get('date'):
                start = datetime.fromisoformat(request.args.get('date'))
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                query['timestamp__gte'] = start
                query['timestamp__lt'] = end

            # Get location data
            locations = VehicleData.objects(**query).order_by('-timestamp')

            result_locations = [{
                'longitude': loc.location.longitude,
                'latitude': loc.location.latitude,
                'altitude': loc.location.altitude,
                'speed': loc.location.speed,
                'course': loc.location.course
            } if loc.location else None for loc in locations]

            return {
                'locations': result_locations,
                'total': len(result_locations)
            }, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle location: {str(e)}")
            return {'message': 'Erro ao buscar localização'}, 500


