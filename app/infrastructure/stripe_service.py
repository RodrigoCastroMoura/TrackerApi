import json
import logging
from typing import Optional, Dict, Any

import stripe

from config import Config

logger = logging.getLogger(__name__)

stripe.api_key = Config.STRIPE_SECRET_KEY

# Fixa a versão da API se STRIPE_API_VERSION estiver configurado, para o comportamento
# não mudar sozinho quando a lib `stripe` for atualizada. Sem a env var, usa a versão
# default da conta/lib (com o fallback de compatibilidade abaixo em create_subscription_elements).
if getattr(Config, 'STRIPE_API_VERSION', None):
    stripe.api_version = Config.STRIPE_API_VERSION


# Caminhos de expand para pegar o segredo do pagamento da 1ª fatura. O
# `latest_invoice.payment_intent` só existe na API < 2025-03; nas novas ele é
# recusado no expand -> _stripe_call_with_expand() derruba os caminhos inválidos e refaz.
_SECRET_EXPAND = [
    'latest_invoice.confirmation_secret',  # API 2025-03+ (substitui latest_invoice.payment_intent)
    'latest_invoice.payment_intent',       # compat API antiga
    'pending_setup_intent',                # planos com trial (free_days > 0)
]


def _stripe_call_with_expand(fn, *args, expand=None, **kwargs):
    """Chama fn(*args, expand=[...], **kwargs) e, se a Stripe recusar um caminho de
    expand (mudança de versão da API), remove o caminho citado no erro e tenta de
    novo, até sobrar uma lista válida."""
    paths = list(expand or [])
    while True:
        try:
            return fn(*args, expand=paths, **kwargs) if paths else fn(*args, **kwargs)
        except Exception as e:
            # stripe.error.InvalidRequestError não é referenciado direto para não
            # depender do layout de exceptions de uma versão específica da lib.
            msg = str(getattr(e, 'user_message', None) or e)
            is_invalid_request = 'InvalidRequest' in type(e).__name__ or getattr(e, 'code', None) == 'parameter_unknown'
            bad = next((p for p in paths if p in msg), None)
            if not bad or not is_invalid_request or 'expand' not in msg.lower():
                raise
            paths = [p for p in paths if p != bad]
            logger.warning(f"[STRIPE] expand '{bad}' recusado pela API; refazendo sem ele")


def _client_secret_from_subscription(subscription) -> Optional[str]:
    """Extrai o client_secret de uma Subscription recém-criada/modificada, cobrindo:
      * API 2025-03+  -> latest_invoice.confirmation_secret.client_secret
      * API antiga     -> latest_invoice.payment_intent.client_secret
      * trial / 1ª fatura R$0 -> pending_setup_intent.client_secret
    """
    client_secret = None
    invoice = getattr(subscription, 'latest_invoice', None)
    if invoice is not None:
        conf = getattr(invoice, 'confirmation_secret', None)          # API nova
        if conf is not None:
            client_secret = getattr(conf, 'client_secret', None)
        if not client_secret:                                         # API antiga
            pi = getattr(invoice, 'payment_intent', None)
            if pi is not None:
                client_secret = getattr(pi, 'client_secret', None)
    if not client_secret:
        psi = getattr(subscription, 'pending_setup_intent', None)     # trial / R$0
        if psi is not None:
            client_secret = getattr(psi, 'client_secret', None)
    return client_secret


def _err(message: str, status: int = 502) -> Dict[str, Any]:
    """Mesmo contrato de erro do PagarmeService: {'error': True, 'message', 'status'}."""
    return {'error': True, 'message': message, 'status': status}


