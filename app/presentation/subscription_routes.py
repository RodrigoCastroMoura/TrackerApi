from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import (
    Customer,
    Subscription,
    SubscriptionPlan,
    to_provider_frequency,
    to_provider_interval,
)
from app.infrastructure.stripe_service import StripeService
from app.presentation.auth_routes import customer_token_required
from bson import ObjectId
from datetime import datetime, timezone
import logging
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento com Stripe')


def _amount_cents(amount) -> int:
    return int(round((amount or 0) * 100))


def _resolve_plan(plan_id_input, company_id):
    """Aceita ObjectId de 24 chars ou o provider_plan_id (price_... da Stripe)."""
    plan = None
    if plan_id_input and len(plan_id_input) == 24:
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id_input, company_id=company_id, is_active=True, visible=True
            ).first()
        except Exception:
            plan = None
    if not plan:
        plan = SubscriptionPlan.objects(
            provider_plan_id=plan_id_input,
            company_id=company_id,
            is_active=True,
            visible=True,
        ).first()
    return plan


def _ensure_stripe_price(plan):
    """Garante que o SubscriptionPlan tenha um Price na Stripe (provider_plan_id
    price_...). Cria sob demanda, como o fluxo do Mercado Pago fazia. Retorna
    (price_id, None) ou (None, (body, status))."""
    price_id = plan.provider_plan_id
    if price_id and price_id.startswith('price_'):
        return price_id, None

    interval_count, interval = to_provider_interval(plan.frequency, plan.frequency_type)
    result = StripeService.create_plan(
        name=plan.name,
        amount_cents=_amount_cents(plan.amount),
        interval=interval,
        interval_count=interval_count,
        trial_period_days=plan.free_days or 0,
    )
    if not result or result.get('error'):
        motivo = (result or {}).get('message') or 'serviço indisponível no momento'
        return None, ({'message': f'Erro ao criar plano de assinatura na Stripe ({motivo})'}, 502)

    price_id = result['plan_id']
    plan.provider_plan_id = price_id
    plan.save()
    return price_id, None


subscription_create_model = api.model('SubscriptionCreate', {
    'plan_id': fields.String(required=True, description='ID do plano de assinatura cadastrado'),
})


