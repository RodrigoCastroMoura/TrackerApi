from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Payment, Customer
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime
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
            
            topic = data.get('topic') or data.get('type')
            resource_id = data.get('id') or data.get('data', {}).get('id')
            
            logger.info(f"Received Mercado Pago webhook - Topic: {topic}, ID: {resource_id}")
            
            if not topic or not resource_id:
                logger.warning("Webhook missing topic or resource ID")
                return {'message': 'Invalid webhook data'}, 400
            
            if topic in ['preapproval', 'subscription', 'subscription_preapproval']:
                subscription_info = MercadoPagoService.get_subscription_info(str(resource_id))
                
                if not subscription_info:
                    logger.error(f"Failed to get subscription info for ID: {resource_id}")
                    return {'message': 'Subscription not found'}, 404
                
                customer = Customer.objects(
                    mp_subscription_id=str(subscription_info['id']),
                    visible=True
                ).first()
                
                if not customer:
                    payer_email = subscription_info.get('payer_email')
                    if payer_email:
                        customer = Customer.objects(email=payer_email, visible=True).first()
                
                if not customer:
                    logger.warning(f"Customer not found for subscription: {subscription_info['id']}")
                    return {'message': 'Customer not found'}, 404
                
                mp_status = subscription_info['status']
                if mp_status == 'authorized':
                    customer.mp_status = 'succeeded'
                    if not customer.payment_date:
                        customer.payment_date = datetime.utcnow()
                elif mp_status == 'paused':
                    customer.mp_status = 'processing'
                elif mp_status == 'cancelled':
                    customer.mp_status = 'canceled'
                elif mp_status == 'pending':
                    customer.mp_status = 'pending'
                
                customer.mp_subscription_id = str(subscription_info['id'])
                customer.save()
                
                logger.info(f"Customer {customer.id} updated - status: {customer.mp_status}")
            
            elif topic in ['payment', 'payment.created', 'payment.updated', 'merchant_order']:
                payment_info = MercadoPagoService.get_payment_info(str(resource_id))
                
                if not payment_info:
                    logger.error(f"Failed to get payment info for ID: {resource_id}")
                    return {'message': 'Payment not found'}, 404
                
                payer_email = payment_info.get('payer_email')
                if not payer_email:
                    logger.warning("No payer email in payment info")
                    return {'message': 'No payer email'}, 400
                
                customer = Customer.objects(email=payer_email, visible=True).first()
                if not customer:
                    logger.warning(f"Customer not found for email: {payer_email}")
                    return {'message': 'Customer not found'}, 404
                
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
                
                if payment_info.get('card_info'):
                    card_info = payment_info['card_info']
                    customer.card_last_digits = card_info.get('last_four_digits')
                
                customer.save()
                logger.info(f"Customer {customer.id} payment updated - status: {customer.mp_status}")
                
                existing_payment = Payment.objects(mp_payment_id=str(payment_info['id'])).first()
                
                if not existing_payment:
                    payment_status = 'succeeded' if mp_status == 'approved' else (
                        'processing' if mp_status in ['pending', 'in_process'] else (
                        'failed' if mp_status in ['rejected', 'cancelled'] else (
                        'refunded' if mp_status == 'refunded' else 'pending')))
                    
                    payment = Payment(
                        customer_id=customer,
                        company_id=customer.company_id,
                        mp_payment_id=str(payment_info['id']),
                        amount=payment_info['transaction_amount'],
                        currency=payment_info['currency_id'],
                        description=f"Pagamento de assinatura - {customer.name}",
                        status=payment_status,
                        payment_date=datetime.fromisoformat(payment_info['date_created'].replace('Z', '+00:00')) if payment_info.get('date_created') else datetime.utcnow(),
                        card_last_digits=payment_info.get('card_info', {}).get('last_four_digits') if payment_info.get('card_info') else None,
                        payment_method=payment_info.get('payment_method_id', 'mercadopago'),
                        created_by=None,
                        updated_by=None
                    )
                    
                    if mp_status in ['rejected', 'cancelled']:
                        payment.failure_message = payment_info.get('status_detail', 'Pagamento rejeitado')
                    
                    payment.save()
                    logger.info(f"Payment recorded: {payment.id}")
                else:
                    existing_payment.status = 'succeeded' if mp_status == 'approved' else (
                        'processing' if mp_status in ['pending', 'in_process'] else (
                        'failed' if mp_status in ['rejected', 'cancelled'] else (
                        'refunded' if mp_status == 'refunded' else 'pending')))
                    existing_payment.save()
                    logger.info(f"Payment updated: {existing_payment.id}")
            
            return {'message': 'Webhook processado com sucesso'}, 200
            
        except Exception as e:
            logger.error(f"Error processing Mercado Pago webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
