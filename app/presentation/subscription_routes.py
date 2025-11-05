from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, Payment, Company
from app.infrastructure.stripe_service import StripeService
from app.presentation.decorators import customer_token_required
from mongoengine import DoesNotExist
from bson.objectid import ObjectId
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento')

subscription_create_model = api.model('SubscriptionCreate', {
    'plan_name': fields.String(required=True, description='Nome do plano'),
    'amount': fields.Float(required=True, description='Valor mensal em reais'),
    'stripe_price_id': fields.String(required=True, description='ID do Price no Stripe'),
})

@api.route('/')
class SubscriptionCreate(Resource):
    
    @api.doc('create_subscription')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar assinatura mensal para o customer autenticado"""
        try:
            data = request.get_json()
            
            required_fields = ['plan_name', 'amount', 'stripe_price_id']
            for field in required_fields:
                if field not in data or not data[field]:
                    return {'message': f'Campo {field} é obrigatório'}, 400
            
            if data['amount'] <= 0:
                return {'message': 'Valor deve ser maior que zero'}, 400
            
            existing_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'incomplete'],
                visible=True
            ).first()
            
            if existing_subscription:
                return {'message': 'Cliente já possui uma assinatura ativa'}, 400
            
            stripe_customer_id = None
            if hasattr(current_customer, 'card_token') and current_customer.card_token:
                stripe_customer_id = current_customer.card_token
            else:
                stripe_customer_id = StripeService.create_customer(
                    email=current_customer.email,
                    name=current_customer.name,
                    metadata={
                        'customer_id': str(current_customer.id),
                        'company_id': str(current_customer.company_id.id)
                    }
                )
                
                if not stripe_customer_id:
                    return {'message': 'Erro ao criar cliente no sistema de pagamento'}, 500
                
                current_customer.card_token = stripe_customer_id
                current_customer.save()
            
            checkout_session = StripeService.create_checkout_session_for_subscription(
                customer_id=stripe_customer_id,
                price_id=data['stripe_price_id'],
                success_url='/subscription/success?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='/subscription/cancel',
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_name': data['plan_name'],
                }
            )
            
            if not checkout_session:
                return {'message': 'Erro ao criar sessão de checkout'}, 500
            
            subscription = Subscription(
                customer_id=current_customer,
                company_id=current_customer.company_id,
                stripe_customer_id=stripe_customer_id,
                stripe_price_id=data['stripe_price_id'],
                plan_name=data['plan_name'],
                amount=data['amount'],
                status='incomplete',
                created_by=None,
                updated_by=None
            )
            subscription.save()
            
            logger.info(f"Subscription created for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura criada com sucesso',
                'subscription_id': str(subscription.id),
                'checkout_url': checkout_session['session_url'],
                'session_id': checkout_session['session_id']
            }, 201
            
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            return {'message': 'Erro ao criar assinatura'}, 500
    
    @api.doc('get_my_subscription')
    @customer_token_required
    def get(self, current_customer):
        """Consultar assinatura do customer autenticado"""
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
        """Cancelar assinatura do customer autenticado (cancela ao final do período)"""
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
            
            if subscription.stripe_subscription_id:
                success = StripeService.cancel_subscription(
                    subscription.stripe_subscription_id,
                    cancel_immediately=False
                )
                
                if not success:
                    return {'message': 'Erro ao cancelar assinatura no sistema de pagamento'}, 500
            
            subscription.cancel_at_period_end = True
            subscription.canceled_at = datetime.utcnow()
            subscription.updated_by = None
            subscription.save()
            
            logger.info(f"Subscription canceled for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura cancelada com sucesso. Permanecerá ativa até o fim do período pago.',
                'subscription': subscription.to_dict()
            }, 200
            
        except Exception as e:
            logger.error(f"Error canceling subscription: {str(e)}")
            return {'message': 'Erro ao cancelar assinatura'}, 500

@api.route('/reactivate')
class SubscriptionReactivate(Resource):
    
    @api.doc('reactivate_subscription')
    @customer_token_required
    def post(self, current_customer):
        """Reativar assinatura que foi agendada para cancelamento"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                status='active',
                cancel_at_period_end=True,
                visible=True
            ).first()
            
            if not subscription:
                return {'message': 'Nenhuma assinatura agendada para cancelamento encontrada'}, 404
            
            if subscription.stripe_subscription_id:
                success = StripeService.reactivate_subscription(
                    subscription.stripe_subscription_id
                )
                
                if not success:
                    return {'message': 'Erro ao reativar assinatura no sistema de pagamento'}, 500
            
            subscription.cancel_at_period_end = False
            subscription.canceled_at = None
            subscription.updated_by = None
            subscription.save()
            
            logger.info(f"Subscription reactivated for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura reativada com sucesso',
                'subscription': subscription.to_dict()
            }, 200
            
        except Exception as e:
            logger.error(f"Error reactivating subscription: {str(e)}")
            return {'message': 'Erro ao reativar assinatura'}, 500

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

@api.route('/setup-card')
class SetupCard(Resource):
    
    @api.doc('setup_card')
    @customer_token_required
    def post(self, current_customer):
        """Criar sessão para adicionar/atualizar cartão de crédito"""
        try:
            stripe_customer_id = None
            if hasattr(current_customer, 'card_token') and current_customer.card_token:
                stripe_customer_id = current_customer.card_token
            else:
                stripe_customer_id = StripeService.create_customer(
                    email=current_customer.email,
                    name=current_customer.name,
                    metadata={
                        'customer_id': str(current_customer.id),
                        'company_id': str(current_customer.company_id.id)
                    }
                )
                
                if not stripe_customer_id:
                    return {'message': 'Erro ao criar cliente no sistema de pagamento'}, 500
                
                current_customer.card_token = stripe_customer_id
                current_customer.save()
            
            setup_session = StripeService.create_setup_session_for_card(
                customer_id=stripe_customer_id,
                success_url='/card/success?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='/card/cancel'
            )
            
            if not setup_session:
                return {'message': 'Erro ao criar sessão de configuração de cartão'}, 500
            
            logger.info(f"Setup card session created for customer {current_customer.email}")
            
            return {
                'message': 'Sessão criada com sucesso',
                'checkout_url': setup_session['session_url'],
                'session_id': setup_session['session_id']
            }, 200
            
        except Exception as e:
            logger.error(f"Error creating card setup session: {str(e)}")
            return {'message': 'Erro ao criar sessão de cartão'}, 500