def _as_dict(obj) -> Dict[str, Any]:
    """Converte um StripeObject (que no stripe-python 15 NÃO é dict e não suporta
    .get()) em um dict Python puro e aninhado, para o resto do código usar .get()
    normalmente."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(str(obj))
    except (ValueError, TypeError):
        try:
            return dict(obj)
        except Exception:
            return {}


class StripeService:
    """Serviço de assinatura recorrente via Stripe (substitui o Mercado Pago).

    Fluxos suportados:
      * Checkout hospedado (mode=subscription) -> devolve a URL para enviar por
        WhatsApp/e-mail. A Subscription na Stripe só nasce quando o cliente conclui
        o pagamento; o webhook checkout.session.completed correlaciona pelo
        metadata.local_subscription_id.
      * PaymentIntent/Elements -> cria a Subscription já (estado incomplete) e
        devolve o client_secret para a tela nativa do app confirmar o cartão.

    Convenção de retorno (igual ao PagarmeService):
      * dict normalizado em caso de sucesso;
      * {'error': True, 'message': ..., 'status': ...} em erro da API da Stripe;
      * None quando a chave não está configurada.
    """

    # ---------------------------------------------------------------- infra ---

    @staticmethod
    def _ready() -> bool:
        if not Config.STRIPE_SECRET_KEY:
            logger.error("STRIPE_SECRET_KEY not configured")
            return False
        # stripe.api_key é setado no import; reforça caso a config mude em runtime.
        stripe.api_key = Config.STRIPE_SECRET_KEY
        return True

    @staticmethod
    def _message(exc: Exception) -> str:
        return getattr(exc, 'user_message', None) or str(exc)

    # ---------------------------------------------------------------- plans ---

    @staticmethod
    def create_plan(
        name: str,
        amount_cents: int,
        interval: str = 'month',
        interval_count: int = 1,
        trial_period_days: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Cria um Product + Price recorrente. Retorna {'plan_id': price_id} (o
        price_... é guardado em SubscriptionPlan.provider_plan_id) ou {'error': ...}.

        trial_period_days é aceito só para simetria com os outros provedores; na
        Stripe o teste grátis é aplicado por assinatura (subscription_data), não no
        Price — então aqui ele é ignorado."""
        if not StripeService._ready():
            return None
        try:
            product = stripe.Product.create(name=name)
            price = stripe.Price.create(
                product=product.id,
                unit_amount=int(amount_cents),
                currency='brl',
                recurring={'interval': interval, 'interval_count': int(interval_count)},
            )
            logger.info(f"[STRIPE] created price {price.id} (product {product.id}) for '{name}'")
            return {'plan_id': price.id, 'product_id': product.id}
        except Exception as e:
            logger.error(f"Error creating Stripe price for '{name}': {StripeService._message(e)}")
            return _err(StripeService._message(e))

    # --------------------------------------------------------- checkout link ---

    @staticmethod
    def create_checkout_subscription(
        price_id: str,
        customer_email: str,
        local_subscription_id: str,
        success_url: str,
        cancel_url: str,
        trial_period_days: int = 0,
        metadata: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /v1/checkout/sessions (mode=subscription). Devolve a URL hospedada
        de checkout. A subscription só existe depois que o cliente paga; o webhook
        checkout.session.completed traz subscription + metadata."""
        if not StripeService._ready():
            return None
        try:
            meta = dict(metadata or {})
            meta['local_subscription_id'] = str(local_subscription_id)

            subscription_data: Dict[str, Any] = {'metadata': meta}
            if trial_period_days and trial_period_days > 0:
                subscription_data['trial_period_days'] = int(trial_period_days)

            session = stripe.checkout.Session.create(
                mode='subscription',
                line_items=[{'price': price_id, 'quantity': 1}],
                customer_email=customer_email,
                client_reference_id=str(local_subscription_id),
                success_url=success_url,
                cancel_url=cancel_url,
                subscription_data=subscription_data,
                metadata=meta,
            )
            logger.info(
                f"[STRIPE] checkout session {session.id} for local sub {local_subscription_id}"
            )
            return {'session_id': session.id, 'url': session.url}
        except Exception as e:
            logger.error(f"Error creating Stripe checkout session: {StripeService._message(e)}")
            return _err(StripeService._message(e))

    # ------------------------------------------------------ elements / PI ---

    @staticmethod
    def create_subscription_elements(
        price_id: str,
        customer_email: str,
        local_subscription_id: str,
        trial_period_days: int = 0,
        metadata: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Cria Customer + Subscription (payment_behavior=default_incomplete) e
        devolve o client_secret para a tela nativa confirmar o pagamento com o
        Stripe Elements / PaymentSheet."""
        if not StripeService._ready():
            return None
        try:
            meta = dict(metadata or {})
            meta['local_subscription_id'] = str(local_subscription_id)

            customer = stripe.Customer.create(email=customer_email, metadata=meta)

            params: Dict[str, Any] = {
                'customer': customer.id,
                'items': [{'price': price_id}],
                'payment_behavior': 'default_incomplete',
                'payment_settings': {'save_default_payment_method': 'on_subscription'},
                'metadata': meta,
            }
            if trial_period_days and trial_period_days > 0:
                params['trial_period_days'] = int(trial_period_days)

            subscription = _stripe_call_with_expand(
                stripe.Subscription.create, expand=list(_SECRET_EXPAND), **params
            )

            client_secret = _client_secret_from_subscription(subscription)
            if not client_secret:
                logger.error(
                    f"[STRIPE] subscription {subscription.id} sem client_secret "
                    f"(invoice/status={getattr(getattr(subscription, 'latest_invoice', None), 'status', None)})"
                )
                return _err('Stripe não retornou o client_secret da assinatura')

            logger.info(
                f"[STRIPE] subscription {subscription.id} (elements) for local sub {local_subscription_id}"
            )
            return {
                'subscription_id': subscription.id,
                'client_secret': client_secret,
                'customer_id': customer.id,
                'publishable_key': Config.STRIPE_PUBLISHABLE_KEY,
                'status': subscription.status,
            }
        except Exception as e:
            logger.error(f"Error creating Stripe subscription (elements): {StripeService._message(e)}")
            return _err(StripeService._message(e))

    # -------------------------------------------------------- subscriptions ---

    @staticmethod
    def _normalize_subscription(sub) -> Dict[str, Any]:
        sub = _as_dict(sub)
        items = (sub.get('items') or {}).get('data') or []
        first = items[0] if items else {}
        return {
            'id': sub.get('id'),
            'status': sub.get('status'),
            'customer_id': sub.get('customer'),
            'price_id': (first.get('price') or {}).get('id'),
            'item_id': first.get('id'),
            'current_period_end': sub.get('current_period_end'),  # epoch seconds
            'cancel_at_period_end': sub.get('cancel_at_period_end'),
        }

    @staticmethod
    def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
        """GET /v1/subscriptions/{id} — dados normalizados da assinatura."""
        if not StripeService._ready():
            return None
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            return StripeService._normalize_subscription(sub)
        except Exception as e:
            logger.error(f"Error fetching Stripe subscription {subscription_id}: {StripeService._message(e)}")
            return None

    @staticmethod
    def update_subscription_price(
        subscription_id: str,
        new_price_id: str,
        proration_behavior: str = 'none',
    ) -> Optional[Dict[str, Any]]:
        """Troca o Price da assinatura sem re-autorização do cliente
        (PUT no item da subscription)."""
        if not StripeService._ready():
            return None
        try:
            sub = _as_dict(stripe.Subscription.retrieve(subscription_id))
            items = (sub.get('items') or {}).get('data') or []
            if not items:
                logger.error(f"Stripe subscription {subscription_id} has no items to update")
                return None
            item_id = items[0]['id']
            updated = stripe.Subscription.modify(
                subscription_id,
                items=[{'id': item_id, 'price': new_price_id}],
                proration_behavior=proration_behavior,
            )
            return StripeService._normalize_subscription(updated)
        except Exception as e:
            logger.error(f"Error updating Stripe subscription {subscription_id}: {StripeService._message(e)}")
            return None

    @staticmethod
    def change_plan_elements(
        subscription_id: str,
        new_price_id: str,
        proration_behavior: str = 'create_prorations',
    ) -> Optional[Dict[str, Any]]:
        """Troca o Price da assinatura no fluxo nativo (Elements / PaymentSheet) e
        devolve o client_secret para o app confirmar a cobrança do novo plano quando
        houver valor a pagar (upgrade / proração). Se a Stripe conseguir cobrar de
        imediato (mesmo valor, downgrade sem saldo, cartão já salvo) a assinatura já
        volta 'active' e client_secret vem None.

        Retorno: {'subscription_id', 'status', 'client_secret', 'requires_action'} ou
        {'error': True, ...}."""
        if not StripeService._ready():
            return None
        try:
            sub = _as_dict(stripe.Subscription.retrieve(subscription_id))
            items = (sub.get('items') or {}).get('data') or []
            if not items:
                logger.error(f"Stripe subscription {subscription_id} has no items to update")
                return _err('Assinatura da Stripe sem itens para atualizar', 400)
            item_id = items[0]['id']

            updated = _stripe_call_with_expand(
                stripe.Subscription.modify,
                subscription_id,
                expand=list(_SECRET_EXPAND),
                items=[{'id': item_id, 'price': new_price_id}],
                proration_behavior=proration_behavior,
                payment_behavior='default_incomplete',
            )

            client_secret = _client_secret_from_subscription(updated)
            status = getattr(updated, 'status', None)
            logger.info(
                f"[STRIPE] subscription {subscription_id} plan changed to {new_price_id} "
                f"(status={status}, requires_action={bool(client_secret)})"
            )
            return {
                'subscription_id': getattr(updated, 'id', subscription_id),
                'status': status,
                'client_secret': client_secret,
                'requires_action': bool(client_secret),
            }
        except Exception as e:
            logger.error(
                f"Error changing plan for Stripe subscription {subscription_id}: {StripeService._message(e)}"
            )
            return _err(StripeService._message(e))

    @staticmethod
    def cancel_subscription(subscription_id: str) -> bool:
        """DELETE /v1/subscriptions/{id} — só retorna True se a Stripe confirmar
        status 'canceled' (mesma cautela dos outros services)."""
        if not StripeService._ready():
            return False
        try:
            result = _as_dict(stripe.Subscription.cancel(subscription_id))
            if result.get('status') != 'canceled':
                logger.error(
                    f"Stripe did not confirm cancellation for {subscription_id}: "
                    f"got status={result.get('status')!r}"
                )
                return False
            logger.info(f"Canceled Stripe subscription: {subscription_id}")
            return True
        except Exception as e:
            msg = StripeService._message(e)
            # Assinatura já cancelada / inexistente -> tratamos como sucesso idempotente.
            if 'No such subscription' in msg or 'already canceled' in msg:
                logger.warning(f"Stripe cancel {subscription_id}: {msg} (tratado como já cancelada)")
                return True
            logger.error(f"Error canceling Stripe subscription {subscription_id}: {msg}")
            return False

    # -------------------------------------------------------------- invoices ---

    @staticmethod
    def get_invoice(invoice_id: str) -> Optional[Dict[str, Any]]:
        """GET /v1/invoices/{id} — dados normalizados de uma fatura recorrente."""
        if not StripeService._ready():
            return None
        try:
            inv = _as_dict(stripe.Invoice.retrieve(invoice_id))
            paid_at = (inv.get('status_transitions') or {}).get('paid_at')
            return {
                'id': inv.get('id'),
                'status': inv.get('status'),
                'amount': (inv.get('amount_paid') or 0) / 100.0,
                'currency': (inv.get('currency') or 'brl').upper(),
                'paid_at': paid_at,  # epoch seconds
                'subscription_id': inv.get('subscription'),
            }
        except Exception as e:
            logger.error(f"Error fetching Stripe invoice {invoice_id}: {StripeService._message(e)}")
            return None

    # -------------------------------------------------------------- webhook ---

    @staticmethod
    def construct_webhook_event(payload: bytes, sig_header: str) -> Optional[Dict[str, Any]]:
        """Valida a assinatura do header Stripe-Signature e devolve o evento como
        dict Python puro e aninhado (o Event do stripe-python 15 não suporta .get()).
        Retorna None se o secret não estiver configurado ou a assinatura for inválida."""
        secret = Config.STRIPE_WEBHOOK_SECRET
        if not secret:
            logger.warning("STRIPE_WEBHOOK_SECRET not configured - rejecting webhook")
            return None
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            return _as_dict(event)
        except Exception as e:
            logger.error(f"Invalid Stripe webhook signature: {StripeService._message(e)}")
            return None
