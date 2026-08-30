from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, SubscriptionPayment, Customer, period_days_for_frequency
from app.infrastructure.stripe_service import StripeService
from datetime import datetime, timedelta, timezone
import logging
import hmac
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - Stripe e Pagar.me')


def _epoch_to_dt(value):
    """Converte epoch seconds (int) da Stripe para datetime UTC, ou None."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _find_stripe_subscription(obj):
    """Localiza a Subscription local de um objeto de evento da Stripe: primeiro
    pelo provider_subscription_id (sub_... já preenchido), senão pelo
    metadata.local_subscription_id / client_reference_id enviado no checkout."""
    sub_id = None
    if str(obj.get('object')) == 'subscription':
        sub_id = obj.get('id')
    sub_id = sub_id or obj.get('subscription')
    if sub_id:
        found = Subscription.objects(provider_subscription_id=str(sub_id), visible=True).first()
        if found:
            return found

    metadata = obj.get('metadata') or {}
    local_id = (
        metadata.get('local_subscription_id')
        or obj.get('client_reference_id')
    )
    if local_id:
        try:
            return Subscription.objects(id=local_id, visible=True).first()
        except Exception:
            return None
    return None


@api.route('/stripe')
class StripeWebhook(Resource):

    @api.doc('stripe_webhook', description='Webhook da Stripe para assinaturas e faturas recorrentes')
    def post(self):
        """Processar notificações da Stripe (checkout.session.completed,
        customer.subscription.*, invoice.*, charge.refunded)"""
        try:
            event = StripeService.construct_webhook_event(
                request.data, request.headers.get('Stripe-Signature', '')
            )
            if not event:
                return {'message': 'Invalid signature'}, 401

            event_type = event.get('type')
            obj = (event.get('data') or {}).get('object') or {}

            logger.info(f"Received Stripe webhook - Event: {event_type}, ID: {obj.get('id')}")

            handled = (
                event_type == 'checkout.session.completed'
                or event_type.startswith('customer.subscription.')
                or event_type.startswith('invoice.')
                or event_type == 'charge.refunded'
            )
            if not handled:
                logger.info(f"Stripe webhook event ignored: {event_type}")
                return {'message': 'Webhook recebido'}, 200

            subscription = _find_stripe_subscription(obj)
            if not subscription:
                logger.warning(
                    f"Stripe subscription ainda não persistida localmente para evento {event_type}; "
                    f"solicitando reenvio"
                )
                return {'message': 'Assinatura ainda não disponível localmente'}, 503

            customer = subscription.customer_id
            now = datetime.now(timezone.utc)
            period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)

            if event_type == 'checkout.session.completed':
                if obj.get('mode') != 'subscription':
                    return {'message': 'Webhook recebido'}, 200

                stripe_sub_id = obj.get('subscription')
                subscription.provider_subscription_id = str(stripe_sub_id or subscription.provider_subscription_id)
                subscription.provider_customer_id = obj.get('customer') or subscription.provider_customer_id

                info = StripeService.get_subscription(str(stripe_sub_id)) if stripe_sub_id else None
                period_end = _epoch_to_dt(info.get('current_period_end')) if info else None
                next_payment_date = period_end or (now + timedelta(days=period_days))

                subscription.status = 'active'
                subscription.provider_status = 'succeeded'
                if not subscription.current_period_start:
                    subscription.current_period_start = now
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = next_payment_date + timedelta(days=Config.STRIPE_DAYS_TO_EXPIRE)
                subscription.access_blocked = False
                subscription.failure_message = None
                subscription.canceled_at = None
                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Stripe checkout completed for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}"
                )

            elif event_type in ('customer.subscription.created', 'customer.subscription.updated'):
                sub_status = obj.get('status')
                if not subscription.provider_subscription_id:
                    subscription.provider_subscription_id = str(obj.get('id'))
                subscription.provider_customer_id = obj.get('customer') or subscription.provider_customer_id

                if sub_status in ('active', 'trialing'):
                    period_end = _epoch_to_dt(obj.get('current_period_end'))
                    next_payment_date = period_end or (now + timedelta(days=period_days))
                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    if not subscription.current_period_start:
                        subscription.current_period_start = now
                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = next_payment_date + timedelta(
                        days=Config.STRIPE_DAYS_TO_EXPIRE
                    )
                    subscription.access_blocked = False
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()
                elif sub_status in ('past_due', 'unpaid'):
                    subscription.status = 'pendingPayment'
                    subscription.provider_status = 'failed'
                    subscription.save()
                elif sub_status in ('canceled', 'incomplete_expired'):
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()
                else:
                    logger.info(
                        f"Stripe subscription event {event_type} with status {sub_status} - no local change"
                    )

            elif event_type == 'customer.subscription.deleted':
                subscription.status = 'canceled'
                subscription.provider_status = 'canceled'
                subscription.canceled_at = now
                if customer.require_payment_method is False:
                    customer.can_change_plan = True
                subscription.save()
                customer.save()

            elif event_type == 'invoice.paid':
                invoice_id = str(obj.get('id'))
                already_registered = any(
                    p.provider_payment_id == invoice_id
                    for p in subscription.payment_history
                )
                line_items = ((obj.get('lines') or {}).get('data') or [])
                line_period_end = (line_items[0].get('period') or {}).get('end') if line_items else None
                period_end = _epoch_to_dt(obj.get('period_end') or line_period_end)
                next_payment_date = period_end or (now + timedelta(days=period_days))
                grace_period_end = next_payment_date + timedelta(days=Config.STRIPE_DAYS_TO_EXPIRE)

                subscription.status = 'active'
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = grace_period_end
                subscription.provider_status = 'succeeded'
                subscription.failure_message = None
                subscription.canceled_at = None
                if not subscription.provider_subscription_id and obj.get('subscription'):
                    subscription.provider_subscription_id = str(obj.get('subscription'))

                if not already_registered:
                    amount = obj.get('amount_paid')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=invoice_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=(obj.get('currency') or 'BRL').upper(),
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=next_payment_date,
                    ))

                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Stripe invoice paid for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}, grace ends: {grace_period_end.date()}"
                )

            elif event_type in ('invoice.payment_failed', 'invoice.marked_uncollectible'):
                invoice_id = str(obj.get('id'))
                already_registered = any(
                    p.provider_payment_id == invoice_id
                    for p in subscription.payment_history
                )
                subscription.provider_status = 'failed'
                subscription.status = 'pendingPayment'
                subscription.failure_message = f'Cobrança recorrente rejeitada (evento: {event_type})'
                if not already_registered:
                    amount = obj.get('amount_due')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=invoice_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=(obj.get('currency') or 'BRL').upper(),
                        status='rejected',
                        paid_at=now,
                    ))
                subscription.save()
                logger.warning(
                    f"Stripe invoice failed for subscription {subscription.id} (event: {event_type})"
                )

            elif event_type == 'charge.refunded':
                subscription.provider_status = 'refunded'
                subscription.refunded_at = now
                subscription.save()
                logger.info(f"Stripe charge refunded for subscription {subscription.id}")

            else:
                logger.info(f"Stripe webhook event not handled: {event_type}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing Stripe webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500


def _find_pagarme_subscription(data):
    """Localiza a Subscription local de um evento do Pagar.me: primeiro pelo
    provider_subscription_id (id sub_... já preenchido), senão pelo
    metadata.local_subscription_id enviado no payment link."""
    sub_id = (
        (data.get('subscription') or {}).get('id')
        or (data.get('invoice') or {}).get('subscription_id')
        or (data.get('id') if str(data.get('id', '')).startswith('sub_') else None)
    )
    if sub_id:
        found = Subscription.objects(provider_subscription_id=str(sub_id), visible=True).first()
        if found:
            return found

    metadata = data.get('metadata') or {}
    local_id = metadata.get('local_subscription_id')
    if not local_id:
        # charges trazem o metadata no objeto de subscription aninhado
        local_id = ((data.get('subscription') or {}).get('metadata') or {}).get('local_subscription_id')
    if local_id:
        try:
            return Subscription.objects(id=local_id, visible=True).first()
        except Exception:
            return None
    return None


@api.route('/pagarme')
class PagarmeWebhook(Resource):

    @api.doc('pagarme_webhook', description='Webhook do Pagar.me (Stone) para assinaturas e cobranças')
    def post(self):
        """Processar notificações do Pagar.me (subscription.*, charge.*)"""
        try:
            # Auth: o Pagar.me envia as credenciais Basic Auth configuradas no dashboard.
            user = Config.PAGARME_WEBHOOK_USER
            password = Config.PAGARME_WEBHOOK_PASSWORD
            if user or password:
                auth = request.authorization
                if not auth or not hmac.compare_digest(auth.username or '', user or '') \
                        or not hmac.compare_digest(auth.password or '', password or ''):
                    logger.error("Invalid Pagar.me webhook credentials - rejecting request")
                    return {'message': 'Invalid credentials'}, 401
            else:
                logger.warning("PAGARME_WEBHOOK_USER/PASSWORD not configured - processing without validation")

            body = request.get_json() or {}
            event = body.get('type')
            data = body.get('data') or {}

            logger.info(f"Received Pagar.me webhook - Event: {event}, ID: {data.get('id')}")

            if not event:
                return {'message': 'Invalid webhook data'}, 400

            # Só tratamos eventos de assinatura/cobrança
            if not (event.startswith('subscription.') or event.startswith('charge.')):
                logger.info(f"Pagar.me webhook event ignored: {event}")
                return {'message': 'Webhook recebido'}, 200

            subscription = _find_pagarme_subscription(data)
            if not subscription:
                logger.warning(
                    f"Pagar.me subscription ainda não persistida localmente para evento {event}; solicitando reenvio"
                )
                return {'message': 'Assinatura ainda não disponível localmente'}, 503

            customer = subscription.customer_id
            now = datetime.now(timezone.utc)
            period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)

            if event in ('subscription.created', 'subscription.activated', 'subscription.updated'):
                sub_status = data.get('status')
                if sub_status in ('active', 'trialing', 'trial'):
                    subscription.provider_subscription_id = str(data.get('id') or subscription.provider_subscription_id)
                    subscription.provider_customer_id = (data.get('customer') or {}).get('id') or subscription.provider_customer_id
                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    if not subscription.current_period_start:
                        subscription.current_period_start = now
                    subscription.current_period_end = now + timedelta(days=period_days)
                    subscription.grace_period_end = subscription.current_period_end + timedelta(
                        days=Config.PAGARME_DAYS_TO_EXPIRE
                    )
                    subscription.access_blocked = False
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()
                elif sub_status in ('canceled', 'expired'):
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()
                else:
                    logger.info(f"Pagar.me subscription event {event} with status {sub_status} - no local change")

            elif event == 'subscription.canceled':
                subscription.status = 'canceled'
                subscription.provider_status = 'canceled'
                subscription.canceled_at = now
                if customer.require_payment_method is False:
                    customer.can_change_plan = True
                subscription.save()
                customer.save()

            elif event in ('charge.paid', 'invoice.paid'):
                charge_id = str(data.get('id'))
                already_registered = any(
                    p.provider_payment_id == charge_id
                    for p in subscription.payment_history
                )
                next_payment_date = now + timedelta(days=period_days)
                grace_period_end = next_payment_date + timedelta(days=Config.PAGARME_DAYS_TO_EXPIRE)

                subscription.status = 'active'
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = grace_period_end
                subscription.provider_status = 'succeeded'
                subscription.failure_message = None
                subscription.canceled_at = None
                if not subscription.provider_subscription_id:
                    sub_ref = (data.get('subscription') or {}).get('id')
                    if sub_ref:
                        subscription.provider_subscription_id = str(sub_ref)

                if not already_registered:
                    amount = data.get('amount')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=charge_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=data.get('currency', 'BRL'),
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=next_payment_date,
                    ))

                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Pagar.me charge paid for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}, grace ends: {grace_period_end.date()}"
                )

            elif event in ('charge.payment_failed', 'invoice.payment_failed'):
                charge_id = str(data.get('id'))
                already_registered = any(
                    p.provider_payment_id == charge_id
                    for p in subscription.payment_history
                )
                subscription.provider_status = 'failed'
                subscription.status = 'pendingPayment'
                subscription.failure_message = f'Cobrança recorrente rejeitada (evento: {event})'
                if not already_registered:
                    amount = data.get('amount')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=charge_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=data.get('currency', 'BRL'),
                        status='rejected',
                        paid_at=now,
                    ))
                subscription.save()
                logger.warning(f"Pagar.me charge failed for subscription {subscription.id} (event: {event})")

            elif event in ('charge.refunded', 'charge.chargedback'):
                subscription.provider_status = 'refunded'
                subscription.refunded_at = now
                subscription.save()
                logger.info(f"Pagar.me charge refunded for subscription {subscription.id}")

            else:
                logger.info(f"Pagar.me webhook event not handled: {event}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing Pagar.me webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
