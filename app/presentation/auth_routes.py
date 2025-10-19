import logging
from flask import request, jsonify
from flask_restx import Namespace, Resource, fields
from app.domain.models import User, Customer
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import jwt
import datetime
from mongoengine.errors import DoesNotExist
from mongoengine import Document, StringField, DateTimeField
from config import Config

logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

api = Namespace('auth', description='Authentication operations')

class TokenBlacklist(Document):
    token = StringField(required=True, unique=True)
    created_at = DateTimeField(default=datetime.datetime.utcnow)
    meta = {
        'collection': 'token_blacklist',
        'indexes': [
            {'fields': ['token'], 'unique': True},
            {'fields': ['created_at'], 'expireAfterSeconds': 604800}
        ]
    }

login_model = api.model('Login', {
    'identifier': fields.String(required=True, description='Email or CPF'),
    'password': fields.String(required=True, description='Password')
})

def create_token(user, token_type='access', resource_id=None):
    now = datetime.datetime.utcnow()

    if token_type == 'access':
        expires = now + datetime.timedelta(hours=1)
        permissions = [p.name for p in user.permissions] if user.permissions else []
    else:
        if token_type == 'customer':
            expires = now + datetime.timedelta(hours=1)
            permissions = ["vehicle_read", "vehicle_write", "vehicle_update"]
        else:
            expires = now + datetime.timedelta(days=7)
            permissions = []

    payload = {
        'user_id': str(user.id),
        'email': user.email,
        'role': user.role,
        'permissions': permissions,
        'exp': expires,
        'iat': now,
        'type': token_type,
        'action_type': token_type,
        'resource_id': resource_id,
        'jti': os.urandom(8).hex()
    }

    secret_key = Config.SECRET_KEY
    if not secret_key:
        logger.error("FLASK_SECRET_KEY not configured")
        raise ValueError("FLASK_SECRET_KEY not configured")

    return jwt.encode(
        payload,
        secret_key,
        algorithm="HS256"
    )

def validate_token_format(token):
    if not token or not isinstance(token, str):
        return False
    parts = token.split('.')
    return len(parts) == 3 and all(len(p) > 0 for p in parts)

def require_permission(resource_type, action_type):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):

            current_user = kwargs.get('current_user')
            current_permissions = kwargs.get('current_permissions')
            permission_name = f'{resource_type}_{action_type}'

            if not current_user:
                return {'message': 'Usuário não autenticado'}, 401

            if current_user.role == 'customer':
                current_permissions = ["vehicle_read", "vehicle_write", "vehicle_update"]
            else:
                current_permissions = [p.name for p in current_user.permissions] if current_user.permissions else []

            if permission_name not in current_permissions:
                return {
                    'message': 'Permissão insuficiente',
                    'required_permission': f'{resource_type}_{action_type}'
                }, 403

            return f(*args, **kwargs)
        return decorated
    return decorator

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            auth_header = request.headers.get('Authorization', '').strip()

            if not auth_header:
                logger.warning("No Authorization header provided")
                return {'message': 'Token não fornecido', 'error': 'missing_token'}, 401

            # Handle case where token is sent without Bearer prefix
            if ' ' not in auth_header:
                token = auth_header
            else:
                parts = auth_header.split(' ')
                if len(parts) != 2 or parts[0].lower() != 'bearer':
                    logger.warning(f"Invalid Authorization header format: {auth_header}")
                    return {'message': 'Formato do token inválido', 'error': 'invalid_format'}, 401
                token = parts[1]

            if not validate_token_format(token):
                logger.warning("Invalid token format")
                return {'message': 'Formato do token inválido', 'error': 'invalid_format'}, 401

            if TokenBlacklist.objects(token=token).first():
                logger.warning(f"Token found in blacklist")
                return {'message': 'Token revogado', 'error': 'revoked_token'}, 401

            secret_key = Config.SECRET_KEY
            if not secret_key:
                logger.error("FLASK_SECRET_KEY not configured")
                return {'message': 'Erro de configuração do servidor', 'error': 'server_config'}, 500

            try:
                data = jwt.decode(
                    token,
                    secret_key,
                    algorithms=["HS256"]
                )
            except jwt.ExpiredSignatureError:
                logger.warning("Token has expired")
                return {'message': 'Token expirado', 'error': 'token_expired'}, 401
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid token: {str(e)}")
                return {'message': 'Token inválido', 'error': 'invalid_token'}, 401

            if data.get('type') not in ['access', 'customer', 'document_signature']:
                logger.warning(f"Invalid token type: {data.get('type')}")
                return {'message': 'Tipo de token inválido', 'error': 'invalid_token_type'}, 401
            
            current_user = User.objects(id=data['user_id']).first()
            if not current_user:
                current_user = Customer.objects(id=data['user_id']).first()
                
            try:
                # Check if user is active
                if current_user.status != 'active':
                    logger.warning(f"Inactive user attempted to access: {current_user.email}")
                    return {'message': 'Usuário inativo', 'error': 'inactive_user'}, 401

                if current_user.email != data['email']:
                    logger.warning("Token email mismatch with current user")
                    return {'message': 'Token inválido', 'error': 'email_mismatch'}, 401

                if current_user.role != data['role']:
                    logger.warning("Token role mismatch with current user")
                    return {'message': 'Token inválido', 'error': 'role_mismatch'}, 401

                # For class methods, pass current_user as a kwarg
                if len(args) > 0 and isinstance(args[0], Resource):
                    return f(args[0], current_user=current_user, *args[1:], **kwargs)
                # For regular functions
                return f(current_user, *args, **kwargs)

            except DoesNotExist:
                logger.warning(f"User not found for ID: {data['user_id']}")
                return {'message': 'Usuário não encontrado', 'error': 'user_not_found'}, 404

        except Exception as e:
            logger.error(f"Unexpected error in token validation: {str(e)}")
            return {'message': 'Erro na validação do token', 'error': 'validation_error'}, 500

    return decorated

