import os
from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, SubscriptionPlan
from app.infrastructure.mercadopago_service import MercadoPagoService
from app.presentation.auth_routes import customer_token_required
from mongoengine import DoesNotExist
from datetime import datetime, timedelta, timezone
import logging
from config import Config

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
            
            # Step 2: Create or reuse Mercado Pago preapproval plan
            frequency = plan.frequency or 1
            frequency_type = plan.frequency_type or 'months'
            mp_plan_id = plan.mp_preapproval_plan_id

            if not mp_plan_id:
                mp_plan = MercadoPagoService.create_subscription_plan(
                    plan_name=plan.name,
                    amount=plan.amount,
                    frequency=frequency,
                    frequency_type=frequency_type
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
                frequency=frequency,
                frequency_type=frequency_type,
                back_url=Config.MERCADOPAGO_URL_RETURN,
                external_reference=str(current_customer.id),
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                }
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
                    billing_cycle=frequency_type,
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
                'billing_cycle': frequency_type,
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
        """Trocar de plano de assinatura"""
        try:
            data = request.get_json()
            
            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400
            
            # Fetch the new subscription plan
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
            
            # Find existing active subscription
            existing_subscription = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'pending'],
                visible=True
            ).first()

            if not existing_subscription:
                return {'message': 'Nenhuma assinatura ativa encontrada para alterar. Crie uma assinatura primeiro.'}, 404

            old_plan_name = existing_subscription.plan_name
            old_plan_amount = existing_subscription.amount

            # Step 1: Ensure MP plan ID exists for the new plan
            new_frequency = new_plan.frequency or 1
            new_frequency_type = new_plan.frequency_type or 'months'
            mp_plan_id = new_plan.mp_preapproval_plan_id

            if not mp_plan_id:
                mp_plan = MercadoPagoService.create_subscription_plan(
                    plan_name=new_plan.name,
                    amount=new_plan.amount,
                    frequency=new_frequency,
                    frequency_type=new_frequency_type
                )
                if not mp_plan:
                    return {'message': 'Erro ao criar plano de assinatura no Mercado Pago'}, 500

                mp_plan_id = mp_plan['plan_id']
                new_plan.mp_preapproval_plan_id = mp_plan_id
                new_plan.save()

            # Step 2: Create new MP subscription BEFORE canceling old one
            mp_subscription = MercadoPagoService.create_pending_subscription(
                reason=new_plan.name,
                payer_email=current_customer.email,
                amount=new_plan.amount,
                frequency=new_frequency,
                frequency_type=new_frequency_type,
                back_url=os.environ.get('APP_URL', 'https://www.rcminformatica.tec.br/'),
                external_reference=str(current_customer.id),
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(new_plan.id),
                }
            )

            if not mp_subscription or mp_subscription.get('error'):
                mp_msg = mp_subscription.get('message', '') if mp_subscription else ''
                if 'real or test users' in mp_msg:
                    return {'message': 'Erro de ambiente: no modo sandbox o email do cliente deve ser de um usuário de teste do Mercado Pago. Em produção use o token APP- e emails reais.', 'mp_error': mp_msg}, 400
                return {'message': mp_msg or 'Erro ao criar assinatura no Mercado Pago'}, 400

            # Step 3: Save new subscription to DB — if fails, cancel new MP subscription (old is untouched)
            new_mp_sub_id = mp_subscription['subscription_id']
            try:
                subscription = Subscription(
                    customer_id=current_customer,
                    company_id=current_customer.company_id,
                    mp_subscription_id=new_mp_sub_id,
                    mp_preapproval_plan_id=mp_plan_id,
                    plan_name=new_plan.name,
                    amount=new_plan.amount,
                    status='pending',
                    mp_status='pending',
                    billing_cycle=new_frequency_type,
                    currency='BRL',
                    payment_url=mp_subscription['init_point'],
                    created_by=None,
                    updated_by=None
                )
                subscription.save()
            except Exception as db_error:
                logger.error(f"DB save failed for new subscription, canceling new MP sub {new_mp_sub_id}: {db_error}")
                MercadoPagoService.cancel_subscription(new_mp_sub_id)
                return {'message': 'Erro ao salvar nova assinatura. Tente novamente.'}, 500

            # Step 4: Only NOW cancel the old subscription on MP (new is safely persisted)
            if existing_subscription.mp_subscription_id:
                success = MercadoPagoService.cancel_subscription(existing_subscription.mp_subscription_id)
                if not success:
                    logger.warning(f"Failed to cancel old MP subscription: {existing_subscription.mp_subscription_id}")

            # Step 5: Mark old subscription as canceled
            existing_subscription.status = 'canceled'
            existing_subscription.canceled_at = datetime.now(timezone.utc)
            existing_subscription.cancel_at_period_end = True
            existing_subscription.updated_by = None
            existing_subscription.save()

            current_customer.current_plan_name = new_plan.name
            current_customer.previous_plan_name = old_plan_name
            current_customer.previous_plan_amount = old_plan_amount
            current_customer.plan_changed_at = datetime.now(timezone.utc)
            current_customer.save()
            
            logger.info(f"Subscription plan changed for customer {current_customer.email}, new plan: {new_plan.name}, MP subscription ID: {mp_subscription['subscription_id']}")
            
            return {
                'message': 'Plano alterado com sucesso. Acesse o link para autorizar os pagamentos.',
                'subscription_id': str(subscription.id),
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'payment_url': mp_subscription['init_point'],
                'mp_subscription_id': mp_subscription['subscription_id'],
                'previous_plan': {
                    'plan_name': old_plan_name,
                    'amount': old_plan_amount
                },
                'changed_at': subscription.changed_at.isoformat() if subscription.changed_at else None
            }, 200
            
        except Exception as e:
            logger.error(f"Error changing subscription plan: {str(e)}")
            return {'message': 'Erro ao trocar de plano'}, 500

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
                    'payment_url': None,
                }, 200

            return {
                'has_subscription': True,
                'status': subscription.status,
                'mp_status': subscription.mp_status,
                'plan_name': subscription.plan_name,
                'amount': subscription.amount,
                'billing_cycle': subscription.billing_cycle,
                'payment_url': subscription.payment_url,
                'require_payment_method': current_customer.require_payment_method,
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
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
            
            logger.info(f"Subscription canceled for customer {current_customer.email}")
            
            return {
                'message': 'Assinatura cancelada com sucesso',
                'subscription': subscription.to_dict()
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
