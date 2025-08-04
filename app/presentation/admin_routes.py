
from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import User, Company
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging
from bson.objectid import ObjectId
from bson.errors import InvalidId
import re

logger = logging.getLogger(__name__)

api = Namespace('admins', description='Admin user operations')

permission_details = api.model('PermissionDetails', {
    'id': fields.String(readonly=True),
    'name': fields.String(readonly=True),
    'description': fields.String(readonly=True),
    'resource_type': fields.String(readonly=True),
    'action_type': fields.String(readonly=True)
})

admin_model = api.model('Admin', {
    'id': fields.String(readonly=True, description='Admin unique identifier'),
    'name': fields.String(required=True, description='Admin full name'),
    'matricula': fields.String(description='Admin matricula (unique)'),
    'email': fields.String(required=True, description='Admin email address (unique)'),
    'cpf': fields.String(required=True, description='Admin CPF (unique, 11 digits)'),
    'phone': fields.String(description='Admin phone number'),
    'password': fields.String(required=True, description='Admin password (required for creation)'),
    'role': fields.String(readonly=True, default='admin', description='Admin role'),
    'status': fields.String(required=True, description='Admin status', enum=['active', 'inactive'], default='active'),
    'permissions': fields.List(fields.Nested(permission_details), description='Admin permissions'),
    'company_id': fields.String(required=True, description='Company ID admin belongs to'),
    'created_at': fields.DateTime(readonly=True, description='Creation timestamp'),
    'created_by': fields.String(readonly=True, description='User ID who created this admin'),
    'updated_at': fields.DateTime(readonly=True, description='Last update timestamp'),
    'updated_by': fields.String(readonly=True, description='User ID who last updated this admin')
})

pagination_model = api.model('PaginatedAdmins', {
    'admins': fields.List(fields.Nested(admin_model), description='List of admins'),
    'total': fields.Integer(description='Total number of admins'),
    'page': fields.Integer(description='Current page number'),
    'per_page': fields.Integer(description='Number of items per page'),
    'total_pages': fields.Integer(description='Total number of pages')
})

status_toggle_model = api.model(
    'StatusToggle', {
        'status':
        fields.String(required=True,
                      description='New status value',
                      enum=['active', 'inactive'])
    })

user_model = api.model(
    'User', {
        'id':
        fields.String(readonly=True, description='User unique identifier'),
        'name':
        fields.String(required=True, description='User full name'),
        'matricula':
        fields.String(description='User matricula (unique)'),
        'email':
        fields.String(required=True,
                      description='User email address (unique)'),
        'cpf':
        fields.String(required=True,
                      description='User CPF (unique, 11 digits)'),
        'phone':
        fields.String(description='User phone number'),
        'password':
        fields.String(required=True,
                      description='User password (required for creation)'),
        'role':
        fields.String(
            required=True, description='User role', enum=['admin', 'user']),
        'status':
        fields.String(required=True,
                      description='User status',
                      enum=['active', 'inactive'],
                      default='active'),
        'permissions':
        fields.List(fields.Nested(permission_details),
                    description='User permissions'),
        'company_id':
        fields.String(required=True, description='Company ID user belongs to'),
        'created_at':
        fields.DateTime(readonly=True, description='Creation timestamp'),
        'created_by':
        fields.String(readonly=True,
                      description='User ID who created this user'),
        'updated_at':
        fields.DateTime(readonly=True, description='Last update timestamp'),
        'updated_by':
        fields.String(readonly=True,
                      description='User ID who last updated this user')
    })


