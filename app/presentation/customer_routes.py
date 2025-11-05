from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer
from app.presentation.auth_routes import token_required, require_permission
from mongoengine.errors import NotUniqueError, ValidationError, DoesNotExist
import logging
from bson.objectid import ObjectId
from datetime import datetime
import re

logger = logging.getLogger(__name__)

api = Namespace('customers', description='Customer operations')

# Funções auxiliares de validação
def validate_email(email):
    """Valida formato de email"""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_pattern, email) is not None

def validate_cpf(cpf):
    """Valida e limpa CPF"""
    cpf_clean = re.sub(r'\D', '', cpf)
    return cpf_clean if len(cpf_clean) == 11 else None

def validate_date(date_str):
    """Valida formato de data DD/MM/AAAA"""
    date_pattern = r'^\d{2}/\d{2}/\d{4}$'
    return re.match(date_pattern, date_str) is not None

def validate_state(state):
    """Valida sigla de estado"""
    state_pattern = r'^[A-Z]{2}$'
    return re.match(state_pattern, state.upper()) is not None

def validate_cep(cep):
    """Valida e limpa CEP"""
    cep_clean = re.sub(r'\D', '', cep)
    return cep_clean if len(cep_clean) == 8 else None

# Customer Model for Swagger
customer_model = api.model('Customer', {
    'id': fields.String(readonly=True, description='Customer unique identifier'),
    'name': fields.String(required=True, description='Nome completo do cliente'),
    'email': fields.String(required=True, description='Email do cliente'),
    'document': fields.String(required=True, description='CPF do cliente (11 dígitos)'),
    'phone': fields.String(required=True, description='Telefone do cliente'),
    'password': fields.String(required=True, description='Senha do cliente (mínimo 6 caracteres)'),
    'street': fields.String(required=True, description='Rua/Logradouro'),
    'number': fields.String(required=True, description='Número'),
    'complement': fields.String(description='Complemento'),
    'district': fields.String(required=True, description='Bairro'),
    'city': fields.String(required=True, description='Cidade'),
    'state': fields.String(required=True, description='Estado (sigla)'),
    'postal_code': fields.String(required=True, description='CEP'),
    'card_brand': fields.String(readonly=True, description='Bandeira do cartão'),
    'card_last_digits': fields.String(readonly=True, description='Últimos 4 dígitos do cartão'),
    'status': fields.String(readonly=True, description='Status do cliente (gerado automaticamente como active)'),
    'created_at': fields.DateTime(readonly=True),
    'updated_at': fields.DateTime(readonly=True)
})

customer_update_model = api.model('CustomerUpdate', {
    'name': fields.String(description='Nome completo do cliente'),
    'email': fields.String(description='Email do cliente'),
    'phone': fields.String(description='Telefone do cliente'),
    'street': fields.String(description='Rua/Logradouro'),
    'number': fields.String(description='Número'),
    'complement': fields.String(description='Complemento'),
    'district': fields.String(description='Bairro'),
    'city': fields.String(description='Cidade'),
    'state': fields.String(description='Estado (sigla)'),
    'postal_code': fields.String(description='CEP'),
    'status': fields.String(description='Status do cliente', enum=['active', 'inactive'])
})

payment_card_model = api.model('PaymentCard', {
    'card_token': fields.String(required=True, description='Token do cartão no PagSeguro'),
    'card_brand': fields.String(required=True, description='Bandeira do cartão'),
    'card_last_digits': fields.String(required=True, description='Últimos 4 dígitos')
})

pagination_model = api.model('PaginatedCustomers', {
    'customers': fields.List(fields.Nested(customer_model)),
    'total': fields.Integer(description='Total de clientes'),
    'page': fields.Integer(description='Página atual'),
    'per_page': fields.Integer(description='Itens por página'),
    'total_pages': fields.Integer(description='Total de páginas')
})

