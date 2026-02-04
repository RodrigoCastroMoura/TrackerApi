from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, Payment, Customer
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime, timedelta
import logging
import hmac
import hashlib
import os
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
        # Split the x-signature header
        parts = x_signature.split(',')
        if len(parts) < 2:
            logger.warning("Invalid x-signature format")
            return False
        
        ts_part = parts[0]  # ts=1234567890
        signature_part = parts[1]  # v1=abc123...
        
        # Extract values
        ts_value = ts_part.split('=')[1]
        received_signature = signature_part.split('=')[1]
        
        # Build the signature template (MUST end with semicolon)
        # Format: id:{data_id};request-id:{x_request_id};ts:{timestamp};
        signature_template = f"id:{data_id};request-id:{x_request_id};ts:{ts_value};"
        
        # Generate HMAC-SHA256 signature
        calculated_signature = hmac.new(
            secret.encode('utf-8'),
            signature_template.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Use constant-time comparison to prevent timing attacks
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
            
            # Check if this is a test/sandbox webhook (live_mode=false)
            is_test_mode = data.get('live_mode') == False
            
            # Step 1: Validate webhook signature for security (skip in test mode)
            x_signature = request.headers.get('x-signature', '')
            x_request_id = request.headers.get('x-request-id', '')
            data_id = request.args.get('data.id', '')
            
            # Get webhook secret from environment
            webhook_secret = Config.MERCADOPAGO_WEBHOOK_SECRET
            
            if is_test_mode:
                logger.info("Test mode webhook received - skipping signature validation")
            elif not webhook_secret:
                logger.warning("⚠️ MERCADOPAGO_WEBHOOK_SECRET not configured - processing without validation")
            else:
                # Validate signature only in production mode with secret configured
                if x_signature and not validate_mercadopago_signature(x_signature, x_request_id, data_id, webhook_secret):
                    logger.error("Invalid webhook signature - rejecting request")
                    return {'message': 'Invalid signature'}, 401
                
                logger.info("Webhook signature validated successfully")
            
            # Step 2: Process webhook data (already parsed above)
            # Mercado Pago sends notifications about different events
            topic = data.get('topic') or data.get('type')
            resource_id = data.get('id') or data.get('data', {}).get('id')
            
            logger.info(f"Received Mercado Pago webhook - Topic: {topic}, ID: {resource_id}")
            
            if not topic or not resource_id:
                logger.warning("Webhook missing topic or resource ID")
                return {'message': 'Invalid webhook data'}, 400
            
            # Process subscription notification (preapproval)
            if topic in ['preapproval', 'subscription']:
                subscription_info = MercadoPagoService.get_subscription_info(str(resource_id))
                
                if not subscription_info:
                    logger.error(f"Failed to get subscription info for ID: {resource_id}")
                    return {'message': 'Subscription not found'}, 404
                
                # Find subscription by mp_subscription_id
                subscription = Subscription.objects(
                    mp_subscription_id=str(subscription_info['id']),
                    visible=True
                ).first()
                
                if not subscription:
                    logger.warning(f"Subscription not found in DB for MP ID: {subscription_info['id']}")
                    return {'message': 'Subscription not found in database'}, 404
                
                # Map Mercado Pago subscription status to our status
                mp_status = subscription_info['status']
                if mp_status == 'authorized':
                    subscription.status = 'active'
                    if not subscription.current_period_start:
                        subscription.current_period_start = datetime.utcnow()
                        subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
                elif mp_status == 'paused':
                    subscription.status = 'past_due'
                elif mp_status == 'cancelled':
                    subscription.status = 'canceled'
                    subscription.canceled_at = datetime.utcnow()
                elif mp_status == 'pending':
                    subscription.status = 'pending'
                
                # Store payer ID if available
                if subscription_info.get('payer_id'):
                    subscription.mp_payer_id = str(subscription_info['payer_id'])
                
                subscription.save()
                logger.info(f"Subscription {subscription.id} updated to status: {subscription.status}")
            
            # Process payment notification
            elif topic in ['payment', 'merchant_order']:
                payment_info = MercadoPagoService.get_payment_info(str(resource_id))
                
                if not payment_info:
                    logger.error(f"Failed to get payment info for ID: {resource_id}")
                    return {'message': 'Payment not found'}, 404
                
                # Find customer by email
                payer_email = payment_info.get('payer_email')
                if not payer_email:
                    logger.warning("No payer email in payment info")
                    return {'message': 'No payer email'}, 400
                
                customer = Customer.objects(email=payer_email, visible=True).first()
                if not customer:
                    logger.warning(f"Customer not found for email: {payer_email}")
                    return {'message': 'Customer not found'}, 404
                
                # Find or create subscription
                subscription = Subscription.objects(
                    customer_id=customer.id,
                    visible=True
                ).order_by('-created_at').first()
                
                if not subscription:
                    logger.warning(f"No subscription found for customer: {customer.email}")
                    return {'message': 'Subscription not found'}, 404
                
                # Map Mercado Pago status to our status
                mp_status = payment_info['status']
                if mp_status == 'approved':
                    payment_status = 'succeeded'
                    subscription_status = 'active'
                elif mp_status in ['pending', 'in_process']:
                    payment_status = 'processing'
                    subscription_status = 'active' if subscription.status == 'active' else 'pending'
                elif mp_status in ['rejected', 'cancelled']:
                    payment_status = 'failed'
                    subscription_status = subscription.status
                elif mp_status == 'refunded':
                    payment_status = 'refunded'
                    subscription_status = subscription.status
                else:
                    payment_status = 'pending'
                    subscription_status = subscription.status
                
                # Update subscription
                if subscription.status != subscription_status:
                    subscription.status = subscription_status
                    
                    if subscription_status == 'active' and not subscription.current_period_start:
                        subscription.current_period_start = datetime.utcnow()
                        subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
                    
                    if payment_info.get('card_info'):
                        card_info = payment_info['card_info']
                        subscription.card_last_digits = card_info.get('last_four_digits')
                        customer.card_last_digits = card_info.get('last_four_digits')
                        customer.save()
                    
                    subscription.save()
                
                # Check if payment already exists
                existing_payment = Payment.objects(mp_payment_id=str(payment_info['id'])).first()
                
                if not existing_payment:
                    # Create payment record
                    payment = Payment(
                        customer_id=customer,
                        subscription_id=subscription if subscription else None,
                        company_id=customer.company_id,
                        mp_payment_id=str(payment_info['id']),
                        amount=payment_info['transaction_amount'],
                        currency=payment_info['currency_id'],
                        description=f"Pagamento de assinatura - {subscription.plan_name if subscription else 'N/A'}",
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
                    logger.info(f"Payment recorded: {payment.id} - Status: {payment_status}")
                else:
                    # Update existing payment
                    existing_payment.status = payment_status
                    existing_payment.save()
                    logger.info(f"Payment updated: {existing_payment.id} - Status: {payment_status}")
            
            return {'message': 'Webhook processado com sucesso'}, 200
            
        except Exception as e:
            logger.error(f"Error processing Mercado Pago webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
