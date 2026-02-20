from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Vehicle, VehicleData
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging
from bson.objectid import ObjectId
from datetime import datetime
from app.infrastructure.redis_cache import vehicle_cache
import re

logger = logging.getLogger(__name__)

api = Namespace('vehicles', description='Vehicle operations')

# Vehicle Model for Swagger
vehicle_model = api.model('Vehicle', {
    'id': fields.String(readonly=True, description='Vehicle unique identifier'),
    'IMEI': fields.String(required=True, description='IMEI único do dispositivo'),
    'dsplaca': fields.String(description='Placa do veículo'),
    'dsmodelo': fields.String(description='Modelo do veículo'),
    'dsmarca': fields.String(description='Marca do veículo'),
    'tipo': fields.String(description='Tipo do veículo', enum=['carro', 'moto', 'caminhao', 'van', 'onibus', 'outro']),
    'ano': fields.Integer(description='Ano do veículo'),
    'comandobloqueo': fields.Boolean(description='Comando de bloqueio pendente'),
    'bloqueado': fields.Boolean(description='Status atual de bloqueio', default=False),
    'comandotrocarip': fields.Boolean(description='Comando para trocar IP pendente'),
    'ignicao': fields.Boolean(description='Status da ignição', default=False),
    'status': fields.String(description='Status do veículo', enum=['active', 'inactive'], default='active'),
    'created_at': fields.DateTime(readonly=True),
    'updated_at': fields.DateTime(readonly=True)
})

vehicle_update_model = api.model('VehicleUpdate', {
    'dsplaca': fields.String(description='Placa do veículo'),
    'dsmodelo': fields.String(description='Modelo do veículo'),
    'dsmarca': fields.String(description='Marca do veículo'),
    'tipo': fields.String(description='Tipo do veículo', enum=['carro', 'moto', 'caminhao', 'van', 'onibus', 'outro']),
    'ano': fields.Integer(description='Ano do veículo')
})

vehicle_data_model = api.model('VehicleData', {
    'id': fields.String(readonly=True),
    'imei': fields.String(required=True),
    'longitude': fields.String(description='Longitude'),
    'latitude': fields.String(description='Latitude'),
    'altitude': fields.String(description='Altitude'),
    'timestamp': fields.DateTime(description='Data do servidor'),
    'deviceTimestamp': fields.DateTime(description='Data do dispositivo'),
    'mensagem_raw': fields.String(description='Mensagem original')
})

pagination_model = api.model('PaginatedVehicles', {
    'vehicles': fields.List(fields.Nested(vehicle_model)),
    'total': fields.Integer(description='Total de veículos'),
    'page': fields.Integer(description='Página atual'),
    'per_page': fields.Integer(description='Itens por página'),
    'total_pages': fields.Integer(description='Total de páginas')
})

block_command_model = api.model('BlockCommand', {
    'comando': fields.String(required=True, enum=['bloquear', 'desbloquear'], 
                             description='Comando de bloqueio/desbloqueio')
})

