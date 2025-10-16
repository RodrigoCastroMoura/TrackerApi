from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Vehicle, VehicleData, Company
from app.presentation.auth_routes import token_required, require_permission
from app.infrastructure.geocoding_service import get_geocoding_service
from mongoengine.errors import DoesNotExist
import logging
from bson.objectid import ObjectId
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

api = Namespace('tracking', description='Vehicle tracking operations')

# Models for Swagger
location_model = api.model('Location', {
    'lat': fields.Float(description='Latitude'),
    'lng': fields.Float(description='Longitude'),
    'address': fields.String(description='Endereço'),
    'speed': fields.Float(description='Velocidade em km/h'),
    'heading': fields.Float(description='Direção em graus'),
    'timestamp': fields.DateTime(description='Timestamp da localização')
})

tracker_info_model = api.model('TrackerInfo', {
    'serial': fields.String(description='Serial do rastreador (IMEI)'),
    'battery': fields.Float(description='Nível de bateria (%)'),
    'signal_strength': fields.Integer(description='Força do sinal'),
    'online': fields.Boolean(description='Status online')
})

vehicle_tracking_model = api.model('VehicleTracking', {
    'id': fields.String(readonly=True),
    'plate': fields.String(description='Placa do veículo'),
    'customer_id': fields.String(description='ID do cliente'),
    'customer_name': fields.String(description='Nome do cliente'),
    'location': fields.Nested(location_model),
    'status': fields.String(description='Status do veículo'),
    'tracker_serial': fields.String(description='Serial do rastreador'),
    'is_tracking': fields.Boolean(description='Se está rastreando')
})

