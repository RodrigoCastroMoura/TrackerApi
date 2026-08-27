from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import (
    Customer,
    Subscription,
    SubscriptionPlan,
    to_mercadopago_frequency,
)
from app.infrastructure.pagarme_service import PagarmeService
from app.presentation.auth_routes import customer_token_required
from datetime import datetime, timezone
import logging
from config import Config

logger = logging.getLogger(__name__)

api = Namespace(
    'subscriptions-pagarme',
    description='Operações de assinatura e pagamento com Pagar.me (Stone)',
)

subscription_create_model = api.model('SubscriptionPagarmeCreate', {
    'plan_id': fields.String(required=True, description='ID do plano de assinatura cadastrado'),
})


def _amount_cents(amount: float) -> int:
    return int(round((amount or 0) * 100))


def _resolve_plan(plan_id_input, company_id):
    """Aceita ObjectId de 24 chars ou o provider_plan_id (mesma regra do MP)."""
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


def _require_pagarme_plan(plan):
    """Exige que o SubscriptionPlan já tenha um plano correspondente no Pagar.me,
    identificado por provider_plan_id começando com 'plan_'. Retorna
    (plan_id, error_response_or_None).

    A criação do plano no Pagar.me é responsabilidade do endpoint dedicado
    /api/subscription-plans-pagarme (POST para criar, POST /<id>/sync para
    vincular um plano local já existente) — este fluxo só consome.
    """
    existing = plan.provider_plan_id
    if existing and existing.startswith('plan_'):
        return existing, None
    if existing and not existing.startswith('plan_'):
        return None, ({
            'message': 'Este plano está vinculado a outro provedor de pagamento '
                       '(Mercado Pago). Use um plano criado em /api/subscription-plans-pagarme.'
        }, 409)
    return None, ({
        'message': 'Plano ainda não sincronizado com o Pagar.me. Crie-o em '
                   'POST /api/subscription-plans-pagarme ou sincronize um plano '
                   'existente em POST /api/subscription-plans-pagarme/<plan_id>/sync.'
    }, 409)


@api.route('/')
class SubscriptionPagarmeResource(Resource):

    @api.doc('create_subscription_pagarme')
    @api.expect(subscription_create_model)
    @customer_token_required
    def post(self, current_customer):
        """Criar assinatura Pagar.me a partir de um plano cadastrado (checkout via Payment Link)"""
        try:
            data = request.get_json() or {}
            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            plan = _resolve_plan(data['plan_id'], current_customer.company_id)
            if not plan:
                return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

            # Bloqueia se já tem assinatura ativa/pendente de pagamento
            active_subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
            ).first()
            if active_subscription and active_subscription.status in ['active', 'pendingPayment']:
                return {'message': 'Já existe uma assinatura ativa ou pendente para este cliente'}, 400

            plan_id, err = _require_pagarme_plan(plan)
            if err:
                return err

            mp_frequency, mp_frequency_type = to_mercadopago_frequency(
                plan.frequency, plan.frequency_type
            )

            # Cria a Subscription local ANTES do payment link para ter o id de
            # correlação (metadata.local_subscription_id). O provider_subscription_id
            # (id sub_... do Pagar.me) é preenchido pelo webhook subscription.created.
            subscription = Subscription(
                customer_id=current_customer,
                company_id=current_customer.company_id,
                provider_plan_id=plan_id,
                plan_name=plan.name,
                amount=plan.amount,
                status='pending',
                provider_status='pending',
                billing_cycle=mp_frequency_type,
                frequency=mp_frequency,
                currency='BRL',
                created_by=None,
                updated_by=None,
            )
            subscription.save()

            link = PagarmeService.create_subscription_payment_link(
                plan_id=plan_id,
                local_subscription_id=str(subscription.id),
                customer_id=str(current_customer.id),
                company_id=str(current_customer.company_id.id),
            )
            if not link or link.get('error'):
                # Evita Subscription órfã se o Pagar.me falhar
                subscription.visible = False
                subscription.status = 'canceled'
                subscription.save()
                msg = link.get('message', '') if link else ''
                return {'message': msg or 'Erro ao criar link de pagamento no Pagar.me'}, 502

            subscription.payment_url = link['url']
            subscription.save()

            logger.info(
                f"Pagar.me subscription created for customer {current_customer.email}, "
                f"plan: {plan.name}, local id: {subscription.id}"
            )

            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'plan_name': plan.name,
                'amount': plan.amount,
                'billing_cycle': plan.frequency_type,
                'payment_url': link['url'],
                'instructions': 'Acesse o link para autorizar os pagamentos recorrentes',
            }, 201

        except Exception as e:
            logger.error(f"Error creating Pagar.me subscription: {str(e)}")
            return {'message': 'Erro ao criar assinatura'}, 500

    @api.doc('get_my_subscription_pagarme')
    @customer_token_required
    def get(self, current_customer):
        """Consultar assinatura mais recente do customer autenticado"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
            ).order_by('-created_at').first()
            if not subscription:
                return {'message': 'Nenhuma assinatura encontrada'}, 404
            return subscription.to_dict(), 200
        except Exception as e:
            logger.error(f"Error getting Pagar.me subscription: {str(e)}")
            return {'message': 'Erro ao consultar assinatura'}, 500

    @api.doc('change_subscription_plan_pagarme')
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
                visible=True,
            ).order_by('-created_at').first()
            if not existing:
                return {'message': 'Nenhuma assinatura encontrada. Crie uma assinatura primeiro.'}, 404

            plan_id, err = _require_pagarme_plan(new_plan)
            if err:
                return err

            mp_frequency, mp_frequency_type = to_mercadopago_frequency(
                new_plan.frequency, new_plan.frequency_type
            )
            was_canceled = existing.status == 'canceled'

            if was_canceled:
                # Assinatura cancelada -> cliente precisa reautorizar via novo payment link
                link = PagarmeService.create_subscription_payment_link(
                    plan_id=plan_id,
                    local_subscription_id=str(existing.id),
                    customer_id=str(current_customer.id),
                    company_id=str(current_customer.company_id.id),
                )
                if not link or link.get('error'):
                    msg = link.get('message', '') if link else ''
                    return {'message': msg or 'Erro ao criar link de pagamento no Pagar.me'}, 502

                existing.provider_subscription_id = None
                existing.payment_url = link['url']
                existing.status = 'pending'
                existing.provider_status = 'pending'
                requires_authorization = True
            else:
                # Assinatura ativa -> atualiza o valor do item sem reautorização
                if not existing.provider_subscription_id:
                    return {'message': 'ID da assinatura no Pagar.me ainda não disponível'}, 400

                items = PagarmeService.get_subscription_items(existing.provider_subscription_id)
                if not items:
                    return {'message': 'Não foi possível obter os itens da assinatura no Pagar.me'}, 502

                item_id = items[0].get('id')
                updated = PagarmeService.update_subscription_pricing(
                    subscription_id=existing.provider_subscription_id,
                    item_id=item_id,
                    amount_cents=_amount_cents(new_plan.amount),
                    name=new_plan.name,
                )
                if not updated:
                    return {'message': 'Erro ao atualizar assinatura no Pagar.me'}, 502

                requires_authorization = False
                customer = Customer.objects(id=current_customer.id).first()
                customer.can_change_plan = False
                customer.save()

            existing.provider_plan_id = plan_id
            existing.plan_name = new_plan.name
            existing.amount = new_plan.amount
            existing.billing_cycle = mp_frequency_type
            existing.frequency = mp_frequency
            existing.currency = 'BRL'
            existing.failure_message = None
            existing.cancel_at_period_end = False
            existing.canceled_at = None
            existing.updated_by = None
            existing.save()

            action = 'reativada' if was_canceled else 'atualizada'
            logger.info(
                f"Pagar.me subscription {action} for customer {current_customer.email}, "
                f"plan: {new_plan.name}"
            )

            body = {
                'message': f'Assinatura {action} com sucesso.',
                'subscription_id': str(existing.id),
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'billing_cycle': new_plan.frequency_type,
                'requires_authorization': requires_authorization,
            }
            if requires_authorization:
                body['payment_url'] = existing.payment_url
                body['message'] += ' Acesse o link para autorizar os pagamentos.'
            return body, 200

        except Exception as e:
            logger.error(f"Error updating Pagar.me subscription: {str(e)}")
            return {'message': 'Erro ao atualizar assinatura'}, 500


@api.route('/status')
class SubscriptionPagarmeStatus(Resource):

    @api.doc('get_subscription_status_pagarme')
    @customer_token_required
    def get(self, current_customer):
        """Status resumido da assinatura do cliente (para polling do app)"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
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
            logger.error(f"Error getting Pagar.me subscription status: {str(e)}")
            return {'message': 'Erro ao consultar status'}, 500


