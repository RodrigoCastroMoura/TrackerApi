import os
import stripe
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

def get_domain():
    """Get the application domain for redirect URLs"""
    if os.environ.get('REPLIT_DEPLOYMENT'):
        return os.environ.get('REPLIT_DEV_DOMAIN', 'localhost:5000')
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    return domains.split(',')[0] if domains else 'localhost:5000'

class StripeService:
    """Service for handling Stripe payment operations"""
    
    @staticmethod
    def create_customer(email: str, name: str, metadata: Optional[Dict] = None) -> Optional[str]:
        """
        Create a Stripe customer
        
        Args:
            email: Customer email
            name: Customer name
            metadata: Additional metadata
            
        Returns:
            Stripe customer ID or None if failed
        """
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata=metadata or {}
            )
            logger.info(f"Created Stripe customer: {customer.id} for {email}")
            return customer.id
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            return None
    
    @staticmethod
    def create_checkout_session_for_subscription(
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Stripe Checkout Session for subscription
        
        Args:
            customer_id: Stripe customer ID
            price_id: Stripe Price ID
            success_url: URL to redirect after success
            cancel_url: URL to redirect after cancel
            metadata: Additional metadata
            
        Returns:
            Dict with session URL and session ID, or None if failed
        """
        try:
            domain = get_domain()
            
            session = stripe.checkout.Session.create(
                customer=customer_id,
                line_items=[{
                    'price': price_id,
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=f'https://{domain}{success_url}',
                cancel_url=f'https://{domain}{cancel_url}',
                metadata=metadata or {},
                payment_method_types=['card'],
                locale='pt-BR',
            )
            
            logger.info(f"Created checkout session: {session.id}")
            return {
                'session_id': session.id,
                'session_url': session.url
            }
        except Exception as e:
            logger.error(f"Error creating checkout session: {str(e)}")
            return None
    
    @staticmethod
    def create_setup_session_for_card(
        customer_id: str,
        success_url: str,
        cancel_url: str
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Stripe Checkout Session for setting up payment method (card)
        
        Args:
            customer_id: Stripe customer ID
            success_url: URL to redirect after success
            cancel_url: URL to redirect after cancel
            
        Returns:
            Dict with session URL and session ID, or None if failed
        """
        try:
            domain = get_domain()
            
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode='setup',
                success_url=f'https://{domain}{success_url}',
                cancel_url=f'https://{domain}{cancel_url}',
                payment_method_types=['card'],
                locale='pt-BR',
            )
            
            logger.info(f"Created setup session for card: {session.id}")
            return {
                'session_id': session.id,
                'session_url': session.url
            }
        except Exception as e:
            logger.error(f"Error creating setup session: {str(e)}")
            return None
    
    @staticmethod
    def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription details from Stripe
        
        Args:
            subscription_id: Stripe subscription ID
            
        Returns:
            Subscription data or None if failed
        """
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            
            return {
                'id': subscription.id,
                'status': subscription.status,
                'current_period_start': datetime.fromtimestamp(subscription.current_period_start),
                'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
                'cancel_at_period_end': subscription.cancel_at_period_end,
                'canceled_at': datetime.fromtimestamp(subscription.canceled_at) if subscription.canceled_at else None,
                'customer': subscription.customer,
            }
        except Exception as e:
            logger.error(f"Error retrieving subscription: {str(e)}")
            return None
    
    @staticmethod
    def cancel_subscription(subscription_id: str, cancel_immediately: bool = False) -> bool:
        """
        Cancel a subscription
        
        Args:
            subscription_id: Stripe subscription ID
            cancel_immediately: If True, cancel immediately; if False, cancel at period end
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if cancel_immediately:
                stripe.Subscription.delete(subscription_id)
                logger.info(f"Canceled subscription immediately: {subscription_id}")
            else:
                stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
                logger.info(f"Scheduled subscription cancellation at period end: {subscription_id}")
            
            return True
        except Exception as e:
            logger.error(f"Error canceling subscription: {str(e)}")
            return False
    
    @staticmethod
    def reactivate_subscription(subscription_id: str) -> bool:
        """
        Reactivate a subscription that was set to cancel at period end
        
        Args:
            subscription_id: Stripe subscription ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=False
            )
            logger.info(f"Reactivated subscription: {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Error reactivating subscription: {str(e)}")
            return False
    
    @staticmethod
    def get_payment_method_details(payment_method_id: str) -> Optional[Dict[str, Any]]:
        """
        Get payment method details
        
        Args:
            payment_method_id: Stripe payment method ID
            
        Returns:
            Payment method details or None if failed
        """
        try:
            pm = stripe.PaymentMethod.retrieve(payment_method_id)
            
            if pm.type == 'card':
                return {
                    'brand': pm.card.brand,
                    'last4': pm.card.last4,
                    'exp_month': pm.card.exp_month,
                    'exp_year': pm.card.exp_year,
                }
            
            return None
        except Exception as e:
            logger.error(f"Error retrieving payment method: {str(e)}")
            return None
    
    @staticmethod
    def construct_webhook_event(payload: bytes, signature: str):
        """
        Construct and verify a webhook event from Stripe
        
        Args:
            payload: Raw request body
            signature: Stripe signature header
            
        Returns:
            Stripe Event object or None if verification failed
        """
        try:
            if not STRIPE_WEBHOOK_SECRET:
                logger.warning("STRIPE_WEBHOOK_SECRET not set, webhook verification disabled")
                return stripe.Event.construct_from(
                    stripe.util.json.loads(payload), stripe.api_key
                )
            
            event = stripe.Webhook.construct_event(
                payload, signature, STRIPE_WEBHOOK_SECRET
            )
            logger.info(f"Webhook event verified: {event.type}")
            return event
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {str(e)}")
            return None
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error constructing webhook event: {str(e)}")
            return None
    
    @staticmethod
    def get_customer_default_payment_method(customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get customer's default payment method
        
        Args:
            customer_id: Stripe customer ID
            
        Returns:
            Payment method details or None
        """
        try:
            customer = stripe.Customer.retrieve(customer_id)
            
            if customer.invoice_settings and customer.invoice_settings.default_payment_method:
                pm_id = customer.invoice_settings.default_payment_method
                return StripeService.get_payment_method_details(pm_id)
            
            return None
        except Exception as e:
            logger.error(f"Error getting customer payment method: {str(e)}")
            return None
