from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, SubscriptionPlan, to_mercadopago_frequency
from app.infrastructure.mercadopago_service import MercadoPagoService
from app.presentation.auth_routes import customer_token_required
from mongoengine import DoesNotExist
from datetime import datetime, timedelta, timezone
import logging
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento com Mercado Pago')

def _mp_start_date(delay_days: int = 0) -> str:
    """
    Data/hora atual (ou deslocada `delay_days` dias) formatada no padrão ISO 8601
    exigido pelo Mercado Pago (com milissegundos e offset), usada como
    auto_recurring.start_date. Sem esse campo o MP só cobra a partir do próximo
    ciclo depois da autorização.
    """
    when = datetime.now(timezone.utc) + timedelta(days=delay_days)
    return when.strftime('%Y-%m-%dT%H:%M:%S.000+00:00')

def _mp_start_date_now() -> str:
    return _mp_start_date(0)

def _first_charge_start_date(frequency: int, frequency_type: str) -> str:
    """
    Planos mensais (frequency=1, frequency_type='months'): primeira cobrança só
    daqui a 1 mês, seguindo o ciclo normal do plano — sem cobrança na criação.
    Qualquer outro plano: cobrança imediata, assim que o cliente autorizar.
    """
    if frequency == 1 and frequency_type == 'months':
        return _mp_start_date(30)
    if frequency == 1 and frequency_type == 'weeks':
        return _mp_start_date(7)
    return _mp_start_date(0)

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
            
            # Bloqueia se já tem assinatura ativa
            active_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status='active',
                visible=True
            ).first()

            if active_subscription:
                return {'message': 'Cliente já possui uma assinatura ativa'}, 400

            # Se tiver assinatura pendente, cancela antes de criar a nova
            pending_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status='pending',
                visible=True
            ).first()

            if pending_subscription:
                if pending_subscription.mp_subscription_id:
                    MercadoPagoService.cancel_subscription(pending_subscription.mp_subscription_id)
                pending_subscription.delete()
                logger.info(f"Deleted previous pending subscription {pending_subscription.id} before creating new one")
            
            # Step 2: Create subscription plan on Mercado Pago if not already created
            mp_frequency, mp_frequency_type = to_mercadopago_frequency(plan.frequency, plan.frequency_type)
            mp_plan_id = plan.mp_preapproval_plan_id

            if not mp_plan_id:
                mp_plan = MercadoPagoService.create_subscription_plan(
                    plan_name=plan.name,
                    amount=plan.amount,
                    frequency=mp_frequency,
                    frequency_type=mp_frequency_type
                )

                if not mp_plan:
                    return {'message': 'Erro ao criar plano de assinatura no Mercado Pago'}, 500

                mp_plan_id = mp_plan['plan_id']
                plan.mp_preapproval_plan_id = mp_plan_id
                plan.save()

            # Step 3: Create pending subscription — generates payment link for the customer
            mp_subscription = MercadoPagoService.create_pending_subscription(
                reason=plan.name,
                payer_email=current_customer.email,
                amount=plan.amount,
                frequency=mp_frequency,
                frequency_type=mp_frequency_type,
                back_url=Config.MERCADOPAGO_URL_RETURN,
                external_reference=str(current_customer.id),
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                },
                start_date=_first_charge_start_date(plan.frequency, plan.frequency_type)
            )

            if not mp_subscription or mp_subscription.get('error'):
                mp_msg = mp_subscription.get('message', '') if mp_subscription else ''
                if 'real or test users' in mp_msg:
                    return {'message': 'Erro de ambiente: no modo sandbox o email do cliente deve ser de um usuário de teste do Mercado Pago. Em produção use o token APP- e emails reais.', 'mp_error': mp_msg}, 400
                return {'message': mp_msg or 'Erro ao criar assinatura no Mercado Pago'}, 400
            
            # Step 4: Salvar no banco — se falhar, cancela no MP para evitar órfão
            mp_sub_id = mp_subscription['subscription_id']
            try:
                subscription = Subscription(
                    customer_id=current_customer,
                    company_id=current_customer.company_id,
                    mp_subscription_id=mp_sub_id,
                    mp_preapproval_plan_id=mp_plan_id,
                    plan_name=plan.name,
                    amount=plan.amount,
                    status='pending',
                    mp_status='pending',
                    billing_cycle=mp_frequency_type,
                    frequency=mp_frequency,
                    currency='BRL',
                    payment_url=mp_subscription['init_point'],
                    created_by=None,
                    updated_by=None
                )
                subscription.save()
            except Exception as db_error:
                logger.error(f"DB save failed, canceling MP subscription {mp_sub_id}: {db_error}")
                MercadoPagoService.cancel_subscription(mp_sub_id)
                return {'message': 'Erro ao salvar assinatura. Tente novamente.'}, 500

            logger.info(f"Subscription created for customer {current_customer.email}, plan: {plan.name}, MP ID: {mp_sub_id}")

            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'plan_name': plan.name,
                'amount': plan.amount,
                'billing_cycle': plan.frequency_type,
                'payment_url': mp_subscription['init_point'],
                'mp_subscription_id': mp_sub_id,
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

    @api.doc('change_subscription_plan')
    @api.expect(subscription_create_model)
    @customer_token_required
    def put(self, current_customer):
        """Trocar de plano ou reativar assinatura cancelada"""
        try:
            data = request.get_json()

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            plan_id_input = data['plan_id']
            new_plan = None

            if len(plan_id_input) == 24:
                try:
                    new_plan = SubscriptionPlan.objects(
                        id=plan_id_input,
                        company_id=current_customer.company_id,
                        is_active=True,
                        visible=True
                    ).first()
                except Exception:
                    pass
            
            if not new_plan:
                new_plan = SubscriptionPlan.objects(
                    mp_preapproval_plan_id=plan_id_input,
                    company_id=current_customer.company_id,
                    is_active=True,
                    visible=True
                ).first()

            if not new_plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            # Busca a assinatura existente
            existing = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'canceled'],
                visible=True
            ).order_by('-created_at').first()

            if not existing:
                return {'message': 'Nenhuma assinatura encontrada. Crie uma assinatura primeiro.'}, 404

            # new_plan.frequency/frequency_type usam o vocabulário amigável do domínio
            # (days/weeks/months/years); o Mercado Pago só aceita days/months.
            mp_new_frequency, mp_new_frequency_type = to_mercadopago_frequency(new_plan.frequency, new_plan.frequency_type)
            mp_plan_id = new_plan.mp_preapproval_plan_id
            was_canceled = existing.status == 'canceled'

            if was_canceled:
                # Subscription cancelada → cria nova preapproval (cliente precisa re-autorizar).
                # start_date=agora garante que a cobrança aconteça assim que o cliente
                # reautorizar, em vez de só no próximo ciclo.
                mp_subscription = MercadoPagoService.create_pending_subscription(
                    reason=new_plan.name,
                    payer_email=current_customer.email,
                    amount=new_plan.amount,
                    frequency=mp_new_frequency,
                    frequency_type=mp_new_frequency_type,
                    back_url=Config.MERCADOPAGO_URL_RETURN,
                    external_reference=str(current_customer.id),
                    metadata={
                        'customer_id': str(current_customer.id),
                        'company_id': str(current_customer.company_id.id),
                        'plan_id': str(new_plan.id),
                    },
                    start_date=_mp_start_date_now()
                )

                if not mp_subscription or mp_subscription.get('error'):
                    mp_msg = mp_subscription.get('message', '') if mp_subscription else ''
                    if 'real or test users' in mp_msg:
                        return {'message': 'Erro de ambiente: no modo sandbox o email do cliente deve ser de um usuário de teste do Mercado Pago. Em produção use o token APP- e emails reais.', 'mp_error': mp_msg}, 400
                    return {'message': mp_msg or 'Erro ao criar assinatura no Mercado Pago'}, 400

                new_mp_sub_id = mp_subscription['subscription_id']
                new_payment_url = mp_subscription['init_point']
                requires_authorization = True

            else:
                # Subscription ativa/pendente → atualiza a preapproval existente no MP
                if not existing.mp_subscription_id:
                    return {'message': 'ID da assinatura no Mercado Pago não encontrado'}, 400

                mp_updated = MercadoPagoService.update_subscription(
                    subscription_id=existing.mp_subscription_id,
                    plan_name=new_plan.name,
                    amount=new_plan.amount
                )
                
                if not mp_updated:
                    return {'message': 'Erro ao atualizar assinatura no Mercado Pago'}, 500

                new_mp_sub_id = existing.mp_subscription_id
                new_payment_url = existing.payment_url
                requires_authorization = False

                customer = Customer.objects(id=current_customer.id).first()
                customer.can_change_plan = False
                customer.save()

            # Atualiza o mesmo documento de assinatura no banco
            existing.mp_subscription_id = new_mp_sub_id
            existing.mp_preapproval_plan_id = mp_plan_id
            existing.plan_name = new_plan.name
            existing.amount = new_plan.amount
            existing.billing_cycle = mp_new_frequency_type
            existing.frequency = mp_new_frequency
            existing.currency = 'BRL'
            existing.payment_url = new_payment_url
            existing.status = 'pending' if was_canceled else existing.status
            existing.mp_status = 'pending' if was_canceled else existing.mp_status
            existing.failure_message = None
            existing.cancel_at_period_end = False
            existing.canceled_at = None
            existing.access_blocked = False
            existing.updated_by = None

            existing.save()

            action = 'reativada' if was_canceled else 'atualizada'
            logger.info(f"Subscription {action} for customer {current_customer.email}, plan: {new_plan.name}, MP ID: {new_mp_sub_id}")

            response_body = {
                'message': f'Assinatura {action} com sucesso.',
                'subscription_id': str(existing.id),
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'billing_cycle': new_plan.frequency_type,
                'mp_subscription_id': new_mp_sub_id,
                'requires_authorization': requires_authorization,
            }

            if requires_authorization:
                response_body['payment_url'] = new_payment_url
                response_body['message'] += ' Acesse o link para autorizar os pagamentos.'

            return response_body, 200

        except Exception as e:
            logger.error(f"Error updating subscription: {str(e)}")
            return {'message': 'Erro ao atualizar assinatura'}, 500

