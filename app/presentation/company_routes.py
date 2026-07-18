from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Company
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging
from bson.objectid import ObjectId
import re

logger = logging.getLogger(__name__)

api = Namespace('companies', description='Company operations')

# Funções auxiliares de validação
def validate_email(email):
    """Valida formato de email"""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_pattern, email) is not None

def validate_cnpj(cnpj):
    """Valida e limpa CNPJ"""
    cnpj_clean = re.sub(r'\D', '', cnpj)
    return cnpj_clean if len(cnpj_clean) == 14 else None

# Company Model for Swagger
company_model = api.model('Company', {
    'id': fields.String(readonly=True, description='Company unique identifier'),
    'name': fields.String(required=True, description='Nome da empresa'),
    'cnpj': fields.String(description='CNPJ da empresa (14 dígitos)'),
    'email': fields.String(description='Email da empresa'),
    'phone': fields.String(description='Telefone da empresa'),
    'status': fields.String(readonly=True, description='Status da empresa (gerado automaticamente como active)'),
    'created_at': fields.DateTime(readonly=True),
    'updated_at': fields.DateTime(readonly=True)
})

company_update_model = api.model('CompanyUpdate', {
    'name': fields.String(description='Nome da empresa'),
    'cnpj': fields.String(description='CNPJ da empresa (14 dígitos)'),
    'email': fields.String(description='Email da empresa'),
    'phone': fields.String(description='Telefone da empresa'),
    'status': fields.String(description='Status da empresa', enum=['active', 'inactive'])
})

pagination_model = api.model('PaginatedCompanies', {
    'companies': fields.List(fields.Nested(company_model)),
    'total': fields.Integer(description='Total de empresas'),
    'page': fields.Integer(description='Página atual'),
    'per_page': fields.Integer(description='Itens por página'),
    'total_pages': fields.Integer(description='Total de páginas')
})

