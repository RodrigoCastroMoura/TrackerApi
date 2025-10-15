from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Alert, Vehicle
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import DoesNotExist, ValidationError
import logging
from bson.objectid import ObjectId

logger = logging.getLogger(__name__)

api = Namespace('alerts', description='Alert management operations')

# Models for Swagger
alert_model = api.model('Alert', {
    'id': fields.String(readonly=True, description='Alert unique identifier'),
    'vehicle_id': fields.String(required=True, description='ID do veículo'),
    'vehicle_plate': fields.String(readonly=True, description='Placa do veículo'),
    'type': fields.String(required=True, description='Tipo de alerta', 
                          enum=['speed_limit', 'geofence', 'ignition', 'low_battery', 'offline', 'panic_button']),
    'condition': fields.Raw(description='Condições do alerta (ex: {"max_speed": 80})'),
    'actions': fields.List(fields.String(enum=['email', 'sms', 'notification']), 
                           description='Ações quando alerta é disparado'),
    'recipients': fields.List(fields.String, description='Lista de emails ou telefones'),
    'active': fields.Boolean(description='Se o alerta está ativo'),
    'created_at': fields.DateTime(readonly=True),
    'updated_at': fields.DateTime(readonly=True)
})

alert_create_model = api.model('AlertCreate', {
    'vehicle_id': fields.String(required=True, description='ID do veículo'),
    'type': fields.String(required=True, description='Tipo de alerta', 
                          enum=['speed_limit', 'geofence', 'ignition', 'low_battery', 'offline', 'panic_button']),
    'condition': fields.Raw(description='Condições do alerta'),
    'actions': fields.List(fields.String(enum=['email', 'sms', 'notification'])),
    'recipients': fields.List(fields.String, description='Lista de emails ou telefones')
})

alert_update_model = api.model('AlertUpdate', {
    'type': fields.String(description='Tipo de alerta'),
    'condition': fields.Raw(description='Condições do alerta'),
    'actions': fields.List(fields.String(enum=['email', 'sms', 'notification'])),
    'recipients': fields.List(fields.String),
    'active': fields.Boolean(description='Se o alerta está ativo')
})


