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
from app.infrastructure.stripe_service import StripeService
from config import Config


def cancelar_assinaturas(email: str = None, todos: bool = False):
    if not Config.STRIPE_SECRET_KEY:
        print('ERRO: STRIPE_SECRET_KEY não configurada.')
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
        stripe_id = sub.provider_subscription_id
        print(f'[{sub.id}] status={sub.status} | stripe_id={stripe_id or "(sem Stripe id)"}')

        if stripe_id:
            if StripeService.cancel_subscription(stripe_id):
                print(f'  → Stripe: cancelada ✓')
                canceladas += 1
            else:
                print(f'  → Stripe: falha ao cancelar')
                erros += 1
        else:
            print(f'  → Sem Stripe id, pulando cancelamento na Stripe')
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