@api.route('')
class CompanyList(Resource):

    @api.doc('list_companies',
             params={
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 10},
                 'name': {'type': 'string', 'description': 'Filtrar por nome (parcial)'},
                 'cnpj': {'type': 'string', 'description': 'Filtrar por CNPJ'},
                 'email': {'type': 'string', 'description': 'Filtrar por email'},
                 'status': {'type': 'string', 'enum': ['active', 'inactive']}
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('company', 'read')
    def get(self, current_user):
        """Listar empresas com paginação e filtros"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem listar empresas'}, 403

            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 10))))

            query = {'visible': True}

            if request.args.get('name'):
                query['name'] = {'$regex': request.args.get('name'), '$options': 'i'}

            if request.args.get('cnpj'):
                cnpj = validate_cnpj(request.args.get('cnpj'))
                if cnpj:
                    query['cnpj'] = cnpj

            if request.args.get('email'):
                query['email'] = {'$regex': request.args.get('email'), '$options': 'i'}

            if request.args.get('status'):
                query['status'] = request.args.get('status')

            total = Company.objects(**query).count()
            total_pages = (total + per_page - 1) // per_page
            companies = Company.objects(**query).order_by('name').skip(
                (page - 1) * per_page).limit(per_page)

            return {
                'companies': [c.to_dict() for c in companies],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200

        except Exception as e:
            logger.error(f"Error listing companies: {str(e)}")
            return {'message': 'Erro ao listar empresas'}, 500

    @api.doc('create_company')
    @api.expect(company_model)
    @token_required
    @require_permission('company', 'write')
    def post(self, current_user):
        """Criar nova empresa"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem criar empresas'}, 403

            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            if 'name' not in data or not data['name']:
                return {'message': 'Campo name é obrigatório'}, 400

            cnpj = None
            if data.get('cnpj'):
                cnpj = validate_cnpj(data['cnpj'])
                if not cnpj:
                    return {'message': 'CNPJ inválido - deve ter 14 dígitos'}, 400

                existing_cnpj = Company.objects(cnpj=cnpj).first()
                if existing_cnpj:
                    return {'message': f'CNPJ {data["cnpj"]} já está cadastrado'}, 409

            if data.get('email') and not validate_email(data['email']):
                return {'message': 'Formato de email inválido'}, 400

            try:
                company = Company(
                    name=data['name'],
                    cnpj=cnpj,
                    email=data.get('email'),
                    phone=data.get('phone'),
                    status='active',
                    created_by=current_user,
                    updated_by=current_user
                )
                company.save()

                logger.info(f"Empresa criada com sucesso: {company.name}")
                return company.to_dict(), 201

            except NotUniqueError as e:
                logger.error(f"NotUniqueError creating company: {str(e)}")
                return {'message': f'CNPJ {data.get("cnpj")} já está cadastrado'}, 409

        except Exception as e:
            logger.error(f"Error creating company: {str(e)}")
            return {'message': 'Erro ao criar empresa'}, 500


@api.route('/<id>')
@api.param('id', 'Company identifier')
class CompanyResource(Resource):

    @api.doc('get_company')
    @api.marshal_with(company_model)
    @token_required
    @require_permission('company', 'read')
    def get(self, current_user, id):
        """Obter empresa específica"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem visualizar empresas'}, 403

            if not ObjectId.is_valid(id):
                return {'message': 'ID da empresa inválido'}, 400

            company = Company.objects.get(id=id, visible=True)
            return company.to_dict(), 200

        except DoesNotExist:
            return {'message': 'Empresa não encontrada'}, 404
        except Exception as e:
            logger.error(f"Error getting company: {str(e)}")
            return {'message': 'Erro ao buscar empresa'}, 500

    @api.doc('update_company')
    @api.expect(company_update_model)
    @token_required
    @require_permission('company', 'update')
    def put(self, current_user, id):
        """Atualizar empresa"""
        try:
            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem atualizar empresas'}, 403

            if not ObjectId.is_valid(id):
                return {'message': 'ID da empresa inválido'}, 400

            company = Company.objects.get(id=id, visible=True)
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            if 'name' in data:
                if not data['name']:
                    return {'message': 'Nome não pode ser vazio'}, 400
                company.name = data['name']

            if 'cnpj' in data:
                if data['cnpj']:
                    cnpj = validate_cnpj(data['cnpj'])
                    if not cnpj:
                        return {'message': 'CNPJ inválido - deve ter 14 dígitos'}, 400
                    existing = Company.objects(cnpj=cnpj, id__ne=id).first()
                    if existing:
                        return {'message': 'CNPJ já está em uso'}, 409
                    company.cnpj = cnpj
                else:
                    company.cnpj = None

            if 'email' in data:
                if data['email'] and not validate_email(data['email']):
                    return {'message': 'Formato de email inválido'}, 400
                company.email = data['email']

            if 'phone' in data:
                company.phone = data['phone']

            if 'status' in data:
                if data['status'] not in ['active', 'inactive']:
                    return {'message': 'Status inválido'}, 400
                company.status = data['status']

            company.updated_by = current_user

            try:
                company.save()
                return company.to_dict(), 200
            except NotUniqueError:
                return {'message': 'CNPJ já está em uso'}, 409
            except ValidationError as e:
                return {'message': str(e)}, 400

        except DoesNotExist:
            return {'message': 'Empresa não encontrada'}, 404
        except Exception as e:
            logger.error(f"Error updating company: {str(e)}")
            return {'message': 'Erro ao atualizar empresa'}, 500

    @api.doc('delete_company')
    @token_required
    @require_permission('company', 'delete')
    def delete(self, current_user, id):
        """Deletar empresa (soft delete)"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID da empresa inválido'}, 400

            if current_user.role != 'admin':
                return {'message': 'Apenas administradores podem deletar empresas'}, 403

            company = Company.objects.get(id=id, visible=True)
            company.visible = False
            company.status = 'inactive'
            company.updated_by = current_user
            company.save()

            return {'message': 'Empresa deletada com sucesso'}, 200

        except DoesNotExist:
            return {'message': 'Empresa não encontrada'}, 404
        except Exception as e:
            logger.error(f"Error deleting company: {str(e)}")
            return {'message': 'Erro ao deletar empresa'}, 500
