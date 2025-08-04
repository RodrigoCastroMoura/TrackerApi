from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Permission, User
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging

logger = logging.getLogger(__name__)

api = Namespace('permissions', description='Permission operations')

permission_model = api.model('Permission', {
    'id': fields.String(readonly=True),
    'name': fields.String(required=True, description='Permission name'),
    'description': fields.String(required=True, description='Permission description')
})

permission_request = api.model('PermissionRequest', {
    'permissions': fields.List(fields.String, required=True, description='Lista de IDs das permissões')
})

@api.route('')
class PermissionList(Resource):
    @api.doc('list_permissions',
             description='Lista todas as permissões disponíveis no sistema. Acesso restrito a administradores com permissão de leitura.',
             responses={
                 200: 'Success',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 500: 'Erro interno do servidor'
             })
    @api.marshal_list_with(permission_model)
    @token_required
    @require_permission('user', 'read')
    def get(self, current_user):
        """List all permissions"""
        try:
            if not current_user.role == 'admin':
                return {'message': 'Apenas administradores podem listar permissões'}, 403

            permissions = Permission.objects.all()
            return [p.to_dict() for p in permissions]
        except Exception as e:
            logger.error(f"Error listing permissions: {str(e)}")
            return {'message': 'Erro ao listar permissões'}, 500

    @api.hide
    @token_required
    def post(self, current_user):
        """Create a new permission"""
        try:
            if not current_user.role == 'admin':
                return {'message': 'Apenas administradores podem criar permissões'}, 403

            if not request.is_json:
                return {'message': 'Requisição deve ser no formato JSON'}, 400

            data = request.json
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            if 'name' not in data or not data['name'].strip():
                return {'message': 'Nome da permissão é obrigatório'}, 400

            if 'description' not in data or not data['description'].strip():
                return {'message': 'Descrição da permissão é obrigatória'}, 400

            try:
                permission = Permission(
                    name=data['name'].strip(),
                    description=data['description'].strip()
                )
                permission.save()
                return permission.to_dict(), 201
            except NotUniqueError:
                return {'message': 'Permissão já existe'}, 409
            except ValidationError as e:
                return {'message': str(e)}, 400

        except Exception as e:
            logger.error(f"Error creating permission: {str(e)}")
            return {'message': 'Erro ao criar permissão'}, 500

@api.route('/admin/<admin_id>/permissions')
class AdminPermissions(Resource):
    @api.doc('manage_admin_permissions',
             description='Gerencia permissões de administradores. Permite adicionar ou remover permissões.',
             params={'admin_id': 'ID do administrador'},
             body=permission_request,
             responses={
                 200: 'Success',
                 400: 'Dados inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Administrador não encontrado',
                 500: 'Erro interno do servidor'
             })
    @token_required
    @require_permission('user', 'update')
    def post(self, current_user, admin_id):
        """Manage admin permissions"""
        try:
            if not current_user.role == 'admin':
                return {'message': 'Apenas administradores podem gerenciar permissões'}, 403

            if not request.is_json:
                return {'message': 'Requisição deve ser no formato JSON'}, 400

            data = request.json
            if not data or 'permissions' not in data or not isinstance(data['permissions'], list):
                return {'message': 'Lista de permissões é obrigatória'}, 400

            try:
                admin = User.objects.get(id=admin_id)
                if admin.role != 'admin':
                    return {'message': 'Usuário não é um administrador'}, 403
            except DoesNotExist:
                return {'message': 'Administrador não encontrado'}, 404

            try:
                permissions = Permission.objects(id__in=data['permissions'])
                admin.permissions = list(permissions)
                admin.save()
                return admin.to_dict(), 200
            except ValidationError as e:
                return {'message': str(e)}, 400

        except Exception as e:
            logger.error(f"Error managing admin permissions: {str(e)}")
            return {'message': 'Erro ao gerenciar permissões do administrador'}, 500

@api.route('/users/<user_id>/permissions')
class UserPermissions(Resource):
    @api.hide
    @token_required
    def post(self, current_user, user_id):
        """Add or remove permissions for a user"""
        try:
            if not current_user.role == 'admin':
                return {'message': 'Apenas administradores podem gerenciar permissões'}, 403

            if not request.is_json:
                return {'message': 'Requisição deve ser no formato JSON'}, 400

            data = request.json
            if not data or 'permissions' not in data or not isinstance(data['permissions'], list):
                return {'message': 'Lista de permissões é obrigatória'}, 400

            try:
                user = User.objects.get(id=user_id)
            except DoesNotExist:
                return {'message': 'Usuário não encontrado'}, 404

            try:
                permissions = Permission.objects(id__in=data['permissions'])
                user.permissions = list(permissions)
                user.save()
                return user.to_dict(), 200
            except ValidationError as e:
                return {'message': str(e)}, 400

        except Exception as e:
            logger.error(f"Error managing user permissions: {str(e)}")
            return {'message': 'Erro ao gerenciar permissões do usuário'}, 500