@api.route('/status')
class SubscriptionStatus(Resource):

    @api.doc('get_subscription_status')
    @customer_token_required
    def get(self, current_customer):
        """Status resumido da assinatura do cliente (para polling do app)"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                visible=True
            ).order_by('-created_at').first()

            if not subscription:
                return {
                    'has_subscription': False,
                    'status': None,
                    'mp_status': None,
                    'require_payment_method': current_customer.require_payment_method,
                }, 200

            return {
                'has_subscription': True,
                'status': subscription.status,
                'mp_status': subscription.mp_status,
                'require_payment_method': current_customer.require_payment_method,
            }, 200

        except Exception as e:
            logger.error(f"Error getting subscription status: {str(e)}")
            return {'message': 'Erro ao consultar status'}, 500

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

            customer = Customer.objects(id=current_customer.id).first()

            if not customer:
                return {'message': 'Cliente não encontrado'}, 404
            
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
            subscription.canceled_at = datetime.now(timezone.utc)
            subscription.cancel_at_period_end = True
            subscription.updated_by = None
            subscription.save()

            customer.can_change_plan = True
            customer.subscription_blocked = False
            customer.save()
            
            logger.info(f"Subscription canceled for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura cancelada com sucesso'
            }, 200
            
        except Exception as e:
            logger.error(f"Error canceling subscription: {str(e)}")
            return {'message': 'Erro ao cancelar assinatura'}, 500

@api.route('/statement')
class SubscriptionStatement(Resource):

    @api.doc('get_subscription_statement')
    @customer_token_required
    def get(self, current_customer):
        """Resumo e histórico de pagamentos da assinatura ativa do cliente"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                visible=True
            ).order_by('-created_at').first()

            if not subscription:
                return {'message': 'Nenhuma assinatura encontrada'}, 404

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            is_overdue = False
            days_overdue = 0
            days_until_block = None

            if subscription.current_period_end and now > subscription.current_period_end:
                is_overdue = True
                days_overdue = (now - subscription.current_period_end).days

                if subscription.grace_period_end:
                    if now > subscription.grace_period_end:
                        days_until_block = 0
                    else:
                        days_until_block = (subscription.grace_period_end - now).days

            payment_history = sorted(
                [p.to_dict() for p in (subscription.payment_history or [])],
                key=lambda p: p['paid_at'] or '',
                reverse=True
            )

            return {
                'summary': {
                    'plan_amount': subscription.amount,
                    'plan_name': subscription.plan_name,
                    'status': subscription.status,
                    'next_payment_date': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                    'grace_period_end': subscription.grace_period_end.isoformat() if subscription.grace_period_end else None,
                    'is_overdue': is_overdue,
                    'days_overdue': days_overdue,
                    'days_until_block': days_until_block,
                    'access_blocked': subscription.access_blocked
                },
                'payment_history': {
                    'total_payments': len(payment_history),
                    'payments': payment_history
                }
            }, 200

        except Exception as e:
            logger.error(f"Error getting subscription statement: {str(e)}")
            return {'message': 'Erro ao gerar extrato'}, 500
