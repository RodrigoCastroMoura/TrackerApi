from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, Payment, Customer
from app.infrastructure.mercadopago_service import MercadoPagoService
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - Mercado Pago')

@api.route('/mercadopago')
class MercadoPagoWebhook(Resource):
    
    @api.doc('mercadopago_webhook', description='Webhook do Mercado Pago para processar notificações de pagamento')
    def post(self):
        """Processar notificações do Mercado Pago (payment, subscription)"""
        try:
            data = request.get_json() or {}
            
            # Mercado Pago sends notifications about different events
            topic = data.get('topic') or data.get('type')
            resource_id = data.get('id') or data.get('data', {}).get('id')
            
            logger.info(f"Received Mercado Pago webhook - Topic: {topic}, ID: {resource_id}")
            
            if not topic or not resource_id:
                logger.warning("Webhook missing topic or resource ID")
                return {'message': 'Invalid webhook data'}, 400
            
            # Process payment notification
            if topic in ['payment', 'merchant_order']:
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
