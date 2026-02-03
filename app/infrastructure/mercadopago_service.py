import os
import mercadopago
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

MP_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')

class MercadoPagoService:
    """Service for handling Mercado Pago payment operations"""
    
    @staticmethod
    def get_sdk():
        """Get configured Mercado Pago SDK instance"""
        if not MP_ACCESS_TOKEN:
            logger.error("MERCADOPAGO_ACCESS_TOKEN not configured")
            return None
        return mercadopago.SDK(MP_ACCESS_TOKEN)
    
    @staticmethod
    def create_subscription_preference(
        customer_email: str,
        plan_name: str,
        amount: float,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a payment preference for subscription (monthly recurring)
        
        Args:
            customer_email: Customer email
            plan_name: Name of the plan
            amount: Monthly amount
            metadata: Additional metadata
            
        Returns:
            Dict with init_point (payment URL) and preference_id, or None if failed
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            preference_data = {
                "items": [
                    {
                        "title": plan_name,
                        "quantity": 1,
                        "currency_id": "BRL",
                        "unit_price": float(amount)
                    }
                ],
                "payer": {
                    "email": customer_email
                },
                "back_urls": {
                    "success": f"{os.environ.get('REPLIT_DEV_DOMAIN', 'localhost')}/subscription/success",
                    "failure": f"{os.environ.get('REPLIT_DEV_DOMAIN', 'localhost')}/subscription/failure",
                    "pending": f"{os.environ.get('REPLIT_DEV_DOMAIN', 'localhost')}/subscription/pending"
                },
                "auto_return": "approved",
                "metadata": metadata or {}
            }
            
            preference_response = sdk.preference().create(preference_data)
            preference = preference_response["response"]
            
            logger.info(f"Created payment preference: {preference['id']}")
            
            return {
                'preference_id': preference['id'],
                'init_point': preference['init_point'],  # Payment URL
                'sandbox_init_point': preference.get('sandbox_init_point')  # Test environment
            }
            
        except Exception as e:
            logger.error(f"Error creating payment preference: {str(e)}")
            return None
    
    @staticmethod
    def get_payment_info(payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Get payment information
        
        Args:
            payment_id: Mercado Pago payment ID
            
        Returns:
            Payment data or None if failed
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            payment_response = sdk.payment().get(payment_id)
            payment = payment_response["response"]
            
            return {
                'id': payment['id'],
                'status': payment['status'],  # approved, pending, rejected, cancelled, refunded
                'status_detail': payment.get('status_detail'),
                'transaction_amount': payment['transaction_amount'],
                'currency_id': payment['currency_id'],
                'date_approved': payment.get('date_approved'),
                'date_created': payment['date_created'],
                'payer_email': payment['payer']['email'] if 'payer' in payment else None,
                'payment_method_id': payment.get('payment_method_id'),
                'payment_type_id': payment.get('payment_type_id'),
                'card_info': {
                    'first_six_digits': payment.get('card', {}).get('first_six_digits'),
                    'last_four_digits': payment.get('card', {}).get('last_four_digits'),
                } if 'card' in payment else None
            }
            
        except Exception as e:
            logger.error(f"Error getting payment info: {str(e)}")
            return None
    
    @staticmethod
    def create_subscription_plan(
        plan_name: str,
        amount: float,
        frequency: int = 1,
        frequency_type: str = 'months'
    ) -> Optional[Dict[str, Any]]:
        """
        Create a preapproval plan (subscription plan)
        
        Args:
            plan_name: Name of the subscription plan
            amount: Monthly amount
            frequency: Billing frequency (default: 1)
            frequency_type: Type of frequency (months, days) - default: months
            
        Returns:
            Plan data with ID or None if failed
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            plan_data = {
                "reason": plan_name,
                "auto_recurring": {
                    "frequency": frequency,
                    "frequency_type": frequency_type,
                    "transaction_amount": float(amount),
                    "currency_id": "BRL"
                },
                "back_url": f"{os.environ.get('REPLIT_DEV_DOMAIN', 'localhost')}/subscription/success",
            }
            
            plan_response = sdk.preapproval_plan().create(plan_data)
            plan = plan_response["response"]
            
            logger.info(f"Created subscription plan: {plan['id']}")
            
            return {
                'plan_id': plan['id'],
                'init_point': plan.get('init_point')
            }
            
        except Exception as e:
            logger.error(f"Error creating subscription plan: {str(e)}")
            return None
    
    @staticmethod
    def create_subscription(
        preapproval_plan_id: str,
        payer_email: str,
        card_token_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a subscription (preapproval) for a customer WITH card_token_id.
        Use create_pending_subscription for payment link flow.
        
        Args:
            preapproval_plan_id: ID of the preapproval plan
            payer_email: Customer email
            card_token_id: Card token (REQUIRED when using preapproval_plan_id)
            metadata: Additional metadata
            
        Returns:
            Subscription data or None if failed
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            if not card_token_id:
                logger.error("card_token_id is required when using preapproval_plan_id")
                return None
            
            subscription_data = {
                "preapproval_plan_id": preapproval_plan_id,
                "payer_email": payer_email,
                "card_token_id": card_token_id,
                "status": "authorized",
                "metadata": metadata or {}
            }
            
            sub_response = sdk.preapproval().create(subscription_data)
            subscription = sub_response["response"]
            
            logger.info(f"Created subscription: {subscription['id']}")
            
            return {
                'subscription_id': subscription['id'],
                'init_point': subscription.get('init_point'),
                'status': subscription['status']
            }
            
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            return None

    @staticmethod
    def create_pending_subscription(
        reason: str,
        payer_email: str,
        amount: float,
        frequency: int = 1,
        frequency_type: str = 'months',
        back_url: Optional[str] = None,
        external_reference: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a pending subscription that generates a payment link.
        Customer can complete payment via the init_point URL.
        
        Args:
            reason: Description of the subscription
            payer_email: Customer email
            amount: Monthly amount in BRL
            frequency: Billing frequency (default: 1)
            frequency_type: Type of frequency (months, days) - default: months
            back_url: URL to redirect after payment
            external_reference: Your reference ID (e.g., customer_id)
            metadata: Additional metadata
            
        Returns:
            Dict with subscription_id, init_point (payment URL), and status
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            subscription_data = {
                "reason": reason,
                "payer_email": payer_email,
                "auto_recurring": {
                    "frequency": frequency,
                    "frequency_type": frequency_type,
                    "transaction_amount": float(amount),
                    "currency_id": "BRL"
                },
                "status": "pending"
            }
            
            if back_url:
                subscription_data["back_url"] = back_url
            
            if external_reference:
                subscription_data["external_reference"] = external_reference
                
            if metadata:
                subscription_data["metadata"] = metadata
            
            sub_response = sdk.preapproval().create(subscription_data)
            
            if sub_response.get("status") == 400 or sub_response.get("status") == 401:
                logger.error(f"Mercado Pago error: {sub_response.get('response')}")
                return {
                    'error': True,
                    'message': sub_response.get('response', {}).get('message', 'Unknown error'),
                    'status': sub_response.get("status")
                }
            
            subscription = sub_response["response"]
            
            logger.info(f"Created pending subscription: {subscription['id']}")
            
            return {
                'subscription_id': subscription['id'],
                'init_point': subscription.get('init_point'),
                'status': subscription['status']
            }
            
        except Exception as e:
            logger.error(f"Error creating pending subscription: {str(e)}")
            return None
    
    @staticmethod
    def cancel_subscription(subscription_id: str) -> bool:
        """
        Cancel a subscription
        
        Args:
            subscription_id: Mercado Pago subscription ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return False
            
            update_data = {
                "status": "cancelled"
            }
            
            sdk.preapproval().update(subscription_id, update_data)
            logger.info(f"Canceled subscription: {subscription_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error canceling subscription: {str(e)}")
            return False
    
    @staticmethod
    def get_subscription_info(subscription_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription information
        
        Args:
            subscription_id: Mercado Pago subscription ID
            
        Returns:
            Subscription data or None if failed
        """
        try:
            sdk = MercadoPagoService.get_sdk()
            if not sdk:
                return None
            
            sub_response = sdk.preapproval().get(subscription_id)
            subscription = sub_response["response"]
            
            return {
                'id': subscription['id'],
                'status': subscription['status'],  # pending, authorized, paused, cancelled
                'payer_id': subscription.get('payer_id'),
                'payer_email': subscription.get('payer_email'),
                'next_payment_date': subscription.get('next_payment_date'),
                'date_created': subscription.get('date_created'),
                'last_modified': subscription.get('last_modified'),
            }
            
        except Exception as e:
            logger.error(f"Error getting subscription info: {str(e)}")
            return None