@api.route('/login')
class Login(Resource):
    @api.doc('login')
    @api.expect(login_model)
    @limiter.limit("5 per minute")
    def post(self):
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            identifier = data.get('identifier')
            password = data.get('password')

            if not identifier or not password:
                return {'message': 'Identificador e senha são obrigatórios'}, 400

            user = User.objects(email=identifier).first()
            if not user:
                user = User.objects(document=identifier).first()

            if user and user.check_password(password):
                # Check if user is active
                if user.status != 'active':
                    logger.warning(f"Login attempt by inactive user: {user.email}")
                    return {'message': 'Usuário inativo'}, 401

                access_token = create_token(user, 'access')
                refresh_token = create_token(user, 'refresh')

                return {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'token_type': 'Bearer',
                    'expires_in': 3600,
                    'requires_password_change': not user.password_changed,
                    'user': {
                        'id': str(user.id),
                        'name': user.name,
                        'email': user.email,
                        'role': user.role,
                        'permissions': [p.name for p in user.permissions] if user.permissions else [],
                    }
                }, 200

            return {'message': 'Credenciais inválidas'}, 401

        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return {'message': 'Erro ao realizar login'}, 500


@api.route('/customer/login')
class LoginCustomer(Resource):
    @api.doc('login')
    @api.expect(login_model)
    @limiter.limit("5 per minute")
    def post(self):
        try:
            data = request.get_json()
            if not data:
                return {'message': 'Dados não fornecidos'}, 400

            identifier = data.get('identifier')
            password = data.get('password')

            if not identifier or not password:
                return {'message': 'Identificador e senha são obrigatórios'}, 400

            customer = Customer.objects(email=identifier).first()
            if not customer:
                customer = Customer.objects(document=identifier).first()
            if not customer:
                customer = Customer.objects(phone=identifier).first()

            if customer and customer.check_password(password):
                # Check if user is active
                if customer.status != 'active':
                    logger.warning(f"Login attempt by inactive user: {customer.document}")
                    return {'message': 'Usuário inativo'}, 401

                access_token = create_token(customer, 'customer')
                refresh_token = create_token(customer, 'refresh')

                return {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'token_type': 'Bearer',
                    'expires_in': 3600,
                    'requires_password_change': not customer.password_changed,
                    'user': {
                        'id': str(customer.id),
                        'name': customer.name,
                        'email': customer.email,
                        'role': customer.role,
                        'document': customer.document,
                        'phone': customer.phone
                    }
                }, 200

            return {'message': 'Credenciais inválidas'}, 401

        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return {'message': 'Erro ao realizar login'}, 500




@api.route('/refresh')
class TokenRefresh(Resource):
    @api.doc('refresh_token')
    def post(self):
        try:
            auth_header = request.headers.get('Authorization')
            if not auth_header:
                return {'message': 'Token não fornecido'}, 401

            if not auth_header.startswith('Bearer '):
                return {'message': 'Token inválido'}, 401

            refresh_token = auth_header.split(' ')[1]

            if TokenBlacklist.objects(token=refresh_token).first():
                return {'message': 'Token inválido'}, 401

            secret_key = Config.SECRET_KEY
            if not secret_key:
                raise ValueError("FLASK_SECRET_KEY not configured")

            data = jwt.decode(
                refresh_token,
                secret_key,
                algorithms=["HS256"]
            )

            if data.get('type') != 'refresh':
                return {'message': 'Token inválido'}, 401

            user = User.objects.get(id=data['user_id'])

            # Check if user is active before refreshing token
            if user.status != 'active':
                logger.warning(f"Token refresh attempt by inactive user: {user.email}")
                return {'message': 'Usuário inativo'}, 401

            new_access_token = create_token(user, 'access')

            return {
                'access_token': new_access_token,
                'token_type': 'Bearer',
                'expires_in': 3600
            }, 200

        except jwt.ExpiredSignatureError:
            return {'message': 'Token expirado'}, 401
        except (jwt.InvalidTokenError, DoesNotExist):
            return {'message': 'Token inválido'}, 401
        except Exception as e:
            logger.error(f"Token refresh error: {str(e)}")
            return {'message': 'Erro ao atualizar token'}, 500