@api.route('/cancel')
class SubscriptionPagarmeCancel(Resource):

    @api.doc('cancel_subscription_pagarme')
    @customer_token_required
    def post(self, current_customer):
        """Cancelar assinatura ativa do customer"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id,
                status__in=['active', 'pending'],
                visible=True,
            ).first()

            customer = Customer.objects(id=current_customer.id).first()
            if not customer:
                return {'message': 'Cliente não encontrado'}, 404
            if not subscription:
                return {'message': 'Nenhuma assinatura ativa encontrada'}, 404
            if subscription.status == 'canceled':
                return {'message': 'Assinatura já está cancelada'}, 400

            if subscription.provider_subscription_id:
                success = PagarmeService.cancel_subscription(subscription.provider_subscription_id)
                if not success:
                    logger.warning(
                        f"Failed to cancel subscription on Pagar.me: {subscription.provider_subscription_id}"
                    )

            subscription.status = 'canceled'
            subscription.canceled_at = datetime.now(timezone.utc)
            subscription.save()

            logger.info(f"Pagar.me subscription canceled for customer {current_customer.email}")
            return {'message': 'Assinatura cancelada com sucesso'}, 200
        except Exception as e:
            logger.error(f"Error canceling Pagar.me subscription: {str(e)}")
            return {'message': 'Erro ao cancelar assinatura'}, 500


@api.route('/statement')
class SubscriptionPagarmeStatement(Resource):

    @api.doc('get_subscription_statement_pagarme')
    @customer_token_required
    def get(self, current_customer):
        """Resumo e histórico de pagamentos da assinatura ativa do cliente"""
        try:
            subscription = Subscription.objects(
                customer_id=current_customer.id, visible=True
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
                reverse=True,
            )

            return {
                'summary': {
                    'plan_amount': subscription.amount,
                    'plan_name': subscription.plan_name,
                    'status': subscription.status,
                    'next_payment_date': subscription.current_period_end.isoformat()
                    if subscription.current_period_end else None,
                    'grace_period_end': subscription.grace_period_end.isoformat()
                    if subscription.grace_period_end else None,
                    'is_overdue': is_overdue,
                    'days_overdue': days_overdue,
                    'days_until_block': days_until_block,
                },
                'payment_history': {
                    'total_payments': len(payment_history),
                    'payments': payment_history,
                },
            }, 200
        except Exception as e:
            logger.error(f"Error getting Pagar.me subscription statement: {str(e)}")
            return {'message': 'Erro ao gerar extrato'}, 500
