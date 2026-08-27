import logging
from typing import Optional, Dict, Any, Iterable

import requests

from config import Config

logger = logging.getLogger(__name__)

PAGARME_SECRET_KEY = Config.PAGARME_SECRET_KEY
PAGARME_API_URL = (Config.PAGARME_API_URL or 'https://api.pagar.me/core/v5').rstrip('/')

# Cabeçalho exigido pela documentação de skill do Pagar.me.
_USER_AGENT = 'pagarme-skill-generated/1.0'
_TIMEOUT = 20


class PagarmeService:
    """Serviço de assinatura recorrente via Pagar.me (Stone).

    Espelha a interface do MercadoPagoService, mas usa a API core v5 do Pagar.me
    (HTTP Basic Auth com a secret key como usuário e senha vazia). Fluxo de
    autorização do cliente é via Payment Link hospedado (type=subscription).
    """

    # ---------------------------------------------------------------- infra ---

    @staticmethod
    def _request(method: str, path: str, body: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Chama a API do Pagar.me. Retorna o JSON de resposta em caso de sucesso,
        ou {'error': True, 'message': ..., 'status': ...} em caso de erro 4xx/5xx —
        mesmo contrato do MercadoPagoService.create_pending_subscription."""
        if not PAGARME_SECRET_KEY:
            logger.error("PAGARME_SECRET_KEY not configured")
            return None

        url = f"{PAGARME_API_URL}{path}"
        headers = {
            'User-Agent': _USER_AGENT,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        try:
            logger.info(f"[PAGARME REQUEST] {method} {path} | body={body}")
            resp = requests.request(
                method,
                url,
                json=body,
                headers=headers,
                auth=(PAGARME_SECRET_KEY, ''),
                timeout=_TIMEOUT,
            )

            try:
                data = resp.json() if resp.content else {}
            except ValueError:
                data = {}

            logger.info(
                f"[PAGARME RESPONSE] {method} {path} | status={resp.status_code} | body={data}"
            )

            if resp.status_code >= 400:
                message = data.get('message') if isinstance(data, dict) else None
                if not message and isinstance(data, dict):
                    errors = data.get('errors')
                    if isinstance(errors, dict):
                        message = '; '.join(
                            f"{k}: {', '.join(v) if isinstance(v, list) else v}"
                            for k, v in errors.items()
                        )
                return {
                    'error': True,
                    'message': message or f'Pagar.me retornou status {resp.status_code}',
                    'status': resp.status_code,
                }

            return data if isinstance(data, dict) else {'data': data}

        except requests.RequestException as e:
            logger.error(f"Error calling Pagar.me {method} {path}: {str(e)}")
            return None

    # ---------------------------------------------------------------- plans ---

    @staticmethod
    def create_plan(
        name: str,
        amount_cents: int,
        interval: str = 'month',
        interval_count: int = 1,
        payment_methods: Iterable[str] = ('credit_card',),
        trial_period_days: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """POST /plans — cria o plano recorrente. Retorna {'plan_id': ...} ou
        {'error': True, ...}."""
        body = {
            'name': name,
            'currency': 'BRL',
            'interval': interval,
            'interval_count': interval_count,
            'billing_type': 'prepaid',
            'payment_methods': list(payment_methods),
            'items': [
                {
                    'name': name,
                    'quantity': 1,
                    'pricing_scheme': {'scheme_type': 'unit', 'price': int(amount_cents)},
                }
            ],
        }
        if trial_period_days and trial_period_days > 0:
            body['trial_period_days'] = int(trial_period_days)

        resp = PagarmeService._request('POST', '/plans', body)
        if not resp or resp.get('error'):
            return resp
        return {'plan_id': resp.get('id')}

    # ------------------------------------------------------- payment links ---

    @staticmethod
    def create_subscription_payment_link(
        plan_id: str,
        local_subscription_id: str,
        customer_id: Optional[str] = None,
        company_id: Optional[str] = None,
        start_in: int = 1,
        expires_in: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /paymentlinks (type=subscription) — devolve a URL hospedada de
        checkout. A subscription no Pagar.me só nasce quando o cliente conclui o
        pagamento; o webhook subscription.created correlaciona pelo metadata."""
        body = {
            'type': 'subscription',
            'payment_settings': {'accepted_payment_methods': ['credit_card']},
            'cart_settings': {
                'recurrences': [{'plan_id': plan_id, 'start_in': start_in}]
            },
            'metadata': {
                'local_subscription_id': local_subscription_id,
                'customer_id': customer_id,
                'company_id': company_id,
            },
            'max_paid_sessions': 1,
        }
        if expires_in:
            body['expires_in'] = int(expires_in)

        resp = PagarmeService._request('POST', '/paymentlinks', body)
        if not resp or resp.get('error'):
            return resp
        return {'paymentlink_id': resp.get('id'), 'url': resp.get('url')}

    # -------------------------------------------------------- subscriptions ---

    @staticmethod
    def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
        """GET /subscriptions/{id} — dados normalizados da assinatura."""
        resp = PagarmeService._request('GET', f'/subscriptions/{subscription_id}')
        if not resp or resp.get('error'):
            return None

        customer = resp.get('customer') or {}
        current_cycle = resp.get('current_cycle') or {}
        return {
            'id': resp.get('id'),
            'status': resp.get('status'),
            'customer_id': customer.get('id'),
            'plan_id': (resp.get('plan') or {}).get('id'),
            'current_cycle_end': current_cycle.get('end_at'),
            'next_billing_at': resp.get('next_billing_at'),
            'items': resp.get('items') or [],
        }

    @staticmethod
    def get_subscription_items(subscription_id: str) -> Optional[list]:
        """GET /subscriptions/{id}/items — usado para descobrir o item_id antes de
        atualizar o preço."""
        resp = PagarmeService._request('GET', f'/subscriptions/{subscription_id}/items')
        if not resp or resp.get('error'):
            return None
        return resp.get('data') or resp.get('items') or []

    @staticmethod
    def update_subscription_pricing(
        subscription_id: str,
        item_id: str,
        amount_cents: int,
        name: str,
    ) -> Optional[Dict[str, Any]]:
        """PUT /subscriptions/{id}/items/{item_id} — troca o valor da assinatura
        sem re-autorização do cliente."""
        body = {
            'name': name,
            'quantity': 1,
            'pricing_scheme': {'scheme_type': 'unit', 'price': int(amount_cents)},
        }
        resp = PagarmeService._request(
            'PUT', f'/subscriptions/{subscription_id}/items/{item_id}', body
        )
        if not resp or resp.get('error'):
            return None
        return resp

    @staticmethod
    def cancel_subscription(subscription_id: str) -> bool:
        """DELETE /subscriptions/{id} — só retorna True se o Pagar.me confirmar
        status 'canceled' (mesma cautela do MercadoPagoService.cancel_subscription)."""
        resp = PagarmeService._request('DELETE', f'/subscriptions/{subscription_id}')
        if not resp or resp.get('error'):
            logger.error(f"Pagar.me cancel error for {subscription_id}: {resp}")
            return False

        result_status = resp.get('status')
        if result_status != 'canceled':
            logger.error(
                f"Pagar.me did not confirm cancellation for {subscription_id}: "
                f"got status={result_status!r}"
            )
            return False

        logger.info(f"Canceled Pagar.me subscription: {subscription_id}")
        return True

    # -------------------------------------------------------------- charges ---

    @staticmethod
    def get_charge(charge_id: str) -> Optional[Dict[str, Any]]:
        """GET /charges/{id} — dados normalizados de uma cobrança."""
        resp = PagarmeService._request('GET', f'/charges/{charge_id}')
        if not resp or resp.get('error'):
            return None

        return {
            'id': resp.get('id'),
            'status': resp.get('status'),
            'amount': resp.get('amount'),
            'currency': resp.get('currency', 'BRL'),
            'paid_at': resp.get('paid_at'),
            'subscription_id': (resp.get('subscription') or {}).get('id')
            or (resp.get('invoice') or {}).get('subscription_id'),
        }