@api.route('')
class VehicleList(Resource):
    
    @api.doc('list_vehicles',
             params={
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 10},
                 'customer_id': {'type': 'string', 'description': 'Filtrar por ID do cliente'},
                 'placa': {'type': 'string', 'description': 'Filtrar por placa'},
                 'imei': {'type': 'string', 'description': 'Filtrar por IMEI'},
                 'tipo': {'type': 'string', 'enum': ['carro', 'moto', 'caminhao', 'van', 'onibus', 'outro'], 'description': 'Filtrar por tipo de veículo'},
                 'status': {'type': 'string', 'enum': ['active', 'inactive']},
                 'bloqueado': {'type': 'boolean', 'description': 'Filtrar por status de bloqueio'}
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Listar veículos com paginação e filtros"""
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 10))))
            
            # Build query - filter by company (multi-tenancy)
            query = {'visible': True, 'company_id': current_user.company_id}
            
            # Filters
            if request.args.get('customer_id'):
                if ObjectId.is_valid(request.args.get('customer_id')):
                    query['customer_id'] = ObjectId(request.args.get('customer_id'))
                else:
                    return {'message': 'customer_id inválido'}, 400

            if request.args.get('placa'):
                query['dsplaca'] = {'$regex': request.args.get('placa'), '$options': 'i'}
            
            if request.args.get('imei'):
                query['IMEI'] = request.args.get('imei')
            
            if request.args.get('tipo'):
                query['tipo'] = request.args.get('tipo')
            
            if request.args.get('status'):
                query['status'] = request.args.get('status')
            
            if request.args.get('bloqueado') is not None:
                query['bloqueado'] = request.args.get('bloqueado').lower() == 'true'
            
            # Execute query
            total = Vehicle.objects(**query).count()
            total_pages = (total + per_page - 1) // per_page
            vehicles = Vehicle.objects(**query).order_by('-created_at').skip(
                (page - 1) * per_page).limit(per_page)
            
            return {
                'vehicles': [v.to_dict() for v in vehicles],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200
            
        except Exception as e:
            logger.error(f"Error listing vehicles: {str(e)}")
            return {'message': 'Erro ao listar veículos'}, 500
    
    @api.doc('create_vehicle')
    @api.expect(vehicle_model)
    @token_required
    @require_permission('vehicle', 'write')
    def post(self, current_user):
        """Criar novo veículo"""
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400
            
            # Validate required field
            if 'IMEI' not in data or not data['IMEI']:
                return {'message': 'IMEI é obrigatório'}, 400
            
            # Validate placa format if provided
            if 'dsplaca' in data and data['dsplaca']:
                placa = data['dsplaca'].upper()
                # Brazilian plate format validation (old and Mercosul)
                if not re.match(r'^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$', placa):
                    return {'message': 'Formato de placa inválido'}, 400
                data['dsplaca'] = placa
            
            # Validate customer_id belongs to the same company (multi-tenancy security)
            customer = None
            if data.get('customer_id'):
                if not ObjectId.is_valid(data['customer_id']):
                    return {'message': 'customer_id inválido'}, 400
                
                from app.domain.models import Customer
                try:
                    customer = Customer.objects.get(
                        id=data['customer_id'],
                        company_id=current_user.company_id,
                        visible=True
                    )
                except DoesNotExist:
                    return {'message': 'Cliente não encontrado ou não pertence à sua empresa'}, 403
            
            try:
                vehicle = Vehicle(
                    IMEI=data['IMEI'],
                    dsplaca=data.get('dsplaca'),
                    dsmodelo=data.get('dsmodelo'),
                    dsmarca=data.get('dsmarca'),
                    tipo=data.get('tipo'),
                    ano=data.get('ano'),
                    company_id=current_user.company_id,  # Multi-tenancy
                    customer_id=customer,
                    created_by=current_user,
                    updated_by=current_user
                )
                
                if 'ultimoalertabateria' in data:
                    vehicle.ultimoalertabateria = datetime.fromisoformat(data['ultimoalertabateria'])
                
                vehicle.save()
                vehicle_data = vehicle.to_dict()

                campos_desejados = ['id', 'IMEI', 'dsplaca', 'dsmodelo', 'created_by', 'created_at']
                response_data = {k: vehicle_data[k] for k in campos_desejados if k in vehicle_data}
            
                return response_data, 201
                
            except NotUniqueError as e:
                if 'IMEI' in str(e):
                    return {'message': 'IMEI já cadastrado'}, 409
                if 'dsplaca' in str(e):
                    return {'message': 'Placa já cadastrada'}, 409
                return {'message': 'Erro de duplicação'}, 409
                
        except Exception as e:
            logger.error(f"Error creating vehicle: {str(e)}")
            return {'message': 'Erro ao criar veículo'}, 500

@api.route('/<id>')
@api.param('id', 'Vehicle identifier')
class VehicleResource(Resource):
    
    @api.doc('get_vehicle')
    @api.marshal_with(vehicle_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Obter veículo específico"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            return vehicle.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle: {str(e)}")
            return {'message': 'Erro ao buscar veículo'}, 500
    
    @api.doc('update_vehicle')
    @api.expect(vehicle_update_model)
    @token_required
    @require_permission('vehicle', 'update')
    def put(self, current_user, id):
        """Atualizar veículo"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            data = request.get_json()
            
            # Validate customer_id belongs to the same company (multi-tenancy security)
            if 'customer_id' in data and data['customer_id']:
                if not ObjectId.is_valid(data['customer_id']):
                    return {'message': 'customer_id inválido'}, 400
                
                from app.domain.models import Customer
                try:
                    customer = Customer.objects.get(
                        id=data['customer_id'],
                        company_id=current_user.company_id,
                        visible=True
                    )
                    vehicle.customer_id = customer
                except DoesNotExist:
                    return {'message': 'Cliente não encontrado ou não pertence à sua empresa'}, 403
            
            # Update fields
            if 'dsplaca' in data:
                if data['dsplaca']:
                    placa = data['dsplaca'].upper()
                    if not re.match(r'^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$', placa):
                        return {'message': 'Formato de placa inválido'}, 400
                    # Check if placa is already in use by another vehicle
                    existing = Vehicle.objects(dsplaca=placa, id__ne=id).first()
                    if existing:
                        return {'message': 'Placa já está em uso'}, 409
                    vehicle.dsplaca = placa
                else:
                    vehicle.dsplaca = None
            
            if 'dsmodelo' in data:
                vehicle.dsmodelo = data['dsmodelo']
            
            if 'dsmarca' in data:
                vehicle.dsmarca = data['dsmarca']
            
            if 'tipo' in data:
                vehicle.tipo = data['tipo']

            if 'ano' in data:
                vehicle.ano = data['ano']

            vehicle.updated_by = current_user
            vehicle.save()

            campos_desejados = ['id', 'IMEI', 'dsplaca', 'dsmodelo', 'updated_by','updated_at']
            vehicle_data = vehicle.to_dict()
            response_data = {k: vehicle_data[k] for k in campos_desejados if k in vehicle_data}

            return response_data, 201
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error updating vehicle: {str(e)}")
            return {'message': 'Erro ao atualizar veículo'}, 500
    
    @api.doc('delete_vehicle')
    @token_required
    @require_permission('vehicle', 'delete')
    def delete(self, current_user, id):
        """Deletar veículo (soft delete)"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            vehicle.visible = False
            vehicle.status = 'inactive'
            vehicle.updated_by = current_user
            vehicle.save()
            
            return {'message': 'Veículo deletado com sucesso'}, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error deleting vehicle: {str(e)}")
            return {'message': 'Erro ao deletar veículo'}, 500

@api.route('/<id>/block')
@api.param('id', 'Vehicle identifier')
class VehicleBlock(Resource):
    
    @api.doc('block_vehicle')
    @api.expect(block_command_model)
    @token_required
    @require_permission('customer', 'update')
    def post(self, current_user, id):
        """Enviar comando de bloqueio/desbloqueio"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
             # Build query - filter by company (multi-tenancy)
            query = {'id': id, 'visible': True, 'company_id': current_user.company_id}

            if current_user.role != 'admin':
                query['customer_id'] = current_user.id

            vehicle = Vehicle.objects.get(**query)

            data = request.get_json()
            
            if 'comando' not in data:
                return {'message': 'Comando não especificado'}, 400
            
            if data['comando'] == 'bloquear':
                vehicle.comandobloqueo = False
                message = 'Comando de bloqueio enviado'
            else:
                vehicle.comandobloqueo = True
                message = 'Comando de desbloqueio enviado'
            
            vehicle.updated_by = current_user.id

            vehicle.save()

            imei = vehicle.imei
            updates = {
                "comandobloqueo": vehicle.comandobloqueo,
                "updated_by": vehicle.updated_by,
            }
            vehicle_cache.update_vehicle_fields(imei, updates)
            
            logger.info(f"Block command sent to vehicle {vehicle.IMEI}: {data['comando']}")
            
            return {
                'message': message,
                'id': str(vehicle.id),
                'IMEI': vehicle.IMEI,
                'comando': data['comando']
            }, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado ou inativo'}, 404
        except Exception as e:
            logger.error(f"Error sending block command: {str(e)}")
            return {'message': 'Erro ao enviar comando'}, 500


    
    @api.doc('get_vehicle_location',
             params={
                 'limit': {'type': 'integer', 'default': 10, 'description': 'Número de posições'},
                 'start_date': {'type': 'string', 'description': 'Data inicial (ISO format)'},
                 'end_date': {'type': 'string', 'description': 'Data final (ISO format)'}
             })
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Obter histórico de localização do veículo"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do veículo inválido'}, 400
            
            vehicle = Vehicle.objects.get(id=id, visible=True, company_id=current_user.company_id)
            
            # Build query for vehicle data
            query = {'imei': vehicle.IMEI}
            
            # Date filters
            if request.args.get('start_date'):
                start = datetime.fromisoformat(request.args.get('start_date'))
                query['deviceTimestamp__gte'] = start
            
            if request.args.get('end_date'):
                end = datetime.fromisoformat(request.args.get('end_date'))
                query['deviceTimestamp__lte'] = end
            
            limit = min(100, int(request.args.get('limit', 10)))
            
            # Get location data
            locations = VehicleData.objects(**query).order_by('-deviceTimestamp').limit(limit)
            
            return {
                'vehicle_id': str(vehicle.id),
                'IMEI': vehicle.IMEI,
                'placa': vehicle.dsplaca,
                'locations': [loc.to_dict() for loc in locations],
                'total': len(locations)
            }, 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle location: {str(e)}")
            return {'message': 'Erro ao buscar localização'}, 500

@api.route('/by-placa/<placa>')
@api.param('placa', 'Vehicle placa')
class VehicleByPlaca(Resource):
    
    @api.doc('get_vehicle_by_placa')
    @api.marshal_with(vehicle_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, placa):
        """Buscar veículo por placa"""
        try:
            vehicle = Vehicle.objects.get(dsplaca=placa, visible=True, company_id=current_user.company_id)
            return vehicle.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting vehicle by IMEI: {str(e)}")
            return {'message': 'Erro ao buscar veículo'}, 500