@api.route('')
class AlertList(Resource):
    
    @api.doc('list_alerts',
             responses={
                 200: 'Success',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 500: 'Erro interno do servidor'
             })
    @api.marshal_list_with(alert_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Lista alertas configurados da empresa"""
        try:
            # Filter by company
            alerts = Alert.objects(
                company_id=current_user.company_id,
                visible=True
            ).order_by('-created_at')
            
            return [alert.to_dict() for alert in alerts], 200
            
        except Exception as e:
            logger.error(f"Error listing alerts: {str(e)}")
            return {'message': 'Erro ao listar alertas'}, 500
    
    @api.doc('create_alert')
    @api.expect(alert_create_model)
    @api.marshal_with(alert_model, code=201)
    @token_required
    @require_permission('vehicle', 'write')
    def post(self, current_user):
        """Cria novo alerta"""
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400
            
            # Validate required fields
            if 'vehicle_id' not in data or not data['vehicle_id']:
                return {'message': 'vehicle_id é obrigatório'}, 400
            
            if 'type' not in data or not data['type']:
                return {'message': 'type é obrigatório'}, 400
            
            # Validate vehicle exists and belongs to company
            if not ObjectId.is_valid(data['vehicle_id']):
                return {'message': 'vehicle_id inválido'}, 400
            
            vehicle = Vehicle.objects.get(
                id=data['vehicle_id'],
                company_id=current_user.company_id,
                visible=True
            )
            
            # Validate type
            valid_types = ['speed_limit', 'geofence', 'ignition', 'low_battery', 'offline', 'panic_button']
            if data['type'] not in valid_types:
                return {'message': f'Tipo inválido. Use: {", ".join(valid_types)}'}, 400
            
            # Validate condition based on type
            condition = data.get('condition', {})
            if data['type'] == 'speed_limit' and 'max_speed' not in condition:
                return {'message': 'Condição speed_limit requer max_speed'}, 400
            
            # Create alert
            alert = Alert(
                vehicle_id=vehicle,
                company_id=current_user.company_id,
                type=data['type'],
                condition=condition,
                actions=data.get('actions', ['notification']),
                recipients=data.get('recipients', []),
                active=True,
                created_by=current_user,
                updated_by=current_user
            )
            
            alert.save()
            
            logger.info(f"Alert created: {alert.id} for vehicle {vehicle.IMEI}")
            
            return alert.to_dict(), 201
            
        except DoesNotExist:
            return {'message': 'Veículo não encontrado ou não pertence à sua empresa'}, 404
        except ValidationError as e:
            logger.error(f"Validation error creating alert: {str(e)}")
            return {'message': f'Erro de validação: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error creating alert: {str(e)}")
            return {'message': 'Erro ao criar alerta'}, 500


@api.route('/<id>')
@api.param('id', 'Alert identifier')
class AlertResource(Resource):
    
    @api.doc('get_alert')
    @api.marshal_with(alert_model)
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user, id):
        """Obtém alerta específico"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do alerta inválido'}, 400
            
            alert = Alert.objects.get(
                id=id,
                company_id=current_user.company_id,
                visible=True
            )
            
            return alert.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Alerta não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting alert: {str(e)}")
            return {'message': 'Erro ao buscar alerta'}, 500
    
    @api.doc('update_alert')
    @api.expect(alert_update_model)
    @api.marshal_with(alert_model)
    @token_required
    @require_permission('vehicle', 'update')
    def put(self, current_user, id):
        """Atualiza alerta"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do alerta inválido'}, 400
            
            alert = Alert.objects.get(
                id=id,
                company_id=current_user.company_id,
                visible=True
            )
            
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400
            
            # Update fields
            if 'type' in data:
                valid_types = ['speed_limit', 'geofence', 'ignition', 'low_battery', 'offline', 'panic_button']
                if data['type'] not in valid_types:
                    return {'message': f'Tipo inválido. Use: {", ".join(valid_types)}'}, 400
                alert.type = data['type']
            
            if 'condition' in data:
                alert.condition = data['condition']
            
            if 'actions' in data:
                alert.actions = data['actions']
            
            if 'recipients' in data:
                alert.recipients = data['recipients']
            
            if 'active' in data:
                alert.active = data['active']
            
            alert.updated_by = current_user
            alert.save()
            
            logger.info(f"Alert updated: {alert.id}")
            
            return alert.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Alerta não encontrado'}, 404
        except ValidationError as e:
            logger.error(f"Validation error updating alert: {str(e)}")
            return {'message': f'Erro de validação: {str(e)}'}, 400
        except Exception as e:
            logger.error(f"Error updating alert: {str(e)}")
            return {'message': 'Erro ao atualizar alerta'}, 500
    
    @api.doc('delete_alert')
    @token_required
    @require_permission('vehicle', 'delete')
    def delete(self, current_user, id):
        """Deleta alerta (soft delete)"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do alerta inválido'}, 400
            
            alert = Alert.objects.get(
                id=id,
                company_id=current_user.company_id,
                visible=True
            )
            
            alert.visible = False
            alert.active = False
            alert.updated_by = current_user
            alert.save()
            
            logger.info(f"Alert deleted: {alert.id}")
            
            return {'message': 'Alerta deletado com sucesso'}, 200
            
        except DoesNotExist:
            return {'message': 'Alerta não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error deleting alert: {str(e)}")
            return {'message': 'Erro ao deletar alerta'}, 500


@api.route('/<id>/toggle')
@api.param('id', 'Alert identifier')
class AlertToggle(Resource):
    
    @api.doc('toggle_alert')
    @token_required
    @require_permission('vehicle', 'update')
    def post(self, current_user, id):
        """Ativa/desativa alerta"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do alerta inválido'}, 400
            
            alert = Alert.objects.get(
                id=id,
                company_id=current_user.company_id,
                visible=True
            )
            
            alert.active = not alert.active
            alert.updated_by = current_user
            alert.save()
            
            status = 'ativado' if alert.active else 'desativado'
            logger.info(f"Alert {status}: {alert.id}")
            
            return {
                'message': f'Alerta {status} com sucesso',
                'active': alert.active
            }, 200
            
        except DoesNotExist:
            return {'message': 'Alerta não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error toggling alert: {str(e)}")
            return {'message': 'Erro ao alterar status do alerta'}, 500