@api.route('/')
class SubscriptionResource(Resource):

    @api.doc('create_subscription')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar assinatura a partir de um plano cadastrado (checkout hospedado da Stripe)"""
        try:
            data = request.get_json() or {}

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            plan = _resolve_plan(data['plan_id'], current_customer.company_id)
            if not plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            price_id, err = _ensure_stripe_price(plan)
            if err:
                return err

            # Bloqueia se já tem assinatura ativa/pendente de pagamento
            active_subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
            ).first()

            if active_subscription and active_subscription.status in ['active', 'pendingPayment']:
                return {'message': 'Já existe uma assinatura ativa ou pendente para este cliente'}, 400

            frequency, billing_cycle = to_provider_frequency(plan.frequency, plan.frequency_type)

            # O id local é gerado agora (sem persistir se ainda não existir doc) para
            # servir de chave de correlação no metadata do checkout — o webhook
            # checkout.session.completed usa esse local_subscription_id e preenche o
            # provider_subscription_id (sub_...).
            local_id = active_subscription.id if (
                active_subscription and active_subscription.status in ['canceled', 'pending']
            ) else ObjectId()

            session = StripeService.create_checkout_subscription(
                price_id=price_id,
                customer_email=current_customer.email,
                local_subscription_id=str(local_id),
                success_url=Config.STRIPE_SUCCESS_URL,
                cancel_url=Config.STRIPE_CANCEL_URL,
                trial_period_days=plan.free_days or 0,
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                },
            )
            if not session or session.get('error'):
                motivo = (session or {}).get('message') or 'serviço indisponível no momento'
                logger.warning(
                    f"Falha ao criar checkout na Stripe para {current_customer.email}: {motivo}"
                )
                return {
                    'message': f'Não foi possível iniciar a assinatura na Stripe ({motivo}). '
                               f'A assinatura não foi criada — tente novamente em instantes.'
                }, 502

            if active_subscription and active_subscription.status in ['canceled', 'pending']:
                # Se havia uma assinatura pendente com sub_... na Stripe, cancela para
                # não deixar assinatura órfã.
                if active_subscription.status == 'pending' and active_subscription.provider_subscription_id:
                    StripeService.cancel_subscription(active_subscription.provider_subscription_id)

                active_subscription.provider_subscription_id = None
                active_subscription.provider_plan_id = price_id
                active_subscription.plan_name = plan.name
                active_subscription.amount = plan.amount
                active_subscription.status = 'pending'
                active_subscription.provider_status = 'pending'
                active_subscription.billing_cycle = billing_cycle
                active_subscription.frequency = frequency
                active_subscription.currency = 'BRL'
                active_subscription.payment_url = session['url']
                active_subscription.failure_message = None
                active_subscription.cancel_at_period_end = False
                active_subscription.canceled_at = None
                active_subscription.updated_by = None

                try:
                    active_subscription.save()
                    logger.info(
                        f"Reactivated subscription {active_subscription.id} for customer {current_customer.email}"
                    )
                    return {
                        'message': 'Assinatura criada com sucesso',
                        'subscription_id': str(active_subscription.id),
                        'plan_name': plan.name,
                        'amount': plan.amount,
                        'billing_cycle': plan.frequency_type,
                        'payment_url': session['url'],
                        'instructions': 'Acesse o link para autorizar os pagamentos recorrentes'
                    }, 200
                except Exception as db_error:
                    logger.error(f"DB save failed while reactivating subscription: {db_error}")
                    return {'message': 'Erro ao reativar assinatura. Tente novamente.'}, 500

            # Cria novo documento de assinatura
            try:
                subscription = Subscription(
                    id=local_id,
                    customer_id=current_customer,
                    company_id=current_customer.company_id,
                    provider_plan_id=price_id,
                    plan_name=plan.name,
                    amount=plan.amount,
                    status='pending',
                    provider_status='pending',
                    billing_cycle=billing_cycle,
                    frequency=frequency,
                    currency='BRL',
                    payment_url=session['url'],
                    created_by=None,
                    updated_by=None
                )
                subscription.save()
            except Exception as db_error:
                logger.error(f"DB save failed for Stripe subscription: {db_error}")
                return {'message': 'Erro ao salvar assinatura. Tente novamente.'}, 500

            logger.info(
                f"Subscription created for customer {current_customer.email}, plan: {plan.name}, "
                f"local id: {subscription.id}"
            )

            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'plan_name': plan.name,
                'amount': plan.amount,
                'billing_cycle': plan.frequency_type,
                'payment_url': session['url'],
                'instructions': 'Acesse o link para autorizar os pagamentos recorrentes'
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
            data = request.get_json() or {}

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            new_plan = _resolve_plan(data['plan_id'], current_customer.company_id)
            if not new_plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            existing = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'canceled'],
                visible=True
            ).order_by('-created_at').first()

            if not existing:
                return {'message': 'Nenhuma assinatura encontrada. Crie uma assinatura primeiro.'}, 404

            price_id, err = _ensure_stripe_price(new_plan)
            if err:
                return err

            frequency, billing_cycle = to_provider_frequency(new_plan.frequency, new_plan.frequency_type)
            was_canceled = existing.status == 'canceled'

            if was_canceled:
                # Assinatura cancelada -> cliente precisa reautorizar via novo checkout
                session = StripeService.create_checkout_subscription(
                    price_id=price_id,
                    customer_email=current_customer.email,
                    local_subscription_id=str(existing.id),
                    success_url=Config.STRIPE_SUCCESS_URL,
                    cancel_url=Config.STRIPE_CANCEL_URL,
                    trial_period_days=new_plan.free_days or 0,
                    metadata={
                        'customer_id': str(current_customer.id),
                        'company_id': str(current_customer.company_id.id),
                        'plan_id': str(new_plan.id),
                    },
                )
                if not session or session.get('error'):
                    motivo = (session or {}).get('message') or 'serviço indisponível no momento'
                    return {'message': f'Erro ao criar checkout na Stripe ({motivo})'}, 502

                existing.provider_subscription_id = None
                existing.payment_url = session['url']
                existing.status = 'pending'
                existing.provider_status = 'pending'
                requires_authorization = True
            else:
                # Assinatura ativa -> troca o Price sem reautorização
                if not existing.provider_subscription_id:
                    return {'message': 'ID da assinatura na Stripe ainda não disponível'}, 400

                updated = StripeService.update_subscription_price(
                    subscription_id=existing.provider_subscription_id,
                    new_price_id=price_id,
                )
                if not updated:
                    return {'message': 'Erro ao atualizar assinatura na Stripe'}, 502

                requires_authorization = False
                customer = Customer.objects(id=current_customer.id).first()
                customer.can_change_plan = False
                customer.save()

            existing.provider_plan_id = price_id
            existing.plan_name = new_plan.name
            existing.amount = new_plan.amount
            existing.billing_cycle = billing_cycle
            existing.frequency = frequency
            existing.currency = 'BRL'
            existing.failure_message = None
            existing.cancel_at_period_end = False
            existing.canceled_at = None
            existing.updated_by = None
            existing.save()

            action = 'reativada' if was_canceled else 'atualizada'
            logger.info(
                f"Subscription {action} for customer {current_customer.email}, plan: {new_plan.name}"
            )

            response_body = {
                'message': f'Assinatura {action} com sucesso.',
                'subscription_id': str(existing.id),
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'billing_cycle': new_plan.frequency_type,
                'requires_authorization': requires_authorization,
            }

            if requires_authorization:
                response_body['payment_url'] = existing.payment_url
                response_body['message'] += ' Acesse o link para autorizar os pagamentos.'

            return response_body, 200

        except Exception as e:
            logger.error(f"Error updating subscription: {str(e)}")
            return {'message': 'Erro ao atualizar assinatura'}, 500


@api.route('/elements')
class SubscriptionElements(Resource):

    @api.doc('create_subscription_elements')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar assinatura para a tela nativa do app (Stripe Elements / PaymentSheet).

        Devolve client_secret + publishable_key. A Subscription na Stripe já nasce
        aqui (estado incomplete); a ativação vem pelo webhook invoice.paid /
        customer.subscription.updated após o app confirmar o cartão."""
        try:
            data = request.get_json() or {}

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            plan = _resolve_plan(data['plan_id'], current_customer.company_id)
            if not plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            price_id, err = _ensure_stripe_price(plan)
            if err:
                return err

            active_subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
            ).first()
            if active_subscription and active_subscription.status in ['active', 'pendingPayment']:
                return {'message': 'Já existe uma assinatura ativa ou pendente para este cliente'}, 400

            frequency, billing_cycle = to_provider_frequency(plan.frequency, plan.frequency_type)

            reuse = bool(
                active_subscription and active_subscription.status in ['canceled', 'pending']
            )
            local_id = active_subscription.id if reuse else ObjectId()

            result = StripeService.create_subscription_elements(
                price_id=price_id,
                customer_email=current_customer.email,
                local_subscription_id=str(local_id),
                trial_period_days=plan.free_days or 0,
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                },
            )
            if not result or result.get('error'):
                motivo = (result or {}).get('message') or 'serviço indisponível no momento'
                logger.warning(
                    f"Falha ao criar assinatura Elements na Stripe para {current_customer.email}: {motivo}"
                )
                return {'message': f'Não foi possível iniciar a assinatura na Stripe ({motivo}).'}, 502

            fields_ = dict(
                provider_subscription_id=result['subscription_id'],
                provider_customer_id=result.get('customer_id'),
                provider_plan_id=price_id,
                plan_name=plan.name,
                amount=plan.amount,
                status='pending',
                provider_status='pending',
                billing_cycle=billing_cycle,
                frequency=frequency,
                currency='BRL',
                payment_url=None,
                failure_message=None,
                cancel_at_period_end=False,
                canceled_at=None,
                updated_by=None,
            )

            try:
                if reuse:
                    for k, v in fields_.items():
                        setattr(active_subscription, k, v)
                    subscription = active_subscription
                else:
                    subscription = Subscription(
                        id=local_id,
                        customer_id=current_customer,
                        company_id=current_customer.company_id,
                        created_by=None,
                        **fields_,
                    )
                subscription.save()
            except Exception as db_error:
                logger.error(f"DB save failed for Stripe elements subscription: {db_error}")
                StripeService.cancel_subscription(result['subscription_id'])
                return {'message': 'Erro ao salvar assinatura. Tente novamente.'}, 500

            logger.info(
                f"Elements subscription created for customer {current_customer.email}, "
                f"plan: {plan.name}, Stripe id: {result['subscription_id']}"
            )

            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'provider_subscription_id': result['subscription_id'],
                'client_secret': result.get('client_secret'),
                'publishable_key': result.get('publishable_key'),
                'plan_name': plan.name,
                'amount': plan.amount,
                'billing_cycle': plan.frequency_type,
            }, 201

        except Exception as e:
            logger.error(f"Error creating elements subscription: {str(e)}")
            return {'message': 'Erro ao criar assinatura'}, 500

    @api.doc('change_subscription_plan_elements')
    @api.expect(subscription_create_model)
    @customer_token_required
    def put(self, current_customer):
        """Trocar o plano da assinatura na tela nativa (Stripe Elements / PaymentSheet)
        e ativá-la.

        Troca o Price na assinatura existente da Stripe. Quando houver valor a pagar
        (upgrade / proração) devolve um novo `client_secret` para o app confirmar a
        cobrança — a ativação definitiva vem pelo webhook. Quando a Stripe cobra na
        hora (cartão já salvo, mesmo valor, downgrade sem saldo) a assinatura já volta
        `active` e `requires_action` é `false`."""
        try:
            data = request.get_json() or {}

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            new_plan = _resolve_plan(data['plan_id'], current_customer.company_id)
            if not new_plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
            ).order_by('-created_at').first()
            if not subscription:
                return {'message': 'Nenhuma assinatura encontrada. Crie uma assinatura primeiro.'}, 404

            if subscription.status == 'canceled':
                return {
                    'message': 'Assinatura cancelada — crie uma nova assinatura (POST /subscriptions/elements).'
                }, 400

            if not subscription.provider_subscription_id:
                return {'message': 'ID da assinatura na Stripe ainda não disponível'}, 400

            price_id, err = _ensure_stripe_price(new_plan)
            if err:
                return err

            frequency, billing_cycle = to_provider_frequency(
                new_plan.frequency, new_plan.frequency_type
            )

            result = StripeService.change_plan_elements(
                subscription_id=subscription.provider_subscription_id,
                new_price_id=price_id,
            )
            if not result or result.get('error'):
                motivo = (result or {}).get('message') or 'serviço indisponível no momento'
                logger.warning(
                    f"Falha ao trocar plano (Elements) na Stripe para {current_customer.email}: {motivo}"
                )
                return {'message': f'Não foi possível trocar o plano na Stripe ({motivo}).'}, 502

            requires_action = bool(result.get('requires_action'))
            activated = result.get('status') in ('active', 'trialing')

            subscription.provider_plan_id = price_id
            subscription.plan_name = new_plan.name
            subscription.amount = new_plan.amount
            subscription.billing_cycle = billing_cycle
            subscription.frequency = frequency
            subscription.currency = 'BRL'
            subscription.cancel_at_period_end = False
            subscription.canceled_at = None
            subscription.updated_by = None

            if activated:
                subscription.status = 'active'
                subscription.provider_status = 'succeeded'
                subscription.failure_message = None
            else:
                # aguardando o app confirmar o pagamento do novo plano
                subscription.status = 'pendingPayment'
                subscription.provider_status = 'pending'
            subscription.save()

            customer = Customer.objects(id=current_customer.id).first()
            if customer:
                customer.can_change_plan = False
                if activated:
                    customer.require_payment_method = False
                customer.save()

            logger.info(
                f"Elements subscription plan changed for customer {current_customer.email}, "
                f"plan: {new_plan.name}, activated: {activated}, requires_action: {requires_action}"
            )

            response_body = {
                'message': (
                    'Plano atualizado e assinatura ativada com sucesso.'
                    if activated else
                    'Plano atualizado. Confirme o pagamento no app para ativar o novo plano.'
                ),
                'subscription_id': str(subscription.id),
                'provider_subscription_id': subscription.provider_subscription_id,
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'billing_cycle': new_plan.frequency_type,
                'status': subscription.status,
                'requires_action': requires_action,
            }
            if requires_action:
                response_body['client_secret'] = result.get('client_secret')
                response_body['publishable_key'] = Config.STRIPE_PUBLISHABLE_KEY

            return response_body, 200

        except Exception as e:
            logger.error(f"Error changing elements subscription plan: {str(e)}")
            return {'message': 'Erro ao trocar plano da assinatura'}, 500


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
                    'provider_status': None,
                    'require_payment_method': current_customer.require_payment_method,
                }, 200

            return {
                'has_subscription': True,
                'status': subscription.status,
                'provider_status': subscription.provider_status,
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

            if subscription.status == 'canceled':
                return {'message': 'Assinatura já está cancelada'}, 400

            # Cancela na Stripe se houver ID da assinatura
            if subscription.provider_subscription_id:
                success = StripeService.cancel_subscription(subscription.provider_subscription_id)
                if not success:
                    logger.warning(
                        f"Failed to cancel subscription on Stripe: {subscription.provider_subscription_id}"
                    )

            subscription.status = 'canceled'
            subscription.canceled_at = datetime.now(timezone.utc)
            subscription.save()

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
                    'days_until_block': days_until_block
                },
                'payment_history': {
                    'total_payments': len(payment_history),
                    'payments': payment_history
                }
            }, 200

        except Exception as e:
            logger.error(f"Error getting subscription statement: {str(e)}")
            return {'message': 'Erro ao gerar extrato'}, 500
