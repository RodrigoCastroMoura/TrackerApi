from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, SubscriptionPayment, Customer, period_days_for_frequency
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime, timedelta, timezone
import logging
import hmac
import hashlib
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - Mercado Pago e Pagar.me')

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

            x_signature = request.headers.get('x-signature', '')
            x_request_id = request.headers.get('x-request-id', '')
            data_id = request.args.get('data.id', '')

            webhook_secret = Config.MERCADOPAGO_WEBHOOK_SECRET

            if not webhook_secret:
                logger.warning("MERCADOPAGO_WEBHOOK_SECRET not configured - processing without validation")
            else:
                # x-signature é sempre exigido quando o secret está configurado: a ausência do
                # header não deve pular a validação, e o campo live_mode do próprio payload
                # (controlado por quem envia a requisição) não deve decidir se ela roda.
                if not validate_mercadopago_signature(x_signature, x_request_id, data_id, webhook_secret):
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
                    return {'message': 'Erro ao consultar assinatura no Mercado Pago'}, 502
                
                # Find subscription by MP subscription ID
                subscription = Subscription.objects(
                    provider_subscription_id=str(subscription_info['id']),
                    visible=True
                ).first()
                
                if not subscription:
                    # A assinatura é sempre criada localmente pela nossa própria chamada
                    # à API do Mercado Pago (POST/PUT /api/subscriptions), que já recebe o
                    # provider_subscription_id na resposta síncrona. Se ainda não achamos o
                    # registro aqui, é provável que o webhook tenha chegado antes desse save
                    # local (corrida) — respondemos um status de erro para que o Mercado Pago
                    # reenvie a notificação mais tarde, em vez de descartá-la silenciosamente.
                    logger.warning(f"Subscription ainda não persistida localmente para MP ID {subscription_info['id']}; solicitando reenvio")
                    return {'message': 'Assinatura ainda não disponível localmente'}, 503

                # Get customer from subscription
                customer = subscription.customer_id
                
                provider_status = subscription_info['status']

                if provider_status == 'authorized':
                    now = datetime.now(timezone.utc)
                    period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)
                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    subscription.current_period_start = now
                    subscription.current_period_end = now + timedelta(days=period_days)
                    subscription.grace_period_end = subscription.current_period_end + timedelta(days=Config.MERCADOPAGO_DAYS_TO_EXPIRE)
                    subscription.access_blocked = False
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                elif provider_status == 'paused':
                    subscription.status = 'paused'
                    subscription.provider_status = 'pending'
                elif provider_status == 'cancelled':
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = datetime.now(timezone.utc)
                    if customer.require_payment_method == False:
                        customer.can_change_plan = True

                elif provider_status == 'pending':
                    subscription.status = 'pending'
                    subscription.provider_status = 'pending'

                subscription.save()
                customer.save()

                logger.info(f"Subscription {subscription.id} updated to {subscription.status} / provider_status={subscription.provider_status}")
            
            elif topic in ['subscription_authorized_payment']:
                # Webhook for authorized payment (recurring payment notification)
                # The resource_id is the authorized_payment ID, not the subscription ID
                authorized_payment = MercadoPagoService.get_authorized_payment(str(resource_id))
                
                if not authorized_payment:
                    logger.error(f"Failed to get authorized payment for ID: {resource_id}")
                    return {'message': 'Erro ao consultar pagamento no Mercado Pago'}, 502
                
                # Get subscription ID from authorized payment
                provider_subscription_id = authorized_payment.get('subscription_id')
                if not provider_subscription_id:
                    logger.warning(f"No subscription_id in authorized payment: {resource_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Find subscription by MP subscription ID
                subscription = Subscription.objects(
                    provider_subscription_id=provider_subscription_id,
                    visible=True
                ).first()
                
                if not subscription:
                    logger.warning(f"Subscription not found for authorized payment: {provider_subscription_id}; solicitando reenvio")
                    return {'message': 'Assinatura ainda não disponível localmente'}, 503
                
                # Get customer from subscription
                customer = subscription.customer_id

                payment_status = authorized_payment.get('status')
                now = datetime.now(timezone.utc)

                already_registered = any(
                    p.provider_payment_id == str(resource_id)
                    for p in subscription.payment_history
                )

                if payment_status in ('processed', 'approved'):
                    # Cobrança recorrente confirmada: estende o período e libera o acesso.
                    # Este é o único branch que confirma cobranças recorrentes — sem liberar
                    # require_payment_method/can_change_plan aqui, um cliente cuja primeira
                    # cobrança recorrente aprove sem o webhook de preapproval "authorized" ter
                    # sido processado (ex.: corrida, falha de rede) fica preso indefinidamente
                    # na tela de pagamento do app, mesmo com o pagamento já confirmado no MP.
                    period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)
                    next_payment_date = now + timedelta(days=period_days)
                    grace_period_end = next_payment_date + timedelta(days=Config.MERCADOPAGO_DAYS_TO_EXPIRE)

                    subscription.status = 'active'
                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = grace_period_end
                    subscription.provider_status = 'succeeded'
                    subscription.failure_message = None
                    subscription.canceled_at = None

                    if not already_registered:
                        subscription.payment_history.append(SubscriptionPayment(
                            provider_payment_id=str(resource_id),
                            amount=authorized_payment.get('transaction_amount', subscription.amount),
                            currency=authorized_payment.get('currency_id', 'BRL'),
                            status='approved',
                            paid_at=now,
                            period_start=now,
                            period_end=next_payment_date,
                        ))

                    customer.require_payment_method = False
                    customer.can_change_plan = False

                    subscription.save()
                    customer.save()
                    logger.info(f"Authorized payment processed for subscription {subscription.id}. Next payment: {next_payment_date.date()}, Grace period ends: {grace_period_end.date()}")

                elif payment_status in ('rejected', 'cancelled'):
                    # Cobrança recorrente falhou: NÃO estende o período nem libera acesso.
                    # O cliente mantém o acesso que já tinha até o grace_period_end vigente.
                    subscription.provider_status = 'failed'
                    if payment_status == 'cancelled':
                        subscription.status = 'canceled'
                        subscription.canceled_at = datetime.now(timezone.utc) 
                    else:
                        subscription.status = 'pendingPayment'
                    subscription.failure_message = f'Cobrança recorrente rejeitada (status: {payment_status})'

                    if not already_registered:
                        subscription.payment_history.append(SubscriptionPayment(
                            provider_payment_id=str(resource_id),
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
