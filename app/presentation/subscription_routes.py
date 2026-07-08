from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import Customer, Subscription, SubscriptionPlan, BILLING_CYCLE_PARAMS
from app.infrastructure.abacatepay_service import AbacatePayService
from app.presentation.auth_routes import customer_token_required
from datetime import datetime, timezone
import logging
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('subscriptions', description='Operações de assinatura e pagamento com AbacatePay')

subscription_create_model = api.model('SubscriptionCreate', {
    'plan_id': fields.String(required=True, description='ID do plano de assinatura cadastrado'),
})


def find_plan(plan_id_input, company_id):
    """Busca o plano pelo ObjectId do banco ou pelo abacatepay_product_id"""
    plan = None
    if len(plan_id_input) == 24:
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id_input,
                company_id=company_id,
                is_active=True,
                visible=True
            ).first()
        except Exception:
            pass

    if not plan:
        plan = SubscriptionPlan.objects(
            abacatepay_product_id=plan_id_input,
            company_id=company_id,
            is_active=True,
            visible=True
        ).first()

    return plan


def ensure_product(plan):
    """Garante que o plano tem um produto criado no AbacatePay, criando se necessário"""
    if plan.abacatepay_product_id:
        return plan.abacatepay_product_id

    product = AbacatePayService.create_product(
        external_id=str(plan.id),
        name=plan.name,
        amount=plan.amount,
        cycle=BILLING_CYCLE_PARAMS[plan.billing_cycle]['abacatepay_cycle'],
        description=plan.description,
    )

    if not product or product.get('error'):
        return None

    plan.abacatepay_product_id = product['id']
    plan.save()
    return plan.abacatepay_product_id