@api.route('')
class CustomerList(Resource):
    
    @api.doc('list_customers',
             params={
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 10},
                 'email': {'type': 'string', 'description': 'Filtrar por email'},
                 'cpf': {'type': 'string', 'description': 'Filtrar por CPF'},
                 'name': {'type': 'string', 'description': 'Filtrar por nome (parcial)'},
                 'city': {'type': 'string', 'description': 'Filtrar por cidade'},
                 'state': {'type': 'string', 'description': 'Filtrar por estado'},
                 'status': {'type': 'string', 'enum': ['active', 'inactive']},
                 'auto_debit': {'type': 'boolean', 'description': 'Filtrar por débito automático'}
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user):
        """Listar clientes com paginação e filtros"""
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 10))))
            
            # Build query with multi-tenant isolation (admins can see all companies)
            query = {'visible': True}
            if current_user.role != 'admin':
                query['company_id'] = current_user.company_id
            
            # Filters
            if request.args.get('email'):
                query['email'] = {'$regex': request.args.get('email'), '$options': 'i'}
            
            if request.args.get('cpf'):
                cpf = validate_cpf(request.args.get('cpf'))
                if cpf:
                    query['cpf'] = cpf
            
            if request.args.get('name'):
                query['name'] = {'$regex': request.args.get('name'), '$options': 'i'}
            
            if request.args.get('city'):
                query['city'] = {'$regex': request.args.get('city'), '$options': 'i'}
            
            if request.args.get('state'):
                query['state'] = request.args.get('state').upper()
            
            if request.args.get('status'):
                query['status'] = request.args.get('status')
            
            if request.args.get('auto_debit') is not None:
                query['auto_debit'] = request.args.get('auto_debit').lower() == 'true'
            
            # Execute query
            total = Customer.objects(**query).count()
            total_pages = (total + per_page - 1) // per_page
            customers = Customer.objects(**query).order_by('name').skip(
                (page - 1) * per_page).limit(per_page)
            
            return {
                'customers': [c.to_dict() for c in customers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200
            
        except Exception as e:
            logger.error(f"Error listing customers: {str(e)}")
            return {'message': 'Erro ao listar clientes'}, 500
    
    @api.doc('create_customer')
    @api.expect(customer_model)
    @token_required
    @require_permission('customer', 'write')
    def post(self, current_user):
        """Criar novo cliente"""
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400
            
            # Validate required fields
            required_fields = ['name', 'email', 'document', 'phone', 'password',
                             'street', 'number', 'district', 'city', 'state', 'postal_code']
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400
            
            # Validate document (CPF)
            document = validate_cpf(data['document'])
            if not document:
                return {'message': 'CPF inválido - deve ter 11 dígitos'}, 400
            
            # Validate email format
            if not validate_email(data['email']):
                return {'message': 'Formato de email inválido'}, 400
            
            # Validate password
            password = data['password']
            if len(password) < 6:
                return {'message': 'A senha deve ter no mínimo 6 caracteres'}, 400
            
            # Validate state (2 letters)
            if not validate_state(data['state']):
                return {'message': 'Estado inválido. Use a sigla com 2 letras'}, 400
            
            # Validate CEP
            postal_code = validate_cep(data['postal_code'])
            if not postal_code:
                return {'message': 'CEP inválido - deve ter 8 dígitos'}, 400
            
            try:
                customer = Customer(
                    name=data['name'],
                    email=data['email'].lower(),
                    document=document,
                    phone=data['phone'],
                    street=data['street'],
                    number=data['number'],
                    complement=data.get('complement'),
                    district=data['district'],
                    city=data['city'],
                    state=data['state'].upper(),
                    postal_code=postal_code,
                    status='active',  # Status gerado automaticamente como active
                    company_id=current_user.company_id,
                    created_by=current_user,
                    updated_by=current_user
                )
                customer.set_password(password)  # Hash da senha
                customer.save()
                
                logger.info(f"Cliente criado com sucesso: {customer.email}")
                return customer.to_dict(), 201
                
            except NotUniqueError as e:
                if 'email' in str(e):
                    return {'message': 'Email já cadastrado'}, 409
                if 'document' in str(e):
                    return {'message': 'CPF já cadastrado'}, 409
                return {'message': 'Erro de duplicação'}, 409
                
        except Exception as e:
            logger.error(f"Error creating customer: {str(e)}")
            return {'message': 'Erro ao criar cliente'}, 500

@api.route('/<id>')
@api.param('id', 'Customer identifier')
class CustomerResource(Resource):
    
    @api.doc('get_customer')
    @api.marshal_with(customer_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user, id):
        """Obter cliente específico"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do cliente inválido'}, 400
            
            # Build query with multi-tenant isolation (admins can see all companies)
            query = {'id': id, 'visible': True}
            if current_user.role != 'admin':
                query['company_id'] = current_user.company_id
            
            customer = Customer.objects.get(**query)
            return customer.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting customer: {str(e)}")
            return {'message': 'Erro ao buscar cliente'}, 500
    
    @api.doc('update_customer')
    @api.expect(customer_update_model)
    @token_required
    @require_permission('customer', 'update')
    def put(self, current_user, id):
        """Atualizar cliente"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do cliente inválido'}, 400
            
            # Build query with multi-tenant isolation (admins can see all companies)
            query = {'id': id, 'visible': True}
            if current_user.role != 'admin':
                query['company_id'] = current_user.company_id
            
            customer = Customer.objects.get(**query)
            data = request.get_json()
            
            # Update fields
            if 'name' in data:
                customer.name = data['name']
            
            if 'email' in data:
                if not validate_email(data['email']):
                    return {'message': 'Formato de email inválido'}, 400
                # Check if email is already in use
                existing = Customer.objects(email=data['email'].lower(), id__ne=id).first()
                if existing:
                    return {'message': 'Email já está em uso'}, 409
                customer.email = data['email'].lower()
            
            if 'phone' in data:
                customer.phone = data['phone']
            
            if 'birth_date' in data:
                if not validate_date(data['birth_date']):
                    return {'message': 'Formato de data inválido. Use DD/MM/AAAA'}, 400
                customer.birth_date = data['birth_date']
            
            # Address fields
            if 'street' in data:
                customer.street = data['street']
            
            if 'number' in data:
                customer.number = data['number']
            
            if 'complement' in data:
                customer.complement = data['complement']
            
            if 'district' in data:
                customer.district = data['district']
            
            if 'city' in data:
                customer.city = data['city']
            
            if 'state' in data:
                if not validate_state(data['state']):
                    return {'message': 'Estado inválido. Use a sigla com 2 letras'}, 400
                customer.state = data['state'].upper()
            
            if 'postal_code' in data:
                postal_code = validate_cep(data['postal_code'])
                if not postal_code:
                    return {'message': 'CEP inválido - deve ter 8 dígitos'}, 400
                customer.postal_code = postal_code
            
            # Payment fields
            if 'monthly_amount' in data:
                customer.monthly_amount = data['monthly_amount']
            
            if 'auto_debit' in data:
                customer.auto_debit = data['auto_debit']
            
            if 'status' in data:
                customer.status = data['status']
            
            customer.updated_by = current_user
            customer.save()
            
            return customer.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error updating customer: {str(e)}")
            return {'message': 'Erro ao atualizar cliente'}, 500
    
    @api.doc('delete_customer')
    @token_required
    @require_permission('customer', 'delete')
    def delete(self, current_user, id):
        """Deletar cliente (soft delete)"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do cliente inválido'}, 400
            
            customer = Customer.objects.get(id=id, visible=True)
            customer.visible = False
            customer.status = 'inactive'
            customer.updated_by = current_user
            customer.save()
            
            return {'message': 'Cliente deletado com sucesso'}, 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error deleting customer: {str(e)}")
            return {'message': 'Erro ao deletar cliente'}, 500

@api.route('/<id>/payment-card')
@api.param('id', 'Customer identifier')
class CustomerPaymentCard(Resource):
    
    @api.doc('update_payment_card')
    @api.expect(payment_card_model)
    @token_required
    @require_permission('customer', 'update')
    def post(self, current_user, id):
        """Atualizar dados do cartão de pagamento"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do cliente inválido'}, 400
            
            customer = Customer.objects.get(id=id, visible=True, status='active')
            data = request.get_json()
            
            # Validate required fields
            required_fields = ['card_token', 'card_brand', 'card_last_digits']
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400
            
            # Validate last digits (should be 4 digits)
            digits_pattern = r'^\d{4}$'
            if not re.match(digits_pattern, data['card_last_digits']):
                return {'message': 'Últimos dígitos do cartão devem ter 4 números'}, 400
            
            # Update card information
            customer.card_token = data['card_token']
            customer.card_brand = data['card_brand']
            customer.card_last_digits = data['card_last_digits']
            customer.updated_by = current_user
            customer.save()
            
            logger.info(f"Payment card updated for customer {customer.email}")
            
            return {
                'message': 'Cartão de pagamento atualizado com sucesso',
                'customer_id': str(customer.id),
                'card_brand': customer.card_brand,
                'card_last_digits': customer.card_last_digits
            }, 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado ou inativo'}, 404
        except Exception as e:
            logger.error(f"Error updating payment card: {str(e)}")
            return {'message': 'Erro ao atualizar cartão'}, 500
    
    @api.doc('remove_payment_card')
    @token_required
    @require_permission('customer', 'update')
    def delete(self, current_user, id):
        """Remover cartão de pagamento"""
        try:
            if not ObjectId.is_valid(id):
                return {'message': 'ID do cliente inválido'}, 400
            
            customer = Customer.objects.get(id=id, visible=True)
            
            # Remove card information
            customer.card_token = None
            customer.card_brand = None
            customer.card_last_digits = None
            customer.auto_debit = False  # Disable auto debit when removing card
            customer.updated_by = current_user
            customer.save()
            
            logger.info(f"Payment card removed for customer {customer.email}")
            
            return {'message': 'Cartão removido com sucesso'}, 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error removing payment card: {str(e)}")
            return {'message': 'Erro ao remover cartão'}, 500

@api.route('/search')
class CustomerSearch(Resource):
    
    @api.doc('search_customers',
             params={
                 'q': {'type': 'string', 'required': True, 
                       'description': 'Termo de busca (CPF, email ou nome)'},
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 10}
             })
    @api.marshal_with(pagination_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user):
        """Buscar clientes por CPF, email ou nome"""
        try:
            search_term = request.args.get('q')
            if not search_term:
                return {'message': 'Termo de busca é obrigatório'}, 400
            
            page = max(1, int(request.args.get('page', 1)))
            per_page = max(1, min(100, int(request.args.get('per_page', 10))))
            
            # Build search conditions
            search_conditions = []
            
            # Search by email
            search_conditions.append({
                'email': {'$regex': re.escape(search_term), '$options': 'i'}
            })
            
            # Search by name
            search_conditions.append({
                'name': {'$regex': re.escape(search_term), '$options': 'i'}
            })
            
            # Search by CPF (remove non-digits)
            cpf_cleaned = re.sub(r'\D', '', search_term)
            if cpf_cleaned:
                search_conditions.append({'cpf': cpf_cleaned})
            
            # Build query
            query = Customer.objects(visible=True).filter(__raw__={'$or': search_conditions})
            
            total = query.count()
            total_pages = (total + per_page - 1) // per_page
            customers = query.order_by('name').skip((page - 1) * per_page).limit(per_page)
            
            return {
                'customers': [c.to_dict() for c in customers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }, 200
            
        except Exception as e:
            logger.error(f"Error searching customers: {str(e)}")
            return {'message': 'Erro ao buscar clientes'}, 500

@api.route('/by-cpf/<cpf>')
@api.param('cpf', 'Customer CPF')
class CustomerByCPF(Resource):
    
    @api.doc('get_customer_by_cpf')
    @api.marshal_with(customer_model)
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user, cpf):
        """Buscar cliente por CPF"""
        try:
            # Clean CPF (remove non-digits)
            cpf_cleaned = validate_cpf(cpf)
            if not cpf_cleaned:
                return {'message': 'CPF inválido - deve ter 11 dígitos'}, 400
            
            customer = Customer.objects.get(cpf=cpf_cleaned, visible=True)
            return customer.to_dict(), 200
            
        except DoesNotExist:
            return {'message': 'Cliente não encontrado'}, 404
        except Exception as e:
            logger.error(f"Error getting customer by CPF: {str(e)}")
            return {'message': 'Erro ao buscar cliente'}, 500

@api.route('/stats')
class CustomerStats(Resource):
    
    @api.doc('get_customer_stats')
    @token_required
    @require_permission('customer', 'read')
    def get(self, current_user):
        """Obter estatísticas dos clientes"""
        try:
            total_customers = Customer.objects(visible=True).count()
            active_customers = Customer.objects(visible=True, status='active').count()
            inactive_customers = Customer.objects(visible=True, status='inactive').count()
            auto_debit_customers = Customer.objects(visible=True, status='active', auto_debit=True).count()
            
            # Calculate total monthly revenue
            pipeline = [
                {'$match': {'visible': True, 'status': 'active'}},
                {'$group': {'_id': None, 'total': {'$sum': '$monthly_amount'}}}
            ]
            result = list(Customer.objects.aggregate(pipeline))
            total_monthly_revenue = result[0]['total'] if result else 0
            
            return {
                'total_customers': total_customers,
                'active_customers': active_customers,
                'inactive_customers': inactive_customers,
                'auto_debit_customers': auto_debit_customers,
                'total_monthly_revenue': total_monthly_revenue,
                'average_monthly_amount': total_monthly_revenue / active_customers if active_customers > 0 else 0
            }, 200
            
        except Exception as e:
            logger.error(f"Error getting customer stats: {str(e)}")
            return {'message': 'Erro ao obter estatísticas'}, 500