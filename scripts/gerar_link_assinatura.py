"""
Gera uma URL de teste para a tela de assinatura de documento (/assinar-documento/<token>)
do TrackerPortal, sem depender de nenhum fluxo que ainda cria esses links automaticamente.

Quem assina o documento é o Customer (não o User interno).

Uso:
    python scripts/gerar_link_assinatura.py --customer-id <ObjectId do Customer> --document-id <ObjectId do Document>
    python scripts/gerar_link_assinatura.py --email cliente@empresa.com --document-id <ObjectId do Document>

O token é assinado com a mesma SECRET_KEY do servidor (via .env), então só é válido
testando contra uma instância do TrackerApi rodando com o mesmo .env.

IMPORTANTE: link_token_routes.py (/api/links/validate/<token>) ainda resolve o token
como User, não Customer. Até isso ser corrigido lá, validar um token gerado por este
script vai falhar com "ID do usuario não encontrado no token" — o script já deixa
tudo pronto pro dia em que esse endpoint for ajustado para Customer.
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from mongoengine import connect
connect(host=os.environ.get('MONGODB_URI'))

import jwt
import time
from app.domain.models import Customer
from app.application.auth_service import AuthService
from config import Config


def gerar_token(customer_id: str, document_id: str, expiration_days: int = 7) -> str:
    # time.time() já é epoch UTC; datetime.utcnow().timestamp() NÃO é (interpreta o
    # horário naive como hora local), o que gera iat/exp errados fora do fuso UTC.
    now = int(time.time())
    payload = {
        'user_id': str(customer_id),
        'action_type': 'document_signature',
        'resource_id': str(document_id),
        'exp': now + expiration_days * 86400,
        'iat': now,
        'type': 'link',
        'jti': AuthService._generate_token_id(),
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')


def main():
    parser = argparse.ArgumentParser(description='Gera link de teste para assinatura de documento')
    parser.add_argument('--customer-id', help='ObjectId do Customer que vai assinar')
    parser.add_argument('--email', help='Email do Customer (alternativa a --customer-id)')
    parser.add_argument('--document-id', required=True, help='ObjectId do Document a ser assinado')
    parser.add_argument('--base-url', default='http://localhost:5000', help='URL base do TrackerPortal')
    args = parser.parse_args()

    if not args.customer_id and not args.email:
        print('ERRO: informe --customer-id ou --email')
        sys.exit(1)

    if args.email:
        customer = Customer.objects(email=args.email, status='active').first()
        if not customer:
            print(f'Cliente não encontrado ou inativo: {args.email}')
            sys.exit(1)
        customer_id = str(customer.id)
        print(f'Cliente encontrado: {customer.name} ({customer_id})')
    else:
        customer_id = args.customer_id

    token = gerar_token(customer_id, args.document_id)
    print('\nToken gerado:')
    print(token)
    print('\nURL de teste:')
    print(f'{args.base_url}/assinar-documento/{token}')


if __name__ == '__main__':
    main()