vehicle_location_response_model = api.model('VehicleLocationResponse', {
    'vehicle_id': fields.String(description='ID do veículo'),
    'plate': fields.String(description='Placa do veículo'),
    'location': fields.Nested(location_model),
    'tracker': fields.Nested(tracker_info_model)
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
                 'customer_id': {'type': 'string', 'description': 'Filtrar por cliente'},
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 20}
             })
    @api.marshal_with(tracking_pagination_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Lista todos os veículos com última localização conhecida para visualização no mapa"""
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 20))))
            
            # Build query - filter by company
            query = {'visible': True, 'company_id': current_user.company_id}
            
            # Additional filters
            status_filter = request.args.get('status')
            if status_filter == 'blocked':
                query['bloqueado'] = True
            elif status_filter == 'active':
                query['status'] = 'active'
                query['bloqueado'] = False
            
            # Validate customer_id belongs to the same company (multi-tenancy security)
            customer_id = request.args.get('customer_id')
            if customer_id:
                if not ObjectId.is_valid(customer_id):
                    return {'message': 'customer_id inválido'}, 400
                
                from app.domain.models import Customer
                try:
                    customer = Customer.objects.get(
                        id=customer_id,
                        company_id=current_user.company_id,
                        visible=True
                    )
                    query['customer_id'] = customer.id
                except DoesNotExist:
                    return {'message': 'Cliente não encontrado ou não pertence à sua empresa'}, 403
            
            # Execute query
            total = Vehicle.objects(**query).count()
            vehicles = Vehicle.objects(**query).order_by('-created_at').skip(
                (page - 1) * per_page).limit(per_page)
            
            # Get geocoding service
            geocoding = get_geocoding_service()
            
            # Get last location for each vehicle
            result_vehicles = []
            for vehicle in vehicles:
                # Get last location from VehicleData
                last_location = VehicleData.objects(imei=vehicle.IMEI).order_by('-timestamp').first()
                
                vehicle_data = {
                    'id': str(vehicle.id),
                    'plate': vehicle.dsplaca or 'N/A',
                    'customer_id': str(vehicle.customer_id.id) if vehicle.customer_id else None,
                    'customer_name': vehicle.customer_id.name if vehicle.customer_id else 'N/A',
                    'status': 'blocked' if vehicle.bloqueado else vehicle.status,
                    'tracker_serial': vehicle.IMEI,
                    'is_tracking': last_location is not None
                }
                
                if last_location:
                    lat = float(last_location.latitude) if last_location.latitude else 0.0
                    lng = float(last_location.longitude) if last_location.longitude else 0.0
                    
                    # Get address from coordinates using Nominatim
                    address = 'N/A'
                    if lat != 0.0 and lng != 0.0:
                        address = geocoding.get_address_or_fallback(lat, lng)
                    
                    vehicle_data['location'] = {
                        'lat': lat,
                        'lng': lng,
                        'address': address,
                        'speed': 0.0,  # Not stored in current model
                        'heading': 0.0,  # Not stored in current model
                        'timestamp': last_location.deviceTimestamp
                    }
                else:
                    vehicle_data['location'] = None
                
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
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Retorna a localização atual de um veículo específico"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            
            # Get last location
            last_location = VehicleData.objects(imei=vehicle.IMEI).order_by('-deviceTimestamp').first()
            
            if not last_location:
                return {'message': 'Nenhuma localização encontrada para este veículo'}, 404
            
            # Get geocoding service
            geocoding = get_geocoding_service()
            
            lat = float(last_location.latitude) if last_location.latitude else 0.0
            lng = float(last_location.longitude) if last_location.longitude else 0.0
            
            # Get address from coordinates using Nominatim
            address = 'N/A'
            if lat != 0.0 and lng != 0.0:
                address = geocoding.get_address_or_fallback(lat, lng)
            
            response = {
                'vehicle_id': str(vehicle.id),
                'plate': vehicle.dsplaca or 'N/A',
                'location': {
                    'lat': lat,
                    'lng': lng,
                    'address': address,
                    'speed': 0.0,
                    'heading': 0.0,
                    'altitude': float(last_location.altitude) if last_location.altitude else 0.0,
                    'accuracy': 10.0,  # Not stored in current model
                    'timestamp': last_location.deviceTimestamp
                },
                'tracker': {
                    'serial': vehicle.IMEI,
                    'battery': vehicle.bateriavoltagem or 0.0,
                    'signal_strength': 4,  # Not stored in current model
                    'online': True  # Assume online if we have recent data
                }
            }
            
            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle location: {str(e)}")
            return {'message': 'Erro ao buscar localização do veículo'}, 500


@api.route('/vehicles/<id>/history')
@api.param('id', 'Vehicle identifier')
class VehicleLocationHistory(Resource):
    
    @api.doc('get_vehicle_history',
             params={
                 'start_date': {'type': 'string', 'required': True, 'description': 'Data início (ISO 8601)'},
                 'end_date': {'type': 'string', 'required': True, 'description': 'Data fim (ISO 8601)'},
                 'interval': {'type': 'string', 'enum': ['1min', '5min', '15min', '1hour'], 'description': 'Intervalo'}
             })
    @api.marshal_with(vehicle_history_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Retorna histórico de localizações do veículo para visualização de trajeto"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            
            if not start_date or not end_date:
                return {'message': 'start_date e end_date são obrigatórios'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            
            # Parse dates
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            
            # Get location history
            locations_data = VehicleData.objects(
                imei=vehicle.IMEI,
                deviceTimestamp__gte=start,
                deviceTimestamp__lte=end
            ).order_by('deviceTimestamp')
            
            locations = []
            total_distance = 0.0
            max_speed = 0.0
            prev_loc = None
            
            for loc in locations_data:
                location_point = {
                    'lat': float(loc.latitude) if loc.latitude else 0.0,
                    'lng': float(loc.longitude) if loc.longitude else 0.0,
                    'speed': 0.0,  # Not stored
                    'heading': 0.0,  # Not stored
                    'timestamp': loc.deviceTimestamp
                }
                
                # Calculate distance if we have previous location
                if prev_loc and loc.latitude and loc.longitude:
                    # Simple distance calculation (would use haversine in production)
                    lat_diff = float(loc.latitude) - prev_loc['lat']
                    lng_diff = float(loc.longitude) - prev_loc['lng']
                    distance = ((lat_diff ** 2) + (lng_diff ** 2)) ** 0.5 * 111  # Approx km
                    total_distance += distance
                
                locations.append(location_point)
                prev_loc = location_point
            
            # Calculate stats
            time_diff = (end - start).total_seconds()
            avg_speed = (total_distance / (time_diff / 3600)) if time_diff > 0 else 0.0
            
            response = {
                'vehicle_id': str(vehicle.id),
                'plate': vehicle.dsplaca or 'N/A',
                'period': {
                    'start': start.isoformat(),
                    'end': end.isoformat()
                },
                'locations': locations,
                'total_distance': round(total_distance, 2),
                'total_time_moving': int(time_diff),
                'max_speed': max_speed,
                'avg_speed': round(avg_speed, 2)
            }
            
            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except ValueError as e:
            return {'message': f'Formato de data inválido: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error getting vehicle history: {str(e)}")
            return {'message': 'Erro ao buscar histórico do veículo'}, 500


@api.route('/vehicles/<id>/route')
@api.param('id', 'Vehicle identifier')
class VehicleRoute(Resource):
    
    @api.doc('get_vehicle_route',
             params={
                 'start_date': {'type': 'string', 'required': True, 'description': 'Data início'},
                 'end_date': {'type': 'string', 'required': True, 'description': 'Data fim'}
             })
    @api.marshal_with(vehicle_route_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Retorna rota/trajeto otimizado do veículo para desenhar no mapa"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            
            if not start_date or not end_date:
                return {'message': 'start_date e end_date são obrigatórios'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            
            # Parse dates
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            
            # Get geocoding service
            geocoding = get_geocoding_service()
            
            # Get location history
            locations_data = VehicleData.objects(
                imei=vehicle.IMEI,
                deviceTimestamp__gte=start,
                deviceTimestamp__lte=end
            ).order_by('deviceTimestamp')
            
            points = []
            stops = []
            total_distance = 0.0
            prev_loc = None
            prev_time = None
            
            for loc in locations_data:
                if loc.latitude and loc.longitude:
                    lat = float(loc.latitude)
                    lng = float(loc.longitude)
                    points.append([lat, lng])
                    
                    # Detect stops (simplified - same location for > 5 min)
                    if prev_loc and abs(lat - prev_loc[0]) < 0.001 and abs(lng - prev_loc[1]) < 0.001:
                        if prev_time and (loc.deviceTimestamp - prev_time).total_seconds() > 300:
                            # Get address for the stop location using Nominatim
                            address = geocoding.get_address_or_fallback(lat, lng)
                            
                            stops.append({
                                'lat': lat,
                                'lng': lng,
                                'address': address,
                                'arrival': prev_time,
                                'departure': loc.deviceTimestamp,
                                'duration': int((loc.deviceTimestamp - prev_time).total_seconds())
                            })
                    
                    # Calculate distance
                    if prev_loc:
                        lat_diff = lat - prev_loc[0]
                        lng_diff = lng - prev_loc[1]
                        distance = ((lat_diff ** 2) + (lng_diff ** 2)) ** 0.5 * 111
                        total_distance += distance
                    
                    prev_loc = [lat, lng]
                    prev_time = loc.deviceTimestamp
            
            response = {
                'vehicle_id': str(vehicle.id),
                'route': {
                    'points': points,
                    'polyline': '',  # Would encode with polyline library
                    'total_distance': round(total_distance, 2),
                    'duration': int((end - start).total_seconds()),
                    'stops': stops
                }
            }
            
            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except ValueError as e:
            return {'message': f'Formato de data inválido: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error getting vehicle route: {str(e)}")
            return {'message': 'Erro ao gerar rota do veículo'}, 500
