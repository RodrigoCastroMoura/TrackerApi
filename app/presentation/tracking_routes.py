from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Vehicle
from app.presentation.auth_routes import token_required, require_permission
from app.infrastructure.geocoding_service import (
    get_google_geocoding_service,
    get_geocoding_service
)
from mongoengine.errors import DoesNotExist
import logging
from bson.objectid import ObjectId

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

vehicle_tracking_model = api.model('VehicleTracking', {
    'id': fields.String(readonly=True),
    'dsplaca': fields.String(description='Placa do veículo'),
    'dsmodelo': fields.String(description='Modelo do veículo'),
    'tipo': fields.String(description='Tipo do veículo'),
    'status': fields.String(description='Status do veículo')
})

vehicle_location_response_model = api.model('VehicleLocationResponse', {
    'vehicle_id': fields.String(description='ID do veículo'),
    'plate': fields.String(description='Placa do veículo'),
    'location': fields.Nested(location_model),
    'tipo': fields.String(description='Tipo do veículo'),
    'bloqueado': fields.Boolean(description='Status de bloqueio do veículo')

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
                
                vehicle_data = {
                    'id': str(vehicle.id),
                    'dsplaca': vehicle.dsplaca or 'N/A',
                    'dsmodelo': vehicle.dsmodelo or 'N/A',
                    'tipo': vehicle.tipo,
                    'status': 'blocked' if vehicle.bloqueado else vehicle.status
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


@api.route('/vehicles/<id>/location')
@api.param('id', 'Vehicle identifier')
class VehicleCurrentLocation(Resource):
    
    @api.doc('get_vehicle_current_location')
    @api.marshal_with(vehicle_location_response_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user, id):
        """Retorna a localização atual de um veículo específico"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            
            # Get best geocoding service (Google Maps with Nominatim fallback)
            geocoding = get_best_geocoding_service()
            
            lat = float(vehicle.latitude) if vehicle.latitude else 0.0
            lng = float(vehicle.longitude) if vehicle.longitude else 0.0
            
            # Get address from coordinates (Google Maps or Nominatim)
            address = 'N/A'
            if lat != 0.0 and lng != 0.0:
                address = geocoding.get_address_or_fallback(lat, lng)
            
            response = {
                'vehicle_id': str(vehicle.id),
                'plate': vehicle.dsplaca or 'N/A',
                'tipo': vehicle.tipo,
                'bloqueado': vehicle.bloqueado,
                'location': {
                    'lat': lat,
                    'lng': lng,
                    'address': address,
                    'speed': 0.0,
                    'heading': 0.0,
                    'altitude': float(vehicle.altitude) if vehicle.altitude else 0.0,
                    'accuracy': 10.0,  # Not stored in current model
                    'timestamp': vehicle.tsusermanu
                }
            }
            
            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle location: {str(e)}")
            return {'message': 'Erro ao buscar localização do veículo'}, 500
