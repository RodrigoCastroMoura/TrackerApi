from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, SubscriptionPayment, period_days_for_frequency
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime, timedelta, timezone
import logging
import hmac
import hashlib
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - Mercado Pago')

def validate_mercadopago_signature(x_signature, x_request_id, data_id, secret):
    """
    Validate Mercado Pago webhook signature for security

    Args:
        x_signature: Value from x-signature header (format: "ts=123,v1=abc...")
        x_request_id: Value from x-request-id header
        data_id: Value from data.id query parameter
        secret: Webhook secret key from Mercado Pago dashboard

    Returns:
        bool: True if signature is valid, False otherwise
    """
    if not all([x_signature, x_request_id, data_id, secret]):
        logger.warning("Missing required signature parameters")
        return False

    try:
        # Parseia "ts=...,v1=..." por chave em vez de posição fixa — a MP não
        # garante a ordem das partes, e um espaço após a vírgula (ex: "ts=1, v1=abc")
        # quebraria o split posicional antigo.
        ts_value = None
        received_signature = None
        for part in x_signature.split(','):
            if '=' not in part:
                continue
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip()
            if key == 'ts':
                ts_value = value
            elif key == 'v1':
                received_signature = value

        if not ts_value or not received_signature:
            logger.warning(f"Invalid x-signature format: {x_signature!r}")
            return False

        # A doc da MP recomenda usar data.id em minúsculas na assinatura — o valor
        # recebido na query pode vir com letras maiúsculas em alguns recursos.
        normalized_data_id = str(data_id).lower()
        secret_clean = secret.strip()

        signature_template = f"id:{normalized_data_id};request-id:{x_request_id};ts:{ts_value};"

        calculated_signature = hmac.new(
            secret_clean.encode('utf-8'),
            signature_template.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(calculated_signature, received_signature)

        if not is_valid:
            logger.warning(
                "Signature validation failed - potential security threat | "
                f"manifest={signature_template!r} calculated={calculated_signature} received={received_signature}"
            )

        return is_valid

    except (IndexError, AttributeError, ValueError) as e:
        logger.error(f"Error validating signature: {str(e)}")
        return False


@api.route('/mercadopago')
class MercadoPagoWebhook(Resource):
    
    @api.doc('mercadopago_webhook', description='Webhook do Mercado Pago para processar notificações de pagamento')
    def post(self):
        """Processar notificações do Mercado Pago (payment, subscription)"""
        try:
            data = request.get_json() or {}
            
            is_test_mode = data.get('live_mode') == False
            
            x_signature = request.headers.get('x-signature', '')
            x_request_id = request.headers.get('x-request-id', '')
            data_id = request.args.get('data.id', '')
            
            webhook_secret = Config.MERCADOPAGO_WEBHOOK_SECRET
            
            if is_test_mode:
                logger.info("Test mode webhook received - skipping signature validation")
            elif not webhook_secret:
                logger.warning("MERCADOPAGO_WEBHOOK_SECRET not configured - processing without validation")
            else:
                if x_signature and not validate_mercadopago_signature(x_signature, x_request_id, data_id, webhook_secret):
                    logger.error("Invalid webhook signature - rejecting request")
                    return {'message': 'Invalid signature'}, 401
                
                logger.info("Webhook signature validated successfully")
            
            # MP envia type/topic e data.id tanto no body quanto nos query params
            topic = (data.get('topic') or data.get('type') or data.get('action') or
                     request.args.get('type') or request.args.get('topic'))
            resource_id = (data.get('data', {}).get('id') or
                           request.args.get('data.id') or
                           request.args.get('id'))
            
            logger.info(f"Received Mercado Pago webhook - Topic: {topic}, ID: {resource_id}")
            
            if not topic or not resource_id:
                logger.warning("Webhook missing topic or resource ID")
                return {'message': 'Invalid webhook data'}, 400
            
            if topic in ['preapproval', 'subscription', 'subscription_preapproval']:
                subscription_info = MercadoPagoService.get_subscription_info(str(resource_id))
                
                if not subscription_info:
                    logger.error(f"Failed to get subscription info for ID: {resource_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Find subscription by MP subscription ID
                subscription = Subscription.objects(
                    mp_subscription_id=str(subscription_info['id']),
                    visible=True
                ).first()
                
                if not subscription:
                    # A assinatura é sempre criada localmente pela nossa própria chamada
                    # à API do Mercado Pago (POST/PUT /api/subscriptions), que já recebe o
                    # mp_subscription_id na resposta síncrona. Se ainda não achamos o
                    # registro aqui, o webhook só chegou antes desse save local — não há
                    # nada a criar; o próprio fluxo de criação vai persistir o registro.
                    logger.info(f"Subscription ainda não persistida localmente para MP ID {subscription_info['id']}; ignorando webhook")
                    return {'message': 'Webhook recebido'}, 200

                # Get customer from subscription
                customer = subscription.customer_id
                
                mp_status = subscription_info['status']

                if mp_status == 'authorized':
                    now = datetime.now(timezone.utc)
                    period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)
                    subscription.status = 'active'
                    subscription.mp_status = 'succeeded'
                    subscription.current_period_start = now
                    subscription.current_period_end = now + timedelta(days=period_days)
                    subscription.grace_period_end = subscription.current_period_end + timedelta(days=Config.MERCADOPAGO_DAYS_TO_EXPIRE)
                    subscription.access_blocked = False
                    if not subscription.payment_date:
                        subscription.payment_date = now
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                elif mp_status == 'paused':
                    subscription.status = 'pending'
                    subscription.mp_status = 'processing'
                elif mp_status == 'cancelled':
                    subscription.status = 'canceled'
                    subscription.mp_status = 'canceled'
                    subscription.canceled_at = datetime.now(timezone.utc)
                    subscription.access_blocked = False
                    customer.can_change_plan = True
                elif mp_status == 'pending':
                    subscription.status = 'pending'
                    subscription.mp_status = 'pending'

                subscription.save()
                customer.save()

                logger.info(f"Subscription {subscription.id} updated to {subscription.status} / mp_status={subscription.mp_status}")
            
            elif topic in ['subscription_authorized_payment']:
                # Webhook for authorized payment (recurring payment notification)
                # The resource_id is the authorized_payment ID, not the subscription ID
                authorized_payment = MercadoPagoService.get_authorized_payment(str(resource_id))
                
                if not authorized_payment:
                    logger.error(f"Failed to get authorized payment for ID: {resource_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Get subscription ID from authorized payment
                mp_subscription_id = authorized_payment.get('subscription_id')
                if not mp_subscription_id:
                    logger.warning(f"No subscription_id in authorized payment: {resource_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Find subscription by MP subscription ID
                subscription = Subscription.objects(
                    mp_subscription_id=mp_subscription_id,
                    visible=True
                ).first()
                
                if not subscription:
                    logger.warning(f"Subscription not found for authorized payment: {mp_subscription_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Get customer from subscription
                customer = subscription.customer_id

                payment_status = authorized_payment.get('status')
                now = datetime.now(timezone.utc)

                already_registered = any(
                    p.mp_authorized_payment_id == str(resource_id)
                    for p in subscription.payment_history
                )

                if payment_status in ('processed', 'approved'):
                    # Cobrança recorrente confirmada: estende o período e libera o acesso
                    period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)
                    next_payment_date = now + timedelta(days=period_days)
                    grace_period_end = next_payment_date + timedelta(days=Config.MERCADOPAGO_DAYS_TO_EXPIRE)

                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = grace_period_end
                    subscription.access_blocked = False
                    subscription.mp_status = 'succeeded'
                    subscription.failure_message = None

                    if not already_registered:
                        subscription.payment_history.append(SubscriptionPayment(
                            mp_authorized_payment_id=str(resource_id),
                            amount=authorized_payment.get('transaction_amount', subscription.amount),
                            currency=authorized_payment.get('currency_id', 'BRL'),
                            status='approved',
                            paid_at=now,
                            period_start=now,
                            period_end=next_payment_date,
                        ))

                    subscription.save()
                    customer.save()
                    logger.info(f"Authorized payment processed for subscription {subscription.id}. Next payment: {next_payment_date.date()}, Grace period ends: {grace_period_end.date()}")

                elif payment_status in ('rejected', 'cancelled'):
                    # Cobrança recorrente falhou: NÃO estende o período nem libera acesso.
                    # O cliente mantém o acesso que já tinha até o grace_period_end vigente.
                    subscription.mp_status = 'failed'
                    subscription.failure_message = f'Cobrança recorrente rejeitada (status: {payment_status})'

                    if not already_registered:
                        subscription.payment_history.append(SubscriptionPayment(
                            mp_authorized_payment_id=str(resource_id),
                            amount=authorized_payment.get('transaction_amount', subscription.amount),
                            currency=authorized_payment.get('currency_id', 'BRL'),
                            status='rejected',
                            paid_at=now,
                        ))

                    subscription.save()
                    logger.warning(f"Authorized payment rejected for subscription {subscription.id} (status: {payment_status})")

                else:
                    # pending/scheduled: cobrança ainda em processamento, aguarda webhook com status final
                    logger.info(f"Authorized payment {resource_id} for subscription {subscription.id} still in progress (status: {payment_status})")

            return {'message': 'Webhook processado com sucesso'}, 200
            
        except Exception as e:
            logger.error(f"Error processing Mercado Pago webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
