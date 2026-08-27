"""
Migração: renomeia os campos com prefixo "mp_" das coleções `subscriptions` e
`subscription_plans` para nomes neutros de provedor de pagamento.

    subscription_plans.mp_preapproval_plan_id     -> provider_plan_id
    subscriptions.mp_subscription_id              -> provider_subscription_id
    subscriptions.mp_payer_id                     -> provider_customer_id
    subscriptions.mp_preapproval_plan_id          -> provider_plan_id
    subscriptions.mp_status                       -> provider_status
    subscriptions.payment_history[].mp_authorized_payment_id -> provider_payment_id

Uso:
    python scripts/migrar_campos_mp_para_provider.py --dry-run   # só mostra o que faria
    python scripts/migrar_campos_mp_para_provider.py             # aplica

Idempotente: rodar de novo não causa efeito (nenhum doc terá mais campo mp_*).
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from mongoengine import connect, get_db

TOP_LEVEL_RENAMES = {
    'subscription_plans': {
        'mp_preapproval_plan_id': 'provider_plan_id',
    },
    'subscriptions': {
        'mp_subscription_id': 'provider_subscription_id',
        'mp_payer_id': 'provider_customer_id',
        'mp_preapproval_plan_id': 'provider_plan_id',
        'mp_status': 'provider_status',
    },
}


def migrar(dry_run: bool = False):
    connect(host=os.environ.get('MONGODB_URI'))
    db = get_db()

    for collection, renames in TOP_LEVEL_RENAMES.items():
        col = db[collection]
        for old, new in renames.items():
            count = col.count_documents({old: {'$exists': True}})
            print(f"{collection}.{old} -> {new}: {count} documento(s)")
            if count and not dry_run:
                res = col.update_many(
                    {old: {'$exists': True}},
                    {'$rename': {old: new}},
                )
                print(f"    modificados: {res.modified_count}")

    # payment_history é lista de subdocumentos: $rename com posicional $[] renomeia
    # o campo em todos os elementos do array de uma vez.
    subs = db['subscriptions']
    ph_filter = {'payment_history.mp_authorized_payment_id': {'$exists': True}}
    ph_count = subs.count_documents(ph_filter)
    print(f"subscriptions.payment_history[].mp_authorized_payment_id -> provider_payment_id: {ph_count} documento(s)")
    if ph_count and not dry_run:
        res = subs.update_many(
            ph_filter,
            {'$rename': {'payment_history.$[].mp_authorized_payment_id': 'payment_history.$[].provider_payment_id'}},
        )
        print(f"    modificados: {res.modified_count}")

    print("dry-run — nada foi alterado" if dry_run else "migração concluída")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='apenas mostra o que seria feito')
    args = parser.parse_args()
    migrar(dry_run=args.dry_run)
