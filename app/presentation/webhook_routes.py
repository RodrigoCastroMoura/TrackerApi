from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, Payment, Customer
from app.infrastructure.stripe_service import StripeService
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração com serviços externos')

@api.route('/stripe')
class StripeWebhook(Resource):
    
    @api.doc('stripe_webhook', description='Webhook do Stripe para processar eventos de pagamento')
    def post(self):
        """Processar eventos do Stripe (subscription.created, invoice.paid, etc.)"""
        try:
            payload = request.data
            signature = request.headers.get('Stripe-Signature')
            
            if not signature:
                logger.warning("No Stripe signature in webhook request")
                return {'message': 'Signature não fornecida'}, 400
            
            event = StripeService.construct_webhook_event(payload, signature)
            
            if not event:
                logger.error("Failed to verify webhook event")
                return {'message': 'Erro ao verificar webhook'}, 400
            
            logger.info(f"Processing Stripe webhook event: {event.type}")
            
            if event.type == 'checkout.session.completed':
                session = event.data.object
                
                if session.mode == 'subscription':
                    stripe_subscription_id = session.subscription
                    
                    subscription = Subscription.objects(
                        stripe_customer_id=session.customer
                    ).order_by('-created_at').first()
                    
                    if subscription:
                        subscription.stripe_subscription_id = stripe_subscription_id
                        subscription.status = 'active'
                        
                        stripe_sub_data = StripeService.get_subscription(stripe_subscription_id)
                        if stripe_sub_data:
                            subscription.current_period_start = stripe_sub_data['current_period_start']
                            subscription.current_period_end = stripe_sub_data['current_period_end']
                        
                        if session.payment_method_details:
                            pm_details = StripeService.get_payment_method_details(
                                session.payment_method
                            )
                            if pm_details:
                                subscription.card_brand = pm_details['brand']
                                subscription.card_last_digits = pm_details['last4']
                                
                                customer = subscription.customer_id
                                customer.card_brand = pm_details['brand']
                                customer.card_last_digits = pm_details['last4']
                                customer.save()
                        
                        subscription.save()
                        logger.info(f"Subscription activated: {subscription.id}")
                
                elif session.mode == 'setup':
                    customer = Customer.objects(card_token=session.customer).first()
                    if customer and session.setup_intent:
                        pm_id = session.setup_intent.payment_method
                        if pm_id:
                            pm_details = StripeService.get_payment_method_details(pm_id)
                            if pm_details:
                                customer.card_brand = pm_details['brand']
                                customer.card_last_digits = pm_details['last4']
                                customer.save()
                                logger.info(f"Card updated for customer: {customer.email}")
            
            elif event.type == 'invoice.payment_succeeded':
                invoice = event.data.object
                
                subscription = Subscription.objects(
                    stripe_subscription_id=invoice.subscription
                ).first()
                
                if subscription:
                    payment = Payment(
                        customer_id=subscription.customer_id,
                        subscription_id=subscription,
                        company_id=subscription.company_id,
                        stripe_payment_intent_id=invoice.payment_intent,
                        stripe_charge_id=invoice.charge,
                        stripe_invoice_id=invoice.id,
                        amount=invoice.amount_paid / 100,
                        currency=invoice.currency.upper(),
                        description=f"Pagamento de assinatura - {subscription.plan_name}",
                        status='succeeded',
                        payment_date=datetime.fromtimestamp(invoice.status_transitions.paid_at),
                        card_brand=subscription.card_brand,
                        card_last_digits=subscription.card_last_digits,
                        receipt_url=invoice.hosted_invoice_url,
                        created_by=None,
                        updated_by=None
                    )
                    payment.save()
                    logger.info(f"Payment recorded: {payment.id} for subscription {subscription.id}")
            
            elif event.type == 'invoice.payment_failed':
                invoice = event.data.object
                
                subscription = Subscription.objects(
                    stripe_subscription_id=invoice.subscription
                ).first()
                
                if subscription:
                    subscription.status = 'past_due'
                    subscription.save()
                    
                    payment = Payment(
                        customer_id=subscription.customer_id,
                        subscription_id=subscription,
                        company_id=subscription.company_id,
                        stripe_payment_intent_id=invoice.payment_intent,
                        stripe_invoice_id=invoice.id,
                        amount=invoice.amount_due / 100,
                        currency=invoice.currency.upper(),
                        description=f"Tentativa de pagamento - {subscription.plan_name}",
                        status='failed',
                        failure_message=invoice.last_finalization_error.message if invoice.last_finalization_error else 'Pagamento falhou',
                        payment_date=datetime.utcnow(),
                        card_brand=subscription.card_brand,
                        card_last_digits=subscription.card_last_digits,
                        created_by=None,
                        updated_by=None
                    )
                    payment.save()
                    logger.warning(f"Payment failed for subscription {subscription.id}")
            
            elif event.type == 'customer.subscription.deleted':
                stripe_subscription = event.data.object
                
                subscription = Subscription.objects(
                    stripe_subscription_id=stripe_subscription.id
                ).first()
                
                if subscription:
                    subscription.status = 'canceled'
                    subscription.canceled_at = datetime.fromtimestamp(stripe_subscription.canceled_at)
                    subscription.save()
                    logger.info(f"Subscription canceled: {subscription.id}")
            
            elif event.type == 'customer.subscription.updated':
                stripe_subscription = event.data.object
                
                subscription = Subscription.objects(
                    stripe_subscription_id=stripe_subscription.id
                ).first()
                
                if subscription:
                    subscription.status = stripe_subscription.status
                    subscription.current_period_start = datetime.fromtimestamp(stripe_subscription.current_period_start)
                    subscription.current_period_end = datetime.fromtimestamp(stripe_subscription.current_period_end)
                    subscription.cancel_at_period_end = stripe_subscription.cancel_at_period_end
                    
                    if stripe_subscription.canceled_at:
                        subscription.canceled_at = datetime.fromtimestamp(stripe_subscription.canceled_at)
                    
                    subscription.save()
                    logger.info(f"Subscription updated: {subscription.id}")
            
            return {'message': 'Webhook processado com sucesso'}, 200
            
        except Exception as e:
            logger.error(f"Error processing Stripe webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500
