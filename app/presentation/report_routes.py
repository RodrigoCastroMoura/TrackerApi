from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Vehicle, VehicleData
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import DoesNotExist
import logging
from bson.objectid import ObjectId
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

api = Namespace('reports', description='Vehicle reports operations')

# Models for Swagger
trip_model = api.model('Trip', {
    'start': fields.Raw(description='Informações de início'),
    'end': fields.Raw(description='Informações de fim'),
    'distance': fields.Float(description='Distância em km'),
    'duration': fields.Integer(description='Duração em segundos'),
    'avg_speed': fields.Float(description='Velocidade média')
})

summary_model = api.model('Summary', {
    'total_distance': fields.Float(description='Distância total em km'),
    'total_time': fields.Integer(description='Tempo total em segundos'),
    'total_trips': fields.Integer(description='Total de viagens'),
    'total_stops': fields.Integer(description='Total de paradas'),
    'avg_speed': fields.Float(description='Velocidade média'),
    'max_speed': fields.Float(description='Velocidade máxima'),
    'fuel_consumption': fields.Float(description='Consumo estimado de combustível')
})

vehicle_report_model = api.model('VehicleReport', {
    'vehicle_id': fields.String(description='ID do veículo'),
    'plate': fields.String(description='Placa do veículo'),
    'period': fields.Raw(description='Período do relatório'),
    'summary': fields.Nested(summary_model),
    'trips': fields.List(fields.Nested(trip_model))
})


