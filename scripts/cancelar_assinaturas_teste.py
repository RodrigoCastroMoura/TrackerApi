"""
Script para cancelar todas as assinaturas de teste de um cliente.
Uso:
    python scripts/cancelar_assinaturas_teste.py
    python scripts/cancelar_assinaturas_teste.py --email outro@email.com
    python scripts/cancelar_assinaturas_teste.py --todos
"""
import os
import sys
import argparse

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from mongoengine import connect
connect(host=os.environ.get('MONGODB_URI'))

from app.domain.models import Subscription, Customer
from app.infrastructure.mercadopago_service import MercadoPagoService


def cancelar_assinaturas(email: str = None, todos: bool = False):
    sdk = MercadoPagoService.get_sdk()
    if not sdk:
        print('ERRO: MERCADOPAGO_ACCESS_TOKEN não configurado.')
        sys.exit(1)

    if todos:
        subs = Subscription.objects(visible=True, status__nin=['canceled'])
        print(f'Cancelando todas as assinaturas ativas ({subs.count()} encontradas)...')
    elif email:
        customer = Customer.objects(email=email, visible=True).first()
        if not customer:
            print(f'Cliente não encontrado: {email}')
            sys.exit(1)
        subs = Subscription.objects(customer_id=customer.id, visible=True)
        print(f'Cancelando assinaturas de {email} ({subs.count()} encontradas)...')
    else:
        print('Informe --email <email> ou --todos')
        sys.exit(1)

    if subs.count() == 0:
        print('Nenhuma assinatura encontrada.')
        return

    print()
    canceladas = 0
    erros = 0

    for sub in subs:
        mp_id = sub.mp_subscription_id
        print(f'[{sub.id}] status={sub.status} | mp_id={mp_id or "(sem MP id)"}')

        if mp_id:
            try:
                resp = sdk.preapproval().update(mp_id, {'status': 'cancelled'})
                http = resp.get('status')
                if http in [200, 201]:
                    print(f'  → Mercado Pago: cancelada ✓')
                    canceladas += 1
                elif http == 400:
                    msg = resp.get('response', {}).get('message', 'bad request')
                    print(f'  → Mercado Pago: já cancelada ou inválida ({msg})')
                    canceladas += 1
                else:
                    print(f'  → Mercado Pago: HTTP {http}')
                    erros += 1
            except Exception as e:
                print(f'  → Mercado Pago: erro — {e}')
                erros += 1
        else:
            print(f'  → Sem MP id, pulando cancelamento no MP')
            canceladas += 1

        sub.status = 'canceled'
        sub.visible = False
        sub.save()
        print(f'  → Banco: removida ✓')

    print()
    print(f'Resultado: {canceladas} canceladas | {erros} erros')
    print('Cliente pronto para nova assinatura de teste.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Cancela assinaturas de teste')
    parser.add_argument('--email', type=str, help='Email do cliente específico')
    parser.add_argument('--todos', action='store_true', help='Cancela todas as assinaturas ativas')
    args = parser.parse_args()

    cancelar_assinaturas(email=args.email, todos=args.todos)
