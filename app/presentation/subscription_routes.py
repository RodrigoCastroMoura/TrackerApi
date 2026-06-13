import os
from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, Payment, SubscriptionPlan
from app.infrastructure.mercadopago_service import MercadoPagoService
from app.presentation.auth_routes import customer_token_required
from mongoengine import DoesNotExist
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento com Mercado Pago')

subscription_create_model = api.model('SubscriptionCreate', {
    'plan_id': fields.String(required=True, description='ID do plano de assinatura cadastrado'),
})

@api.route('/')
class SubscriptionResource(Resource):
    
    @api.doc('create_subscription')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar assinatura a partir de um plano cadastrado"""
        try:
            data = request.get_json()
            
            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400
            
            # Step 1: Fetch subscription plan — aceita ObjectId do banco ou mp_preapproval_plan_id
            plan_id_input = data['plan_id']
            plan = None

            if len(plan_id_input) == 24:
                # Formato ObjectId do MongoDB
                try:
                    plan = SubscriptionPlan.objects(
                        id=plan_id_input,
                        company_id=current_customer.company_id,
                        is_active=True,
                        visible=True
                    ).first()
                except Exception:
                    pass

            if not plan:
                # Tenta pelo mp_preapproval_plan_id (ID do Mercado Pago)
                plan = SubscriptionPlan.objects(
                    mp_preapproval_plan_id=plan_id_input,
                    company_id=current_customer.company_id,
                    is_active=True,
                    visible=True
                ).first()

            if not plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404
            
            # Check for existing active subscription
            existing_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'incomplete', 'pending'],
                visible=True
            ).first()
            
            if existing_subscription:
                return {'message': 'Cliente já possui uma assinatura ativa ou pendente'}, 400
            
            # Step 2: Create or reuse Mercado Pago preapproval plan
            mp_plan_id = plan.mp_preapproval_plan_id
            
            if not mp_plan_id:
                # Map billing_cycle to Mercado Pago frequency
                if plan.billing_cycle == 'yearly':
                    frequency = 12
                    frequency_type = 'months'
                else:  # monthly
                    frequency = 1
                    frequency_type = 'months'
                
                # Create new plan in Mercado Pago
                mp_plan = MercadoPagoService.create_subscription_plan(
                    plan_name=plan.name,
                    amount=plan.amount,
                    frequency=frequency,
                    frequency_type=frequency_type
                )
                
                if not mp_plan:
                    return {'message': 'Erro ao criar plano de assinatura no Mercado Pago'}, 500
                
                mp_plan_id = mp_plan['plan_id']
                
                # Save MP plan ID for future reuse
                plan.mp_preapproval_plan_id = mp_plan_id
                plan.save()
            
            # Step 3: Create pending subscription — generates payment link for the customer
            if plan.billing_cycle == 'yearly':
                frequency = 12
                frequency_type = 'months'
            else:
                frequency = 1
                frequency_type = 'months'

            mp_subscription = MercadoPagoService.create_pending_subscription(
                reason=plan.name,
                payer_email=current_customer.email,
                amount=plan.amount,
                frequency=frequency,
                frequency_type=frequency_type,
                back_url=os.environ.get('APP_URL', 'https://www.rcminformatica.tec.br/'),
                external_reference=str(current_customer.id),
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                }
            )
            
            if not mp_subscription or mp_subscription.get('error'):
                mp_msg = mp_subscription.get('message', '') if mp_subscription else ''
                mp_status = mp_subscription.get('status', 400) if mp_subscription else 400
                # Restrição de sandbox: payer e collector precisam ser ambos reais ou ambos de teste
                if 'real or test users' in mp_msg:
                    return {'message': 'Erro de ambiente: no modo sandbox o email do cliente deve ser de um usuário de teste do Mercado Pago. Em produção use o token APP- e emails reais.', 'mp_error': mp_msg}, 400
                return {'message': mp_msg or 'Erro ao criar assinatura no Mercado Pago'}, 400
            
            # Step 4: Create subscription record in our database
            subscription = Subscription(
                customer_id=current_customer,
                company_id=current_customer.company_id,
                mp_subscription_id=mp_subscription['subscription_id'],
                mp_preapproval_plan_id=mp_plan_id,
                plan_name=plan.name,
                amount=plan.amount,
                status='pending',  # Will be updated by webhook when payment is confirmed
                billing_cycle=plan.billing_cycle,
                currency='BRL',
                created_by=None,
                updated_by=None
            )
            subscription.save()
            
            logger.info(f"Recurring subscription created for customer {current_customer.email}, plan: {plan.name}, MP subscription ID: {mp_subscription['subscription_id']}")
            
            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'plan_name': plan.name,
                'amount': plan.amount,
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
                status__in=['active', 'pending'],
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


@api.route('/statement')
class SubscriptionStatement(Resource):
    
    @api.doc('get_subscription_statement',
             params={
                 'month': {'type': 'string', 'description': 'Mês no formato YYYY-MM (ex: 2026-06)', 'default': ''},
                 'page': {'type': 'integer', 'default': 1},
                 'per_page': {'type': 'integer', 'default': 20}
             })
    @customer_token_required
    def get(self, current_customer):
        """Extrato mensal de pagamentos da assinatura (recorrente)"""
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 20))
            month_filter = request.args.get('month', '').strip()
            
            if page < 1 or per_page < 1 or per_page > 100:
                return {'message': 'Parâmetros de paginação inválidos'}, 400
            
            # Get subscription
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                visible=True
            ).order_by('-created_at').first()
            
            if not subscription:
                return {'message': 'Nenhuma assinatura encontrada'}, 404
            
            # Build payment query
            payment_query = {
                'customer_id': current_customer.id,
                'visible': True
            }
            
            # Month filter
            if month_filter:
                try:
                    year, month = month_filter.split('-')
                    start = datetime(int(year), int(month), 1)
                    if int(month) == 12:
                        end = datetime(int(year) + 1, 1, 1)
                    else:
                        end = datetime(int(year), int(month) + 1, 1)
                    payment_query['payment_date__gte'] = start
                    payment_query['payment_date__lt'] = end
                except ValueError:
                    return {'message': 'Formato de mês inválido. Use YYYY-MM (ex: 2026-06)'}, 400
            
            # Get payments for this month
            payments = Payment.objects(**payment_query).order_by('-payment_date')
            
            # Group by month
            monthly_summary = {}
            for p in payments:
                if p.payment_date:
                    key = p.payment_date.strftime('%Y-%m')
                    if key not in monthly_summary:
                        monthly_summary[key] = {
                            'month': key,
                            'month_label': p.payment_date.strftime('%B/%Y').capitalize(),
                            'total': 0.0,
                            'payments': [],
                            'count': 0
                        }
                    monthly_summary[key]['total'] += float(p.amount)
                    monthly_summary[key]['count'] += 1
                    monthly_summary[key]['payments'].append(p.to_dict())
            
            # Sort by month descending
            sorted_months = sorted(monthly_summary.keys(), reverse=True)
            
            # Paginate months
            total_months = len(sorted_months)
            total_pages = (total_months + per_page - 1) // per_page
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_months = sorted_months[start_idx:end_idx]
            
            months_data = [monthly_summary[m] for m in paginated_months]
            
            # Calculate totals
            total_paid = sum(m['total'] for m in monthly_summary.values())
            
            return {
                'subscription': subscription.to_dict(),
                'customer': {
                    'name': current_customer.name,
                    'email': current_customer.email
                },
                'summary': {
                    'total_paid': round(total_paid, 2),
                    'total_months': total_months,
                    'plan_amount': subscription.amount,
                    'plan_name': subscription.plan_name,
                    'status': subscription.status,
                    'next_payment_date': subscription.current_period_end.isoformat() if subscription.current_period_end else None
                },
                'months': months_data,
                'page': page,
                'per_page': per_page,
                'total': total_months,
                'pages': total_pages
            }, 200
            
        except Exception as e:
            logger.error(f"Error getting subscription statement: {str(e)}")
            return {'message': 'Erro ao gerar extrato'}, 500
