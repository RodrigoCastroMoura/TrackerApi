from flask_restx import Namespace, Resource, fields
from app.presentation.auth_routes import customer_token_required
from app.domain.models import Document
from mongoengine.errors import DoesNotExist
from bson.objectid import ObjectId
import logging

logger = logging.getLogger(__name__)

api = Namespace('documents', description='Document file operations')

document_url_model = api.model(
    'DocumentUrl', {
        'document_id': fields.String(description='Document ID'),
        'url': fields.String(description='URL do PDF do documento'),
        'signature': fields.Boolean(description='Indica se o documento ja foi assinado')
    })

error_model = api.model('Error',
                        {'erro': fields.String(description='Error message')})


@api.route('/<id>')
@api.param('id', 'Document ID', required=True)
class DocumentFile(Resource):

    @api.doc('get_document_url',
             responses={
                 200: 'URL retornada com sucesso',
                 400: 'Invalid input',
                 403: 'Unauthorized',
                 404: 'Document not found',
                 500: 'Server error'
             })
    @api.response(200, 'Success', document_url_model)
    @api.response(400, 'Invalid input', error_model)
    @api.response(403, 'Unauthorized', error_model)
    @api.response(404, 'Document not found', error_model)
    @api.response(500, 'Server error', error_model)
    @customer_token_required
    def get(self, current_customer, id):
        """Retorna a URL do PDF do documento para visualização no front-end"""
        try:
            if not ObjectId.is_valid(id):
                return {'erro': 'ID do documento inválido'}, 400

            try:
                document = Document.objects.get(id=id, status='active')
            except DoesNotExist:
                return {'erro': 'Documento não encontrado'}, 404

            if current_customer.role != 'admin' and str(
                    current_customer.id) != str(document.customer_id.id):
                return {'erro': 'Não autorizado a acessar este documento'}, 403

            if not document.url:
                return {'erro': 'URL do documento não encontrada'}, 404

            return {
                'document_id': str(document.id),
                'url': document.url,
                'signature': document.signature
            }, 200

        except Exception as e:
            logger.error(f"Error retrieving document URL: {str(e)}", exc_info=True)
            return {'erro': str(e)}, 500
