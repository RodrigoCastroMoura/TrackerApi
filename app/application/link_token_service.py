
import jwt
import logging
from datetime import datetime, timedelta
from flask import current_app
from app.domain.models import User
from app.application.auth_service import AuthService, TokenBlacklist
from mongoengine import Document, StringField, DateTimeField

logger = logging.getLogger(__name__)

class UsedLinkToken(Document):
    """Classe para armazenar tokens de link já utilizados"""
    token = StringField(required=True, unique=True)
    blacklisted_at = DateTimeField(default=datetime.utcnow)
    expires_at = DateTimeField(required=True)
    meta = {'collection': 'used_link_tokens'}

class LinkTokenService:
    @staticmethod
    def create_link_token(user_id, action_type, resource_id=None, expiration_days=7):
        """
        Cria um token para ser usado em links (email, compartilhamento, etc)
        
        Args:
            user_id: ID do usuário relacionado ao link
            action_type: Tipo de ação (ex: 'password_reset', 'document_access', etc)
            resource_id: ID opcional do recurso relacionado (ex: ID do documento)
            expiration_days: Dias até expiração do token
            
        Returns:
            Token string para uso em URL
        """
        try:
            # Verificar SECRET_KEY
            secret_key = current_app.config.get('SECRET_KEY')
            if not secret_key:
                logger.error("SECRET_KEY não encontrada na configuração")
                raise ValueError("Chave secreta da aplicação não configurada")
            
            # Gerar data de expiração
            expiration = datetime.utcnow() + timedelta(days=expiration_days)
            
            # Preparar payload
            payload = {
                'user_id': str(user_id),
                'action_type': action_type,
                'exp': int(expiration.timestamp()),
                'iat': int(datetime.utcnow().timestamp()),
                'type': 'link',
                'jti': AuthService._generate_token_id()
            }
            
            # Adicionar resource_id se fornecido
            if resource_id:
                payload['resource_id'] = str(resource_id)
            
            # Codificar token
            token = jwt.encode(
                payload,
                secret_key,
                algorithm='HS256'
            )
            
            # Garantir que token seja string
            if isinstance(token, bytes):
                token = token.decode('utf-8')
                
            logger.info(f"Token de link criado com sucesso para ação: {action_type}")
            return token
            
        except Exception as e:
            logger.error(f"Erro ao criar token de link: {str(e)}")
            raise ValueError(f"Erro criando token de link: {str(e)}")
    
    @staticmethod
    def verify_link_token(token, single_use=True):
        """
        Verifica e valida um token de link
        
        Args:
            token: Token string a ser verificado
            single_use: Indica se o token deve ser usado apenas uma vez
            
        Returns:
            dict: Payload do token se válido, ou None se inválido
        """
        try:
            # Validar formato do token
            is_valid, error = AuthService._validate_token_format(token)
            if not is_valid:
                logger.warning(f"Verificação de token de link falhou: {error}")
                return None
            
            # Verificar se o token já foi usado (se for single_use)
            if single_use:
                try:
                    already_used = UsedLinkToken.objects(token=token).first()
                    if already_used:
                        logger.warning("Verificação de token de link falhou: Token já foi utilizado")
                        return None
                except Exception as e:
                    logger.error(f"Erro ao verificar uso anterior do token: {str(e)}")
            
            try:
                # Decodificar e verificar token
                payload = jwt.decode(
                    token, 
                    current_app.config['SECRET_KEY'], 
                    algorithms=['HS256']
                )
                
                # Validar tipo de token
                if payload.get('type') not in ['link', 'document_signature']:
                    logger.warning("Verificação de token de link falhou: Tipo de token inválido")
                    return None
                
                # Validar claims obrigatórias
                required_claims = ['user_id', 'type', 'exp', 'iat', 'type', 'jti']
                if not all(claim in payload for claim in required_claims):
                    logger.warning("Verificação de token de link falhou: Claims obrigatórias ausentes")
                    return None
                
                # Se o token é de uso único, marcar como usado
                if single_use:
                    try:
                        expires_at = datetime.fromtimestamp(payload['exp'])
                        used_token = UsedLinkToken(
                            token=token,
                            expires_at=expires_at
                        )
                        #used_token.save()
                        logger.info(f"Token de link marcado como utilizado: {token[:10]}...")
                    except Exception as e:
                        logger.error(f"Erro ao marcar token como utilizado: {str(e)}")
                
                return payload
                
            except jwt.ExpiredSignatureError:
                logger.warning("Verificação de token de link falhou: Token expirado")
                return None
            except jwt.InvalidTokenError as e:
                logger.warning(f"Verificação de token de link falhou: Token inválido - {str(e)}")
                return None
            
        except Exception as e:
            logger.error(f"Erro na verificação de token de link: {str(e)}")
            return None
