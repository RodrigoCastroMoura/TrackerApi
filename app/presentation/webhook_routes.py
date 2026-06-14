from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Customer, Subscription, SubscriptionPayment
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime, timedelta
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
        parts = x_signature.split(',')
        if len(parts) < 2:
            logger.warning("Invalid x-signature format")
            return False
        
        ts_part = parts[0]
        signature_part = parts[1]
        
        ts_value = ts_part.split('=')[1]
        received_signature = signature_part.split('=')[1]
        
        signature_template = f"id:{data_id};request-id:{x_request_id};ts:{ts_value};"
        
        calculated_signature = hmac.new(
            secret.encode('utf-8'),
            signature_template.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(calculated_signature, received_signature)
        
        if not is_valid:
            logger.warning("Signature validation failed - potential security threat")
        
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
                    logger.warning(f"Subscription not found: {subscription_info['id']}")
                    return {'message': 'Webhook recebido'}, 200
                
                # Get customer from subscription
                customer = subscription.customer_id
                
                mp_status = subscription_info['status']
                if mp_status == 'authorized':
                    subscription.status = 'active'
                    subscription.current_period_start = datetime.utcnow()
                    subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
                    subscription.grace_period_end = subscription.current_period_end + timedelta(days=15)
                    subscription.access_blocked = False
                    customer.mp_status = 'succeeded'
                    customer.payment_deadline = subscription.grace_period_end
                    customer.subscription_blocked = False
                    customer.subscription_blocked_reason = None
                    customer.require_payment_method = False
                    if not customer.payment_date:
                        customer.payment_date = datetime.utcnow()
                elif mp_status == 'paused':
                    subscription.status = 'pending'
                    customer.mp_status = 'processing'
                elif mp_status == 'cancelled':
                    subscription.status = 'canceled'
                    subscription.canceled_at = datetime.utcnow()
                    subscription.access_blocked = True
                    customer.mp_status = 'canceled'
                    customer.subscription_blocked = True
                    customer.subscription_blocked_reason = 'Assinatura cancelada'
                elif mp_status == 'pending':
                    subscription.status = 'pending'
                    customer.mp_status = 'pending'
                
                subscription.save()
                customer.save()
                
                logger.info(f"Subscription {subscription.id} updated to {subscription.status}, customer {customer.id} updated to {customer.mp_status}")
            
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
                
                # Atualizar período e prazo de pagamento
                now = datetime.utcnow()
                next_payment_date = now + timedelta(days=30)
                grace_period_end = next_payment_date + timedelta(days=15)

                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = grace_period_end
                subscription.access_blocked = False

                # Registrar pagamento no histórico (dedup por mp_authorized_payment_id)
                already_registered = any(
                    p.mp_authorized_payment_id == str(resource_id)
                    for p in subscription.payment_history
                )
                if not already_registered:
                    payment_entry = SubscriptionPayment(
                        mp_authorized_payment_id=str(resource_id),
                        amount=authorized_payment.get('transaction_amount', subscription.amount),
                        currency=authorized_payment.get('currency_id', 'BRL'),
                        status='approved' if authorized_payment.get('status') == 'approved' else authorized_payment.get('status', 'pending'),
                        paid_at=now,
                        period_start=now,
                        period_end=next_payment_date,
                    )
                    subscription.payment_history.append(payment_entry)

                subscription.save()
                
                customer.payment_deadline = grace_period_end
                customer.subscription_blocked = False
                customer.subscription_blocked_reason = None
                customer.save()
                
                logger.info(f"Authorized payment processed for subscription {subscription.id}. Next payment: {next_payment_date.date()}, Grace period ends: {grace_period_end.date()}")
            
            elif topic in ['payment', 'payment.created', 'payment.updated', 'merchant_order']:
                payment_info = MercadoPagoService.get_payment_info(str(resource_id))
                
                if not payment_info:
                    logger.error(f"Failed to get payment info for ID: {resource_id}")
                    return {'message': 'Webhook recebido'}, 200
                
                payer_email = payment_info.get('payer_email')
                if not payer_email:
                    logger.warning("No payer email in payment info")
                    return {'message': 'Webhook recebido'}, 200
                
                customer = Customer.objects(email=payer_email, visible=True).first()
                if not customer:
                    logger.warning(f"Customer not found for email: {payer_email}")
                    return {'message': 'Webhook recebido'}, 200
                
                mp_status = payment_info['status']
                if mp_status == 'approved':
                    customer.mp_status = 'succeeded'
                    customer.payment_date = datetime.utcnow()
                    customer.failure_message = None
                elif mp_status in ['pending', 'in_process']:
                    customer.mp_status = 'processing'
                elif mp_status in ['rejected', 'cancelled']:
                    customer.mp_status = 'failed'
                    customer.failure_message = payment_info.get('status_detail', 'Pagamento rejeitado')
                elif mp_status == 'refunded':
                    customer.mp_status = 'refunded'
                    customer.refunded_at = datetime.utcnow()
                
                customer.save()
                logger.info(f"Customer {customer.id} payment updated - status: {customer.mp_status}")
                
            return {'message': 'Webhook processado com sucesso'}, 200
            
        except Exception as e:
            logger.error(f"Error processing Mercado Pago webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
