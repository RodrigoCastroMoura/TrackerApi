from flask_restx import Namespace, Resource, fields
from app.domain.models import Logradouro, Bairro, Localidade
from app.presentation.auth_routes import token_required
import re
import logging

logger = logging.getLogger(__name__)

api = Namespace('cep', description='Busca de CEP')

cep_model = api.model('Logradouro', {
    'cep': fields.String(description='CEP'),
    'logradouro': fields.String(description='Nome do logradouro'),
    'bairro': fields.String(description='Bairro'),
    'cidade': fields.String(description='Município'),
    'uf': fields.String(description='UF'),
})

localidade_cep_model = api.model('LocalidadeCEP', {
    'cep': fields.String(description='CEP da localidade'),
    'cidade': fields.String(description='Município'),
    'uf': fields.String(description='UF'),
})


def _clean_cep(raw: str) -> str | None:
    clean = re.sub(r'\D', '', raw or '')
    return clean if len(clean) == 8 else None


@api.route('/<string:cep>')
@api.param('cep', 'CEP com ou sem formatação (ex: 13500-359 ou 13500359)')
class CepResource(Resource):

    @api.doc('get_cep')
    @api.marshal_with(cep_model)
    @token_required
    def get(self, current_user, cep: str):
        """Busca logradouro, bairro e município pelo CEP"""
        cep_clean = _clean_cep(cep)
        if not cep_clean:
            api.abort(400, 'CEP inválido. Informe 8 dígitos numéricos.')

        logradouro = Logradouro.objects(cep=cep_clean).first()

        if logradouro:
            bai_seq = logradouro.bai_nu_sequencial_ini
            loc_seq = logradouro.loc_nu_sequencial

            bairro = Bairro.objects(bai_nu_sequencial=bai_seq).first() if bai_seq else None
            localidade = Localidade.objects(loc_nu_sequencial=loc_seq).first() if loc_seq else None

            return {
                'cep': cep_clean,
                'logradouro': logradouro.log_nome or '',
                'bairro': bairro.bai_no if bairro else '',
                'cidade': localidade.loc_no if localidade else '',
                'uf': logradouro.ufe_sg or '',
            }

        # Fallback: CEP pode ser de uma localidade (cidade inteira)
        localidade = Localidade.objects(cep=cep_clean).first()
        if localidade:
            return {
                'cep': cep_clean,
                'logradouro': '',
                'complemento': '',
                'bairro': '',
                'cidade': localidade.loc_no or '',
                'uf': localidade.ufe_sg or '',
            }

        api.abort(404, f'CEP {cep_clean} não encontrado.')
