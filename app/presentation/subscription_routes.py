from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, Payment
from app.infrastructure.mercadopago_service import MercadoPagoService
from app.presentation.auth_routes import customer_token_required
from mongoengine import DoesNotExist
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento com Mercado Pago')

subscription_create_model = api.model('SubscriptionCreate', {
    'plan_name': fields.String(required=True, description='Nome do plano de assinatura'),
    'amount': fields.Float(required=True, description='Valor mensal em reais'),
})

@api.route('/')
class SubscriptionResource(Resource):
    
    @api.doc('create_subscription')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar link de pagamento para assinatura mensal"""
        try:
            data = request.get_json()
            
            required_fields = ['plan_name', 'amount']
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400
            
            if data['amount'] <= 0:
                return {'message': 'Valor deve ser maior que zero'}, 400
            
            # Check for existing active subscription
            existing_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'incomplete', 'pending'],
                visible=True
            ).first()
            
            if existing_subscription:
                return {'message': 'Cliente já possui uma assinatura ativa ou pendente'}, 400
            
            # Step 1: Create a preapproval plan (subscription plan) in Mercado Pago
            plan = MercadoPagoService.create_subscription_plan(
                plan_name=data['plan_name'],
                amount=data['amount'],
                frequency=1,
                frequency_type='months'
            )
            
            if not plan:
                return {'message': 'Erro ao criar plano de assinatura no Mercado Pago'}, 500
            
            # Step 2: Create subscription (preapproval) for the customer
            mp_subscription = MercadoPagoService.create_subscription(
                preapproval_plan_id=plan['plan_id'],
                payer_email=current_customer.email,
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                }
            )
            
            if not mp_subscription:
                return {'message': 'Erro ao criar assinatura no Mercado Pago'}, 500
            
            # Step 3: Create subscription record in our database
            subscription = Subscription(
                customer_id=current_customer,
                company_id=current_customer.company_id,
                mp_subscription_id=mp_subscription['subscription_id'],
                mp_preapproval_plan_id=plan['plan_id'],
                plan_name=data['plan_name'],
                amount=data['amount'],
                status='pending',  # Will be updated by webhook when payment is confirmed
                billing_cycle='monthly',
                currency='BRL',
                created_by=None,
                updated_by=None
            )
            subscription.save()
            
            logger.info(f"Recurring subscription created for customer {current_customer.email}, MP subscription ID: {mp_subscription['subscription_id']}")
            
            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'payment_url': mp_subscription['init_point'],
                'mp_subscription_id': mp_subscription['subscription_id'],
                'instructions': 'Acesse o link para autorizar os pagamentos mensais recorrentes'
            }, 201
            
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            return {'message': 'Erro ao criar assinatura'}, 500
    
    @api.doc('get_my_subscription')
    @customer_token_required
    def get(self, current_customer):
        """Consultar assinatura ativa do customer autenticado"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                visible=True
            ).order_by('-created_at').first()
            
            if not subscription:
                return {'message': 'Nenhuma assinatura encontrada'}, 404
            
            return subscription.to_dict(), 200
            
        except Exception as e:
            logger.error(f"Error getting subscription: {str(e)}")
            return {'message': 'Erro ao consultar assinatura'}, 500

@api.route('/cancel')
class SubscriptionCancel(Resource):
    
    @api.doc('cancel_subscription')
    @customer_token_required
    def post(self, current_customer):
        """Cancelar assinatura ativa do customer"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                status='active',
                visible=True
            ).first()
            
            if not subscription:
                return {'message': 'Nenhuma assinatura ativa encontrada'}, 404
            
            if subscription.cancel_at_period_end:
                return {'message': 'Assinatura já está agendada para cancelamento'}, 400
            
            # Cancel on Mercado Pago if subscription ID exists
            if subscription.mp_subscription_id:
                success = MercadoPagoService.cancel_subscription(subscription.mp_subscription_id)
                if not success:
                    logger.warning(f"Failed to cancel subscription on Mercado Pago: {subscription.mp_subscription_id}")
            
            # Mark as canceled
            subscription.status = 'canceled'
            subscription.canceled_at = datetime.utcnow()
            subscription.cancel_at_period_end = True
            subscription.updated_by = None
            subscription.save()
            
            logger.info(f"Subscription canceled for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura cancelada com sucesso',
                'subscription': subscription.to_dict()
            }, 200
            
        except Exception as e:
            logger.error(f"Error canceling subscription: {str(e)}")
            return {'message': 'Erro ao cancelar assinatura'}, 500

@api.route('/payments')
class PaymentHistory(Resource):
    
    @api.doc('get_payment_history')
    @customer_token_required
    def get(self, current_customer):
        """Histórico de pagamentos do customer autenticado"""
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 20))
            
            if page < 1 or per_page < 1 or per_page > 100:
                return {'message': 'Parâmetros de paginação inválidos'}, 400
            
            skip = (page - 1) * per_page
            
            payments = Payment.objects(
                customer_id=current_customer.id,
                visible=True
            ).order_by('-payment_date').skip(skip).limit(per_page)
            
            total = Payment.objects(
                customer_id=current_customer.id,
                visible=True
            ).count()
            
            return {
                'payments': [p.to_dict() for p in payments],
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }, 200
            
        except Exception as e:
            logger.error(f"Error getting payment history: {str(e)}")
            return {'message': 'Erro ao consultar histórico de pagamentos'}, 500
