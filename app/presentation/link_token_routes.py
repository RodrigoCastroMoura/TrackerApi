
from flask import request, jsonify, redirect, url_for
from flask_restx import Namespace, Resource, fields
from app.application.link_token_service import LinkTokenService
from app.domain.models import User
from app.presentation.auth_routes import token_required, create_token
import logging

logger = logging.getLogger(__name__)

api = Namespace('links', description='Operações de link com token')

@api.route('/validate/<token>')
class LinkTokenValidator(Resource):
    @api.doc('validate_link_token')
    def get(self, token):
        """Valida um token recebido via URL e redireciona conforme a ação"""
        try:
            # Verificar token (uso único)
            payload = LinkTokenService.verify_link_token(token, single_use=True)
            if not payload:
                return {'message': 'Token inválido, expirado ou já utilizado'}, 401
        
            # Verificar se resource_id existe no payload
            resource_id = payload.get('resource_id')
            if not resource_id:
                return {'message': 'ID do documento não encontrado no token'}, 400
            
            user = User.objects.get(id=payload.get('user_id'), status= 'active') 

            if not user:
                return {'message': 'ID do usuario não encontrado no token'}, 400
            
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
                    'company_id': str(user.company_id.id),
                    'document_id': resource_id
                }
            }, 200
              
        except Exception as e:
            logger.error(f"Erro ao processar token de link: {str(e)}")
            return {'message': 'Erro ao processar token'}, 500