def ensure_abacatepay_customer(customer):
    """Garante que o customer tem um cliente criado no AbacatePay, criando se necessário"""
    if customer.abacatepay_customer_id:
        return customer.abacatepay_customer_id

    result = AbacatePayService.create_customer(
        name=customer.name,
        email=customer.email,
        tax_id=customer.document,
        cellphone=customer.phone,
    )

    if not result or result.get('error'):
        return None

    customer.abacatepay_customer_id = result['id']
    customer.save()
    return customer.abacatepay_customer_id


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

            plan = find_plan(data['plan_id'], current_customer.company_id)

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
                if pending_subscription.abacatepay_subscription_id:
                    AbacatePayService.cancel_subscription(pending_subscription.abacatepay_subscription_id)
                pending_subscription.delete()
                logger.info(f"Deleted previous pending subscription {pending_subscription.id} before creating new one")

            product_id = ensure_product(plan)
            if not product_id:
                return {'message': 'Erro ao criar produto no AbacatePay'}, 500

            customer = Customer.objects(id=current_customer.id).first()
            abacatepay_customer_id = ensure_abacatepay_customer(customer)
            if not abacatepay_customer_id:
                return {'message': 'Erro ao criar cliente no AbacatePay'}, 500

            checkout = AbacatePayService.create_subscription_checkout(
                product_id=product_id,
                customer_id=abacatepay_customer_id,
                external_id=str(current_customer.id),
                return_url=Config.ABACATEPAY_URL_RETURN,
                completion_url=Config.ABACATEPAY_URL_RETURN,
                metadata={
                    'customer_id': str(current_customer.id),
                    'company_id': str(current_customer.company_id.id),
                    'plan_id': str(plan.id),
                }
            )

            if not checkout or checkout.get('error'):
                msg = checkout.get('message', '') if checkout else ''
                return {'message': msg or 'Erro ao criar assinatura no AbacatePay'}, 400

            # Salvar no banco — se falhar, cancela no AbacatePay para evitar órfão
            try:
                subscription = Subscription(
                    customer_id=current_customer,
                    company_id=current_customer.company_id,
                    abacatepay_subscription_id=checkout['id'],
                    abacatepay_customer_id=abacatepay_customer_id,
                    abacatepay_product_id=product_id,
                    plan_name=plan.name,
                    amount=plan.amount,
                    status='pending',
                    abacatepay_status='pending',
                    billing_cycle=plan.billing_cycle,
                    currency='BRL',
                    payment_url=checkout['url'],
                    created_by=None,
                    updated_by=None
                )
                subscription.save()
            except Exception as db_error:
                logger.error(f"DB save failed, canceling AbacatePay subscription {checkout['id']}: {db_error}")
                AbacatePayService.cancel_subscription(checkout['id'])
                return {'message': 'Erro ao salvar assinatura. Tente novamente.'}, 500

            logger.info(f"Subscription created for customer {current_customer.email}, plan: {plan.name}, AbacatePay ID: {checkout['id']}")

            return {
                'message': 'Assinatura recorrente criada com sucesso',
                'subscription_id': str(subscription.id),
                'plan_name': plan.name,
                'amount': plan.amount,
                'billing_cycle': plan.billing_cycle,
                'payment_url': checkout['url'],
                'abacatepay_subscription_id': checkout['id'],
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
            data = request.get_json()

            if not data.get('plan_id'):
                return {'message': 'Campo plan_id é obrigatório'}, 400

            new_plan = find_plan(data['plan_id'], current_customer.company_id)

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

            product_id = ensure_product(new_plan)
            if not product_id:
                return {'message': 'Erro ao criar produto no AbacatePay'}, 500

            was_canceled = existing.status == 'canceled'

            if was_canceled:
                # Subscription cancelada → cria novo checkout (cliente precisa re-autorizar)
                customer = Customer.objects(id=current_customer.id).first()
                abacatepay_customer_id = ensure_abacatepay_customer(customer)
                if not abacatepay_customer_id:
                    return {'message': 'Erro ao criar cliente no AbacatePay'}, 500

                checkout = AbacatePayService.create_subscription_checkout(
                    product_id=product_id,
                    customer_id=abacatepay_customer_id,
                    external_id=str(current_customer.id),
                    return_url=Config.ABACATEPAY_URL_RETURN,
                    completion_url=Config.ABACATEPAY_URL_RETURN,
                    metadata={
                        'customer_id': str(current_customer.id),
                        'company_id': str(current_customer.company_id.id),
                        'plan_id': str(new_plan.id),
                    }
                )

                if not checkout or checkout.get('error'):
                    msg = checkout.get('message', '') if checkout else ''
                    return {'message': msg or 'Erro ao criar assinatura no AbacatePay'}, 400

                new_subscription_id = checkout['id']
                new_payment_url = checkout['url']
                requires_authorization = True

            else:
                # Subscription ativa → troca o produto na assinatura existente
                if not existing.abacatepay_subscription_id:
                    return {'message': 'ID da assinatura no AbacatePay não encontrado'}, 400

                changed = AbacatePayService.change_subscription_plan(
                    subscription_id=existing.abacatepay_subscription_id,
                    product_id=product_id,
                )

                if not changed or changed.get('error'):
                    return {'message': 'Erro ao atualizar assinatura no AbacatePay'}, 500

                new_subscription_id = existing.abacatepay_subscription_id
                new_payment_url = existing.payment_url
                requires_authorization = False

                customer = Customer.objects(id=current_customer.id).first()
                customer.can_change_plan = False
                customer.save()

            # Atualiza o mesmo documento de assinatura no banco
            existing.abacatepay_subscription_id = new_subscription_id
            existing.abacatepay_product_id = product_id
            existing.plan_name = new_plan.name
            existing.amount = new_plan.amount
            existing.billing_cycle = new_plan.billing_cycle
            existing.currency = 'BRL'
            existing.payment_url = new_payment_url
            existing.status = 'pending' if was_canceled else existing.status
            existing.abacatepay_status = 'pending' if was_canceled else existing.abacatepay_status
            existing.failure_message = None
            existing.cancel_at_period_end = False
            existing.canceled_at = None
            existing.access_blocked = False
            existing.updated_by = None

            existing.save()

            action = 'reativada' if was_canceled else 'atualizada'
            logger.info(f"Subscription {action} for customer {current_customer.email}, plan: {new_plan.name}, AbacatePay ID: {new_subscription_id}")

            response_body = {
                'message': f'Assinatura {action} com sucesso.',
                'subscription_id': str(existing.id),
                'plan_name': new_plan.name,
                'amount': new_plan.amount,
                'billing_cycle': new_plan.billing_cycle,
                'abacatepay_subscription_id': new_subscription_id,
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
                    'abacatepay_status': None,
                    'require_payment_method': current_customer.require_payment_method,
                }, 200

            return {
                'has_subscription': True,
                'status': subscription.status,
                'abacatepay_status': subscription.abacatepay_status,
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

            # Cancel on AbacatePay if subscription ID exists
            if subscription.abacatepay_subscription_id:
                success = AbacatePayService.cancel_subscription(subscription.abacatepay_subscription_id)
                if not success:
                    logger.warning(f"Failed to cancel subscription on AbacatePay: {subscription.abacatepay_subscription_id}")

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