@api.route('')
class AdminList(Resource):
    @api.doc('list_admins',
             params={
                 'page': {'type': 'integer', 'default': 1, 'description': 'Page number'},
                 'per_page': {'type': 'integer', 'default': 10, 'description': 'Items per page'},
                 'company_id': {'type': 'string', 'required': True, 'description': 'Company ID (required)'},
                 'email': {'type': 'string', 'description': 'Filter by email (case-insensitive)'},
                 'cpf': {'type': 'string', 'description': 'Filter by CPF'}
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('admin', 'read')
    def get(self, current_user):
        """List admin users with pagination and filtering"""
        try:
            company_id = request.args.get('company_id')
            if not company_id:
                return {'message': 'Parâmetro company_id é obrigatório'}, 400

            if not ObjectId.is_valid(company_id):
                return {'message': 'ID da empresa inválido'}, 400

            try:
                company = Company.objects.get(id=company_id)
            except DoesNotExist:
                return {'message': 'Empresa não encontrada'}, 404

            if current_user.role != 'admin':
                return {'message': 'Não autorizado a listar administradores'}, 403

            try:
                page = max(1, int(request.args.get('page', 1)))
                per_page = max(1, min(100, int(request.args.get('per_page', 10))))
            except ValueError:
                return {'message': 'Parâmetros de paginação inválidos'}, 400

            query = {'company_id': company.id, 'role': 'admin'}

            email = request.args.get('email')
            if email:
                query['email'] = {'$regex': f'^{re.escape(email)}$', '$options': 'i'}

            cpf = request.args.get('cpf')
            if cpf:
                cpf = re.sub(r'\D', '', cpf)
                if len(cpf) != 11:
                    return {'message': 'CPF inválido'}, 400
                query['cpf'] = cpf

            total = User.objects(**query).count()
            total_pages = (total + per_page - 1) // per_page
            admins = User.objects(**query).order_by('name').skip((page - 1) * per_page).limit(per_page)

            return {
                'admins': [admin.to_dict() for admin in admins],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200

        except Exception as e:
            logger.error(f"Error listing admins: {str(e)}")
            return {'message': 'Erro ao listar administradores'}, 500

    @api.doc('create_admin')
    @api.expect(admin_model)
    @token_required
    @require_permission('admin', 'write')
    def post(self, current_user):
        """Create a new admin user"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem criar outros administradores'}, 403

            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            required_fields = ['name', 'email', 'cpf', 'password', 'company_id']
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400

            if not ObjectId.is_valid(data['company_id']):
                return {'message': 'ID da empresa inválido'}, 400

            try:
                company = Company.objects.get(id=data['company_id'])
            except DoesNotExist:
                return {'message': 'Empresa não encontrada'}, 404

            cpf = re.sub(r'\D', '', data['cpf'])
            if len(cpf) != 11:
                return {'message': 'CPF inválido'}, 400

            try:
                admin = User(
                    name=data['name'],
                    email=data['email'].lower(),
                    cpf=cpf,
                    phone=data.get('phone'),
                    role='admin',
                    company_id=company,
                    created_by=current_user,
                    updated_by=current_user
                )
                admin.set_password(data['password'])
                admin.save()
                return admin.to_dict(), 201

            except NotUniqueError as e:
                if 'email' in str(e):
                    return {'message': 'Email já cadastrado'}, 409
                if 'cpf' in str(e):
                    return {'message': 'CPF já cadastrado'}, 409
                return {'message': 'Erro de unicidade'}, 409

        except Exception as e:
            logger.error(f"Error creating admin: {str(e)}")
            return {'message': 'Erro ao criar administrador'}, 500

@api.route('/<id>')
@api.param('id', 'Admin identifier')
class AdminResource(Resource):
    @api.doc('get_admin')
    @api.marshal_with(admin_model)
    @token_required
    @require_permission('admin', 'read')
    def get(self, current_user, id):
        """Get a specific admin by ID"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Não autorizado a acessar dados de administradores'}, 403

            if not ObjectId.is_valid(id):
                return {'message': 'ID do administrador inválido'}, 400

            admin = User.objects.get(id=id, role='admin')
            return admin.to_dict(), 200

        except DoesNotExist:
            return {'message': 'Administrador não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting admin: {str(e)}")
            return {'message': 'Erro ao buscar administrador'}, 500

    @api.doc('update_admin')
    @api.expect(admin_model)
    @token_required
    @require_permission('admin', 'update')
    def put(self, current_user, id):
        """Update an admin"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Não autorizado a atualizar administradores'}, 403

            if not ObjectId.is_valid(id):
                return {'message': 'ID do administrador inválido'}, 400

            admin = User.objects.get(id=id, role='admin')
            data = request.get_json()
            
            if 'name' in data:
                admin.name = data['name']

            if 'email' in data:
                existing = User.objects(email=data['email'].lower(), id__ne=id).first()
                if existing:
                    return {'message': 'Email já está em uso'}, 409
                admin.email = data['email'].lower()

            if 'phone' in data:
                admin.phone = data['phone']

            if 'password' in data and data['password']:
                admin.set_password(data['password'])

            admin.updated_by = current_user
            admin.save()
            return admin.to_dict(), 200

        except DoesNotExist:
            return {'message': 'Administrador não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error updating admin: {str(e)}")
            return {'message': 'Erro ao atualizar administrador'}, 500

    @api.doc('delete_admin')
    @token_required
    @require_permission('admin', 'delete')
    def delete(self, current_user, id):
        """Delete an admin"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Não autorizado a deletar administradores'}, 403

            if not ObjectId.is_valid(id):
                return {'message': 'ID do administrador inválido'}, 400

            admin = User.objects.get(id=id, role='admin')
            admin.delete()
            return '', 204

        except DoesNotExist:
            return {'message': 'Administrador não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error deleting admin: {str(e)}")
            return {'message': 'Erro ao deletar administrador'}, 500

@api.route('/<id>/status')
@api.param('id', 'User identifier')
class UserStatusToggle(Resource):

    @api.doc('toggle_user_status',
             responses={
                 200: ('Success', user_model),
                 400: 'Dados inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Usuário não encontrado',
                 500: 'Erro interno do servidor'
             })
    @api.expect(status_toggle_model)
    @token_required
    @require_permission('user', 'update')
    def post(self, current_user, id):
        """
        Toggle user status.

        Changes user status between active and inactive.
        Users can only change status of users from their own company unless they are admins.
        """
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do usuário inválido'}, 400

            user = User.objects.get(id=id, role='admin')

            if current_user.role != 'admin' and str(
                    current_user.company_id.id) != str(user.company_id.id):
                return {
                    'message': 'Não autorizado a alterar status deste usuário'
                }, 403

            data = request.get_json()
            if not data or 'status' not in data:
                return {'message': 'Status não fornecido'}, 400

            if data['status'] not in ['active', 'inactive']:
                return {'message': 'Status inválido'}, 400

            user.status = data['status']
            user.updated_by = current_user
            user.save()

            return user.to_dict(), 200

        except DoesNotExist:
            return {'message': 'Usuário não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error toggling user status: {str(e)}")
            return {'message': 'Erro ao alterar status do usuário'}, 500