@api.route('/vehicles/<id>')
@api.param('id', 'Vehicle identifier')
class VehicleReport(Resource):
    
    @api.doc('get_vehicle_report',
             params={
                 'start_date': {'type': 'string', 'required': True, 'description': 'Data início (ISO 8601)'},
                 'end_date': {'type': 'string', 'required': True, 'description': 'Data fim (ISO 8601)'},
                 'type': {'type': 'string', 'enum': ['summary', 'detailed', 'stops', 'trips'], 
                         'description': 'Tipo de relatório'}
             })
    @api.marshal_with(vehicle_report_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Relatório de uso do veículo"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            report_type = request.args.get('type', 'summary')
            
            if not start_date or not end_date:
                return {'message': 'start_date e end_date são obrigatórios'}, 400
            
            vehicle = Vehicle.objects.get(
                id=id,
                visible=True,
                company_id=current_user.company_id
            )
            
            # Parse dates
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            
            # Get location history
            locations_data = VehicleData.objects(
                imei=vehicle.IMEI,
                deviceTimestamp__gte=start,
                deviceTimestamp__lte=end
            ).order_by('deviceTimestamp')
            
            # Calculate statistics
            total_distance = 0.0
            max_speed = 0.0
            trips = []
            stops = []
            
            prev_loc = None
            prev_time = None
            trip_start = None
            trip_start_time = None
            trip_distance = 0.0
            is_moving = False
            
            for loc in locations_data:
                if not loc.latitude or not loc.longitude:
                    continue
                
                lat = float(loc.latitude)
                lng = float(loc.longitude)
                current_time = loc.deviceTimestamp
                
                if prev_loc:
                    # Calculate distance
                    lat_diff = lat - prev_loc[0]
                    lng_diff = lng - prev_loc[1]
                    distance = ((lat_diff ** 2) + (lng_diff ** 2)) ** 0.5 * 111  # Approx km
                    
                    # Check if moving (distance > 100m in the interval)
                    if distance > 0.1:  # 100 meters
                        is_moving = True
                        total_distance += distance
                        trip_distance += distance
                        
                        if not trip_start:
                            trip_start = prev_loc
                            trip_start_time = prev_time
                    else:
                        # Vehicle stopped
                        if is_moving and trip_start:
                            # End trip
                            duration = int((prev_time - trip_start_time).total_seconds())
                            avg_speed = (trip_distance / (duration / 3600)) if duration > 0 else 0.0
                            
                            trips.append({
                                'start': {
                                    'timestamp': trip_start_time.isoformat(),
                                    'location': f'{trip_start[0]:.4f}, {trip_start[1]:.4f}'
                                },
                                'end': {
                                    'timestamp': prev_time.isoformat(),
                                    'location': f'{prev_loc[0]:.4f}, {prev_loc[1]:.4f}'
                                },
                                'distance': round(trip_distance, 2),
                                'duration': duration,
                                'avg_speed': round(avg_speed, 2)
                            })
                            
                            trip_start = None
                            trip_distance = 0.0
                            is_moving = False
                        
                        # Track stop
                        if prev_time and (current_time - prev_time).total_seconds() > 300:  # > 5 min
                            stops.append({
                                'lat': lat,
                                'lng': lng,
                                'arrival': prev_time,
                                'departure': current_time,
                                'duration': int((current_time - prev_time).total_seconds())
                            })
                
                prev_loc = [lat, lng]
                prev_time = current_time
            
            # Close last trip if still moving
            if is_moving and trip_start and prev_loc and prev_time:
                duration = int((prev_time - trip_start_time).total_seconds())
                avg_speed = (trip_distance / (duration / 3600)) if duration > 0 else 0.0
                
                trips.append({
                    'start': {
                        'timestamp': trip_start_time.isoformat(),
                        'location': f'{trip_start[0]:.4f}, {trip_start[1]:.4f}'
                    },
                    'end': {
                        'timestamp': prev_time.isoformat(),
                        'location': f'{prev_loc[0]:.4f}, {prev_loc[1]:.4f}'
                    },
                    'distance': round(trip_distance, 2),
                    'duration': duration,
                    'avg_speed': round(avg_speed, 2)
                })
            
            # Calculate overall statistics
            total_time = int((end - start).total_seconds())
            avg_speed = (total_distance / (total_time / 3600)) if total_time > 0 else 0.0
            
            # Estimate fuel consumption (simplified - would need vehicle specs)
            # Assuming average consumption of 10 km/L
            fuel_consumption = total_distance / 10.0 if total_distance > 0 else 0.0
            
            response = {
                'vehicle_id': str(vehicle.id),
                'plate': vehicle.dsplaca or 'N/A',
                'period': {
                    'start': start.isoformat(),
                    'end': end.isoformat()
                },
                'summary': {
                    'total_distance': round(total_distance, 2),
                    'total_time': total_time,
                    'total_trips': len(trips),
                    'total_stops': len(stops),
                    'avg_speed': round(avg_speed, 2),
                    'max_speed': max_speed,
                    'fuel_consumption': round(fuel_consumption, 2)
                },
                'trips': trips if report_type in ['detailed', 'trips'] else []
            }
            
            return response, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except ValueError as e:
            return {'message': f'Formato de data inválido: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error generating vehicle report: {str(e)}")
            return {'message': 'Erro ao gerar relatório do veículo'}, 500


@api.route('/summary')
class CompanySummaryReport(Resource):
    
    @api.doc('get_company_summary',
             params={
                 'start_date': {'type': 'string', 'required': True, 'description': 'Data início'},
                 'end_date': {'type': 'string', 'required': True, 'description': 'Data fim'}
             })
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Relatório resumido de todos os veículos da empresa"""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            
            if not start_date or not end_date:
                return {'message': 'start_date e end_date são obrigatórios'}, 400
            
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            
            # Get all vehicles from company
            vehicles = Vehicle.objects(
                company_id=current_user.company_id,
                visible=True
            )
            
            total_vehicles = vehicles.count()
            active_vehicles = 0
            total_distance_all = 0.0
            vehicle_summaries = []
            
            for vehicle in vehicles:
                # Get location data for this vehicle
                locations_count = VehicleData.objects(
                    imei=vehicle.IMEI,
                    deviceTimestamp__gte=start,
                    deviceTimestamp__lte=end
                ).count()
                
                if locations_count > 0:
                    active_vehicles += 1
                    
                    # Calculate distance (simplified)
                    locations = VehicleData.objects(
                        imei=vehicle.IMEI,
                        deviceTimestamp__gte=start,
                        deviceTimestamp__lte=end
                    ).order_by('deviceTimestamp')
                    
                    distance = 0.0
                    prev_loc = None
                    
                    for loc in locations:
                        if loc.latitude and loc.longitude and prev_loc:
                            lat_diff = float(loc.latitude) - prev_loc[0]
                            lng_diff = float(loc.longitude) - prev_loc[1]
                            distance += ((lat_diff ** 2) + (lng_diff ** 2)) ** 0.5 * 111
                        
                        if loc.latitude and loc.longitude:
                            prev_loc = [float(loc.latitude), float(loc.longitude)]
                    
                    total_distance_all += distance
                    
                    vehicle_summaries.append({
                        'vehicle_id': str(vehicle.id),
                        'plate': vehicle.dsplaca or 'N/A',
                        'distance': round(distance, 2),
                        'data_points': locations_count
                    })
            
            response = {
                'period': {
                    'start': start.isoformat(),
                    'end': end.isoformat()
                },
                'company_id': str(current_user.company_id.id),
                'total_vehicles': total_vehicles,
                'active_vehicles': active_vehicles,
                'total_distance': round(total_distance_all, 2),
                'vehicles': vehicle_summaries
            }
            
            return response, 200
            
        except ValueError as e:
            return {'message': f'Formato de data inválido: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error generating company summary: {str(e)}")
            return {'message': 'Erro ao gerar resumo da empresa'}, 500