@api.route('/password/recover')
class PasswordRecover(Resource):
    @api.doc('recover_password')
    def post(self):
        """Request password recovery"""
        try:
            data = request.get_json()
            if not data or 'identifier' not in data:
                return {'message': 'Identificador é obrigatório'}, 400

            identifier = data.get('identifier')

            user = User.objects(email=identifier).first()
            if not user:
                user = User.objects(cpf=identifier).first()

            if not user:
                return {'message': 'Usuário não encontrado'}, 404

            # Gerar token temporário de recuperação (válido por 1 hora)
            recovery_token = create_token(user, 'recovery')

            # Enviar email de recuperação
            from app.infrastructure.email_service import EmailService
            if EmailService.send_password_recovery_email(user.email, recovery_token):
                return {'message': 'Email de recuperação enviado com sucesso'}, 200
            else:
                return {'message': 'Erro ao enviar email de recuperação'}, 500

        except Exception as e:
            logger.error(f"Password recovery error: {str(e)}")
            return {'message': 'Erro ao processar recuperação de senha'}, 500

@api.route('/password/reset')
class PasswordReset(Resource):
    @api.doc('reset_password')
    def post(self):
        """Reset password using recovery token"""
        try:
            data = request.get_json()
            if not data or 'token' not in data or 'new_password' not in data:
                return {'message': 'Token e nova senha são obrigatórios'}, 400

            # Verificar token no blacklist de uso único
            token = data['token']
            
            # Verifica se o token já foi usado anteriormente
            from app.application.link_token_service import UsedLinkToken
            if UsedLinkToken.objects(token=token).first():
                logger.warning(f"Tentativa de usar token de recuperação já utilizado")
                return {'message': 'Token já utilizado anteriormente'}, 401
                
            try:
                token_data = jwt.decode(
                    token,
                    Config.SECRET_KEY,
                    algorithms=["HS256"]
                )

                if token_data.get('type') != 'recovery':
                    return {'message': 'Token inválido'}, 401

                user = User.objects.get(id=token_data['user_id'])
                
                # Marcar token como usado antes de processar
                from app.application.link_token_service import UsedLinkToken
                from datetime import datetime
                expires_at = datetime.fromtimestamp(token_data['exp'])
                used_token = UsedLinkToken(
                    token=token,
                    expires_at=expires_at
                )
                used_token.save()
                logger.info(f"Token de recuperação marcado como utilizado")
                
                # Processar a alteração da senha
                user.set_password(data['new_password'])
                user.password_changed = True
                user.save()

                return {'message': 'Senha alterada com sucesso'}, 200

            except jwt.ExpiredSignatureError:
                return {'message': 'Token expirado'}, 401
            except jwt.InvalidTokenError:
                return {'message': 'Token inválido'}, 401

        except Exception as e:
            logger.error(f"Password reset error: {str(e)}")
            return {'message': 'Erro ao resetar senha'}, 500

@api.route('/password/change')
class PasswordChange(Resource):
    @api.doc('change_password')
    @token_required
    def post(self, current_user):
        """Change password (requires authentication)"""
        try:
            data = request.get_json()
            if not data or 'current_password' not in data or 'new_password' not in data:
                return {'message': 'Senha atual e nova senha são obrigatórias'}, 400

            if not current_user.check_password(data['current_password']):
                return {'message': 'Senha atual incorreta'}, 401

            current_user.set_password(data['new_password'])
            current_user.password_changed = True
            current_user.save()

            return {'message': 'Senha alterada com sucesso'}, 200

        except Exception as e:
            logger.error(f"Password change error: {str(e)}")
            return {'message': 'Erro ao alterar senha'}, 500

@api.route('/logout')
class Logout(Resource):
    @api.doc('logout')
    @token_required
    def post(self, current_user):
        try:
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return {'message': 'Token não fornecido'}, 401

            token = auth_header.split(' ')[1]

            blacklist_entry = TokenBlacklist(token=token)
            blacklist_entry.save()

            return {'message': 'Logout realizado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Logout error: {str(e)}")
            return {'message': 'Erro ao realizar logout'}, 500

def cleanup_blacklist():
    try:
        expired_date = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        result = TokenBlacklist.objects(created_at__lt=expired_date).delete()
    except Exception as e:
        logger.error(f"Error cleaning up token blacklist: {str(e)}")

import os