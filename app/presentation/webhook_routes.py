from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, SubscriptionPayment, BILLING_CYCLE_PARAMS
from datetime import datetime, timedelta, timezone
import logging
import hmac
import hashlib
import base64
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - AbacatePay')


def validate_abacatepay_signature(raw_body, signature_header, secret):
    """
    Valida a assinatura HMAC-SHA256 do webhook AbacatePay.

    Args:
        raw_body: corpo bruto (bytes) da requisição
        signature_header: valor do header X-Webhook-Signature (base64)
        secret: segredo configurado ao cadastrar o webhook (ABACATEPAY_WEBHOOK_SECRET)

    Returns:
        bool: True se a assinatura é válida
    """
    if not all([raw_body, signature_header, secret]):
        logger.warning("Missing required signature parameters")
        return False

    try:
        expected_signature = base64.b64encode(
            hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).digest()
        ).decode('utf-8')

        is_valid = hmac.compare_digest(expected_signature, signature_header)

        if not is_valid:
            logger.warning("Signature validation failed - potential security threat")

        return is_valid

    except (AttributeError, ValueError) as e:
        logger.error(f"Error validating signature: {str(e)}")
        return False


@api.route('/abacatepay')
class AbacatePayWebhook(Resource):

    @api.doc('abacatepay_webhook', description='Webhook do AbacatePay para processar notificações de assinatura')
    def post(self):
        """Processar notificações do AbacatePay (subscription.*)"""
        try:
            raw_body = request.get_data()
            data = request.get_json(silent=True) or {}

            signature_header = request.headers.get('X-Webhook-Signature', '')
            webhook_secret = Config.ABACATEPAY_WEBHOOK_SECRET

            if not webhook_secret:
                logger.error("ABACATEPAY_WEBHOOK_SECRET not configured - rejecting webhook")
                return {'message': 'Webhook not configured'}, 500

            if not validate_abacatepay_signature(raw_body, signature_header, webhook_secret):
                logger.error("Invalid webhook signature - rejecting request")
                return {'message': 'Invalid signature'}, 401

            event = data.get('event') or data.get('type')
            payload = data.get('data') or {}
            subscription_id = payload.get('id') or payload.get('subscriptionId')

            logger.info(f"Received AbacatePay webhook - Event: {event}, Subscription ID: {subscription_id}")

            if not event or not subscription_id:
                logger.warning("Webhook missing event or subscription ID")
                return {'message': 'Invalid webhook data'}, 400

            subscription = Subscription.objects(
                abacatepay_subscription_id=str(subscription_id),
                visible=True
            ).first()

            if not subscription:
                # A assinatura é sempre criada localmente pela nossa própria chamada
                # à API do AbacatePay (POST /api/subscriptions), que já recebe o
                # abacatepay_subscription_id na resposta síncrona. Se ainda não achamos
                # o registro aqui, o webhook só chegou antes desse save local.
                logger.info(f"Subscription ainda não persistida localmente para AbacatePay ID {subscription_id}; ignorando webhook")
                return {'message': 'Webhook recebido'}, 200

            customer = subscription.customer_id
            now = datetime.now(timezone.utc)
            period_days = BILLING_CYCLE_PARAMS.get(subscription.billing_cycle, BILLING_CYCLE_PARAMS['monthly'])['period_days']

            if event == 'subscription.trial_started':
                subscription.status = 'active'
                subscription.abacatepay_status = 'pending'
                subscription.current_period_start = now
                subscription.current_period_end = now + timedelta(days=period_days)
                subscription.access_blocked = False
                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(f"Trial started for subscription {subscription.id}")

            elif event == 'subscription.completed':
                subscription.status = 'active'
                subscription.abacatepay_status = 'succeeded'
                subscription.current_period_start = now
                subscription.current_period_end = now + timedelta(days=period_days)
                subscription.grace_period_end = subscription.current_period_end + timedelta(days=Config.ABACATEPAY_DAYS_TO_EXPIRE)
                subscription.access_blocked = False
                if not subscription.payment_date:
                    subscription.payment_date = now
                customer.require_payment_method = False
                customer.can_change_plan = False

                already_registered = any(
                    p.abacatepay_billing_id == str(subscription_id)
                    for p in subscription.payment_history
                )
                if not already_registered:
                    subscription.payment_history.append(SubscriptionPayment(
                        abacatepay_billing_id=str(subscription_id),
                        amount=subscription.amount,
                        currency=subscription.currency,
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=subscription.current_period_end,
                    ))

                subscription.save()
                customer.save()
                logger.info(f"Subscription {subscription.id} activated (first payment confirmed)")

            elif event == 'subscription.renewed':
                next_period_end = now + timedelta(days=period_days)
                grace_period_end = next_period_end + timedelta(days=Config.ABACATEPAY_DAYS_TO_EXPIRE)

                subscription.current_period_end = next_period_end
                subscription.grace_period_end = grace_period_end
                subscription.access_blocked = False
                subscription.abacatepay_status = 'succeeded'
                subscription.failure_message = None

                billing_id = payload.get('billingId') or f"{subscription_id}:{now.isoformat()}"
                already_registered = any(
                    p.abacatepay_billing_id == str(billing_id)
                    for p in subscription.payment_history
                )
                if not already_registered:
                    subscription.payment_history.append(SubscriptionPayment(
                        abacatepay_billing_id=str(billing_id),
                        amount=payload.get('amount', subscription.amount * 100) / 100 if payload.get('amount') else subscription.amount,
                        currency=subscription.currency,
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=next_period_end,
                    ))

                subscription.save()
                logger.info(f"Recurring payment confirmed for subscription {subscription.id}. Next period ends: {next_period_end.date()}")

            elif event == 'subscription.cancelled':
                subscription.status = 'canceled'
                subscription.abacatepay_status = 'canceled'
                subscription.canceled_at = now
                subscription.access_blocked = False
                customer.can_change_plan = True
                subscription.save()
                customer.save()
                logger.info(f"Subscription {subscription.id} canceled via AbacatePay webhook")

            else:
                logger.info(f"Unhandled AbacatePay webhook event: {event}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing AbacatePay webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
