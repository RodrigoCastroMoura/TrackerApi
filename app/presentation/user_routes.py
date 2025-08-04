from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import User
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging
from bson.objectid import ObjectId
from bson.errors import InvalidId
import re

logger = logging.getLogger(__name__)

api = Namespace('users', description='User operations')

# Request/Response Models
permission_details = api.model(
    'PermissionDetails', {
        'id': fields.String(readonly=True),
        'name': fields.String(readonly=True),
        'description': fields.String(readonly=True),
        'resource_type': fields.String(readonly=True),
        'action_type': fields.String(readonly=True)
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
        'signature':
        fields.String(description='User signature URL'),
        'rubric':
        fields.String(description='User rubric URL'),
         'signatureDoc':
        fields.String(description='User signatureDoc URL'),
        'rubricDoc':
        fields.String(description='User rubricDoc URL'),
        'type_font':
        fields.String(description='User signature font'),
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

user_update_model = api.model(
    'UserUpdate', {
        'name':
        fields.String(description='User full name'),
        'matricula':
        fields.String(description='User matricula (unique)'),
        'email':
        fields.String(description='User email address (unique)'),
        'phone':
        fields.String(description='User phone number'),
        'password':
        fields.String(description='User password (optional for updates)'),
        'role':
        fields.String(description='User role', enum=['admin', 'user'])
    })

status_toggle_model = api.model(
    'StatusToggle', {
        'status':
        fields.String(required=True,
                      description='New status value',
                      enum=['active', 'inactive'])
    })

pagination_model = api.model(
    'PaginatedUsers', {
        'users':
        fields.List(fields.Nested(user_model), description='List of users'),
        'total':
        fields.Integer(description='Total number of users'),
        'page':
        fields.Integer(description='Current page number'),
        'per_page':
        fields.Integer(description='Number of items per page'),
        'total_pages':
        fields.Integer(description='Total number of pages')
    })


@api.route('')
class UserList(Resource):

    @api.doc('list_users',
             params={
                 'page': {
                     'type': 'integer',
                     'default': 1,
                     'description': 'Page number'
                 },
                 'per_page': {
                     'type': 'integer',
                     'default': 10,
                     'description': 'Items per page'
                 },
                 'email': {
                     'type': 'string',
                     'description': 'Filter by email (case-insensitive)'
                 },
                 'document': {
                     'type': 'string',
                     'description': 'Filter by Document'
                 }
             },
             responses={
                 200: ('Success', pagination_model),
                 400: 'Parâmetros inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Empresa não encontrada',
                 500: 'Erro interno do servidor'
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('user', 'read')
    def get(self, current_user):
        """
        List users with pagination and filtering.

        Returns a paginated list of users for a specific company. Company ID is required.
        Admin users can see users from any company, while regular users can only see users from their own company.
        """
        try:
           
            try:
                page = max(1, int(request.args.get('page', 1)))
                per_page = max(1,
                               min(100, int(request.args.get('per_page', 10))))
            except ValueError:
                logger.warning("Invalid pagination parameters provided")
                return {'message': 'Parâmetros de paginação inválidos'}, 400

            query = { 'role': 'user', 'visible': True}

            email = request.args.get('email')
            if email:
                query['email'] = {
                    '$regex': f'^{re.escape(email)}$',
                    '$options': 'i'
                }

            cpf = request.args.get('cpf')
            if cpf:
                cpf = re.sub(r'\D', '', cpf)
                if len(cpf) != 11:
                    return {'message': 'CPF inválido'}, 400
                query['cpf'] = cpf

            matricula = request.args.get('matricula')
            if matricula:
                query['matricula'] = {
                    '$regex': f'^{re.escape(matricula)}$',
                    '$options': 'i'
                }

            total = User.objects(**query).count()
            total_pages = (total + per_page - 1) // per_page
            users = User.objects(**query).order_by('name').skip(
                (page - 1) * per_page).limit(per_page)

            return {
                'users': [user.to_dict() for user in users],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200

        except Exception as e:
            logger.error(f"Database error while fetching users: {str(e)}")
            return {'message': 'Erro ao buscar usuários'}, 500

    @api.doc('create_user',
             responses={
                 201: ('User created', user_model),
                 400: 'Dados inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Empresa não encontrada',
                 409: 'Email ou CPF já cadastrado',
                 500: 'Erro interno do servidor'
             })
    @api.expect(user_model)
    @token_required
    @require_permission('user', 'write')
    def post(self, current_user):
        """
        Create a new user.

        Creates a new user in the system. Requires proper authorization and valid input data.
        Only admin users can create other admin users. Regular users can only create users
        in their own company with 'user' role.
        """
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            required_fields = [
                'name', 'email', 'cpf', 'password', 'role'
            ]
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400

            

           
            # Verify company access and role permissions
            if current_user.role != 'admin':
                if data['role'] == 'admin':
                    return {
                        'message':
                        'Apenas administradores podem criar outros administradores'
                    }, 403

            # Validate CPF format
            cpf = re.sub(r'\D', '', data['cpf'])
            if len(cpf) != 11:
                return {'message': 'CPF inválido'}, 400

            # Create user
            try:
                user = User(name=data['name'],
                            matricula=data['matricula'],
                            email=data['email'].lower(),
                            cpf=cpf,
                            phone=data.get('phone'),
                            role='user',
                            created_by=current_user,
                            updated_by=current_user)
                user.set_password(data['password'])
                user.save()
                return user.to_dict(), 201

            except NotUniqueError as e:
                if 'email' in str(e):
                    return {'message': 'Email já cadastrado'}, 409
                if 'cpf' in str(e):
                    return {'message': 'CPF já cadastrado'}, 409
                if 'matricula' in str(e):
                    return {'message': 'Matrícula já cadastrada'}, 409
                return {'message': 'Erro de unicidade'}, 409

            except ValidationError as e:
                return {'message': str(e)}, 400

        except Exception as e:
            logger.error(f"Error creating user: {str(e)}")
            return {'message': 'Erro ao criar usuário'}, 500


@api.route('/<id>')
@api.param('id', 'User identifier')
class UserResource(Resource):

    @api.doc('get_user',
             responses={
                 200: ('Success', user_model),
                 400: 'ID inválido',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Usuário não encontrado',
                 500: 'Erro interno do servidor'
             })
    @api.marshal_with(user_model)
    @token_required
    #@require_permission('user', 'read')
    def get(self, current_user, id):
        """
        Get a specific user by ID.

        Retrieves detailed information about a specific user.
        Users can only access users from their own company unless they are admins.
        """
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do usuário inválido'}, 400

            user = User.objects.get(id=id, role='user')

            if current_user.role != 'admin' and str(
                    current_user.company_id.id) != str(user.company_id.id):
                return {
                    'message': 'Não autorizado a acessar este usuário'
                }, 403

            return user.to_dict(), 200

        except DoesNotExist:
            return {'message': 'Usuário não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting user: {str(e)}")
            return {'message': 'Erro ao buscar usuário'}, 500

    @api.doc('update_user',
             responses={
                 200: ('Success', user_model),
                 400: 'Dados inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Usuário não encontrado',
                 409: 'Email já cadastrado',
                 500: 'Erro interno do servidor'
             })
    @api.expect(user_update_model)
    @token_required
    @require_permission('user', 'update')
    def put(self, current_user, id):
        """
        Update a user.

        Updates user information. Users can only update users from their own company unless they are admins.
        Regular users cannot change roles or promote others to admin.
        """
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do usuário inválido'}, 400

            user = User.objects.get(id=id, role='user')

            if current_user.role != 'admin' and str(
                    current_user.company_id.id) != str(user.company_id.id):
                return {
                    'message': 'Não autorizado a atualizar este usuário'
                }, 403

            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            if 'name' in data:
                user.name = data['name']

            if 'matricula' in data:
                # Check if matricula already exists for another user
                existing_user = User.objects(matricula=data['matricula'],
                                             id__ne=id).first()
                if existing_user:
                    return {
                        'message': 'Matrícula já está em uso por outro usuário'
                    }, 409
                user.matricula = data['matricula']

            if 'email' in data:
                # Check if email already exists for another user
                existing_user = User.objects(email=data['email'].lower(),
                                             id__ne=id).first()
                if existing_user:
                    return {
                        'message': 'Email já está em uso por outro usuário'
                    }, 409
                user.email = data['email'].lower()

            if 'phone' in data:
                user.phone = data['phone']

            if 'password' in data and data['password']:
                user.set_password(data['password'])

            user.role = 'user'

            user.updated_by = current_user

            try:
                user.save()
                return user.to_dict(), 200
            except NotUniqueError:
                return {'message': 'Email já cadastrado'}, 409
            except ValidationError as e:
                return {'message': str(e)}, 400

        except DoesNotExist:
            return {'message': 'Usuário não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error updating user: {str(e)}")
            return {'message': 'Erro ao atualizar usuário'}, 500

    @api.doc('delete_user',
             responses={
                 204: 'Usuário deletado com sucesso',
                 400: 'ID inválido',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Usuário não encontrado',
                 500: 'Erro interno do servidor'
             })
    @token_required
    @require_permission('user', 'delete')
    def delete(self, current_user, id):
        """
        Delete a user.

        Permanently removes a user from the system.
        Users can only delete users from their own company unless they are admins.
        """
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do usuário inválido'}, 400

            user = User.objects.get(id=id, role='user')

            if current_user.role != 'admin' and str(
                    current_user.company_id.id) != str(user.company_id.id):
                return {
                    'message': 'Não autorizado a deletar este usuário'
                }, 403

            user.visible = False
            user.status = 'inactive'
            user.updated_by = current_user
            user.save()
            return {'message': 'Usuário marcado como excluído'}, 200

        except DoesNotExist:
            return {'message': 'Usuário não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error deleting user: {str(e)}")
            return {'message': 'Erro ao deletar usuário'}, 500


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

            user = User.objects.get(id=id, role='user')

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


@api.route('/search')
class UserSearch(Resource):

    @api.doc('search_users',
             params={
                 'q': {
                     'type': 'string',
                     'required': True,
                     'description': 'Search term (can be CPF, matricula, or email)'
                 },
                 'page': {
                     'type': 'integer',
                     'default': 1,
                     'description': 'Page number'
                 },
                 'per_page': {
                     'type': 'integer',
                     'default': 10,
                     'description': 'Items per page'
                 }
             },
             responses={
                 200: ('Success', pagination_model),
                 400: 'Parâmetros inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 500: 'Erro interno do servidor'
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('user', 'read')
    def get(self, current_user):
        """
        Search users by CPF, matricula, or email.

        Searches for users using a single parameter that can match CPF, matricula, or email.
        Returns a paginated list of matching users.
        """
        try:
            search_term = request.args.get('q')
            if not search_term:
                logger.warning("Missing required search parameter 'q'")
                return {'message': 'Parâmetro de busca é obrigatório'}, 400

            try:
                page = max(1, int(request.args.get('page', 1)))
                per_page = max(1, min(100, int(request.args.get('per_page', 10))))
            except ValueError:
                logger.warning("Invalid pagination parameters provided")
                return {'message': 'Parâmetros de paginação inválidos'}, 400

            # Build search query - search in CPF, matricula, and email
            search_conditions = []
            
            # Search by email (partial match, case-insensitive)
            search_conditions.append({
                'email': {
                    '$regex': re.escape(search_term),
                    '$options': 'i'
                }
            })
            
            # Search by CPF (remove non-digits for comparison)
            cpf_cleaned = re.sub(r'\D', '', search_term)
            if cpf_cleaned:
                search_conditions.append({'cpf': cpf_cleaned})
            
            # Search by matricula (case-insensitive, only if matricula field exists and has value)
            search_conditions.append({
                '$and': [
                    {'matricula': {'$exists': True}},  # Field exists in document
                    {'matricula': {'$ne': None}},      # Field is not null
                    {'matricula': {'$ne': ''}},        # Field is not empty string
                    {
                        'matricula': {
                            '$regex': re.escape(search_term),
                            '$options': 'i'
                        }
                    }
                ]
            })

            # Base query filters
            base_filters = {
                'visible': True,
                'role': 'user'  # Only search users, not admins
            }

            # If user is not admin, restrict to their company
            if current_user.role != 'admin':
                base_filters['company_id'] = current_user.company_id.id

            # Build the complete query using __raw__ for complex MongoDB queries
            query = User.objects(**base_filters).filter(__raw__={'$or': search_conditions})

            total = query.count()
            total_pages = (total + per_page - 1) // per_page
            users = query.order_by('name').skip((page - 1) * per_page).limit(per_page)

            return {
                'users': [user.to_dict() for user in users],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200

        except Exception as e:
            logger.error(f"Database error while searching users: {str(e)}")
            return {'message': 'Erro ao buscar usuários'}, 500


@api.route('/<id>/signature')
@api.param('id', 'User identifier')
class UserSignature(Resource):
    signature_model = api.model(
        'Signature', {
            'signature':
            fields.String(required=True, description='User signature URL')
        })

    @api.doc('update_user_signature',
             responses={
                 200: 'Success',
                 400: 'Dados inválidos',
                 401: 'Não autenticado',
                 403: 'Não autorizado',
                 404: 'Usuário não encontrado',
                 500: 'Erro interno do servidor'
             })
    @api.expect(signature_model)
    @token_required
    def post(self, current_user, id):
        """Update user signature."""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do usuário inválido'}, 400

            user = User.objects.get(id=id)

            # Only allow users to update their own signature or admins
            if str(current_user.id) != str(
                    id) and current_user.role != 'admin':
                return {'message': 'Não autorizado'}, 403

            data = request.get_json()
            if not data or 'signature' not in data:
                return {'message': 'URL da assinatura não fornecida'}, 400
            
            if not data or 'rubric' not in data:
                return {'message': 'URL da rubrica não fornecida'}, 400
            
            if not data or 'signatureDoc' not in data:
                return {'message': 'URL da assinaturaDoc não fornecida'}, 400
            
            if not data or 'rubricDoc' not in data:
                return {'message': 'URL da rubricaDoc não fornecida'}, 400

            
            if not data or 'type_font' not in data:
                return {'message': 'Font não fornecida'}, 400

            user.signature = data['signature']
            user.rubric = data['rubric']
            user.signatureDoc = data['signatureDoc']
            user.rubricDoc = data['rubricDoc']
            user.type_font = data['type_font']
            user.save()

            return {'message': 'Assinatura atualizada com sucesso'}, 200

        except DoesNotExist:
            return {'message': 'Usuário não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error updating user signature: {str(e)}")
            return {'message': 'Erro ao atualizar assinatura'}, 500
