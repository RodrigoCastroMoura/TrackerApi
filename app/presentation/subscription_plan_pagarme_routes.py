from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import SubscriptionPlan, Company, FREQUENCY_TYPES, to_provider_interval
from app.presentation.auth_routes import token_required, require_permission
from app.presentation.subscription_plan_routes import parse_frequency
from app.infrastructure.pagarme_service import PagarmeService
import logging

logger = logging.getLogger(__name__)

# Mesmo contrato (request/response) do namespace subscription-plans (Stripe) — a
# aplicação só precisa trocar o path de /api/subscription-plans para
# /api/subscription-plans-pagarme. A única diferença é o provedor onde o plano
# remoto é criado (Pagar.me em vez de Stripe).
api = Namespace('subscription-plans-pagarme', description='Subscription plan management operations (Pagar.me / Stone)')


def _amount_cents(amount) -> int:
    return int(round((amount or 0) * 100))


subscription_plan_model = api.model('SubscriptionPlanPagarme', {
    'name': fields.String(required=True, description='Plan name', example='Plano Básico'),
    'description': fields.String(description='Plan description', example='Até 10 veículos'),
    'amount': fields.Float(required=True, description='Amount in BRL', example=39.99),
    'frequency': fields.Integer(description='Multiplicador do ciclo de cobrança (ex: 2 + frequency_type=weeks = a cada 2 semanas)', example=1),
    'frequency_type': fields.String(
        description='Unidade do ciclo de cobrança',
        enum=list(FREQUENCY_TYPES),
        example='months'
    ),
    'features': fields.List(fields.String, description='List of features', example=['Rastreamento em tempo real']),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles', example=10),
    'free_days': fields.Integer(description='Number of free trial days before first billing', example=0),
    'is_active': fields.Boolean(description='If plan is available for new subscriptions', example=True)
})

subscription_plan_response = api.model('SubscriptionPlanPagarmeResponse', {
    'id': fields.String(description='Plan ID'),
    'company_id': fields.String(description='Company ID'),
    'name': fields.String(description='Plan name'),
    'description': fields.String(description='Plan description'),
    'amount': fields.Float(description='Amount in BRL'),
    'currency': fields.String(description='Currency'),
    'frequency': fields.Integer(description='Billing frequency'),
    'frequency_type': fields.String(description='Frequency type'),
    'provider_plan_id': fields.String(description='ID do plano no provedor de pagamento'),
    'features': fields.List(fields.String, description='List of features'),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles'),
    'free_days': fields.Integer(description='Number of free trial days before first billing'),
    'is_active': fields.Boolean(description='If plan is active'),
    'visible': fields.Boolean(description='If plan is visible'),
    'created_at': fields.String(description='Creation date'),
    'updated_at': fields.String(description='Last update date')
})


def _create_pagarme_plan(plan):
    """Cria o plano no Pagar.me a partir de um SubscriptionPlan (salvo ou não).

    Retorna (plan_id, None) em caso de sucesso, ou (None, (body, status)) com uma
    mensagem de erro amigável quando a criação no Pagar.me falha.
    """
    interval_count, interval = to_provider_interval(plan.frequency, plan.frequency_type)
    result = PagarmeService.create_plan(
        name=plan.name,
        amount_cents=_amount_cents(plan.amount),
        interval=interval,
        interval_count=interval_count,
        trial_period_days=plan.free_days or 0,
    )

    if result and not result.get('error') and result.get('plan_id'):
        return result['plan_id'], None

    motivo = (result or {}).get('message') or 'serviço indisponível no momento'
    logger.warning(f"Falha ao criar plano no Pagar.me para '{plan.name}': {motivo}")
    return None, ({
        'message': f'Não foi possível criar o plano no Pagar.me ({motivo}). '
                   f'O plano não foi salvo — verifique os dados e tente novamente.'
    }, 502)


@api.route('/')
class SubscriptionPlanPagarmeListResource(Resource):
    @api.doc('list_subscription_plans_pagarme', security=None)
    @api.marshal_list_with(subscription_plan_response)
    def get(self):
        """List all active subscription plans (public endpoint)"""
        try:
            company_id = request.args.get('company_id')

            query = {'visible': True}
            if company_id:
                company = Company.objects(id=company_id, visible=True).first()
                if not company:
                    return {'message': 'Company not found'}, 404
                query['company_id'] = company

            plans = SubscriptionPlan.objects(**query)
            return [plan.to_dict() for plan in plans], 200

        except Exception as e:
            logger.error(f"Error listing subscription plans: {str(e)}")
            return {'message': 'Error listing subscription plans'}, 500

    @api.doc('create_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response, code=201)
    def post(self, current_user):
        """Create a new subscription plan (admin only)"""
        try:
            data = request.json

            if not data.get('name') or not data.get('amount'):
                return {'message': 'Name and amount are required'}, 400

            if data['amount'] <= 0:
                return {'message': 'Amount must be greater than zero'}, 400

            frequency, frequency_type = parse_frequency(data)
            if frequency_type is None:
                return {'message': f"frequency_type inválido: {data.get('frequency_type')}. Use days, weeks, months ou years."}, 400

            # Monta o plano em memória (sem persistir) para gerar o payload do Pagar.me.
            plan = SubscriptionPlan(
                company_id=current_user.company_id,
                name=data['name'],
                description=data.get('description', ''),
                amount=data['amount'],
                currency='BRL',
                frequency=frequency,
                frequency_type=frequency_type,
                features=data.get('features', []),
                max_vehicles=data.get('max_vehicles'),
                free_days=data.get('free_days', 0),
                is_active=data.get('is_active', True),
                created_by=current_user,
                updated_by=current_user
            )

            # 1º cria no Pagar.me; só grava na base se der certo.
            provider_plan_id, err = _create_pagarme_plan(plan)
            if err:
                return err

            plan.provider_plan_id = provider_plan_id
            plan.save()

            logger.info(f"Pagar.me plan created: {provider_plan_id} for plan {plan.name}")
            logger.info(f"Subscription plan created: {plan.name} by user {current_user.email}")

            return plan.to_dict(), 201

        except Exception as e:
            logger.error(f"Error creating subscription plan: {str(e)}")
            return {'message': 'Error creating subscription plan'}, 500


@api.route('/<plan_id>')
@api.param('plan_id', 'The subscription plan identifier')
class SubscriptionPlanPagarmeResource(Resource):
    @api.doc('get_subscription_plan_pagarme', security=None)
    @api.marshal_with(subscription_plan_response)
    def get(self, plan_id):
        """Get subscription plan details (public endpoint)"""
        try:
            plan = SubscriptionPlan.objects(id=plan_id, visible=True).first()

            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            return plan.to_dict(), 200

        except Exception as e:
            logger.error(f"Error getting subscription plan: {str(e)}")
            return {'message': 'Error getting subscription plan'}, 500

    @api.doc('update_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response)
    def put(self, current_user, plan_id):
        """Update a subscription plan (admin only).

        Igual ao fluxo da Stripe: atualiza só os campos locais. O plano remoto no
        Pagar.me é imutável em valor/intervalo — troca de valor é feita na
        assinatura."""
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id,
                company_id=current_user.company_id,
                visible=True
            ).first()

            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            data = request.json

            if 'name' in data:
                plan.name = data['name']
            if 'description' in data:
                plan.description = data['description']
            if 'amount' in data:
                if data['amount'] <= 0:
                    return {'message': 'Amount must be greater than zero'}, 400
                plan.amount = data['amount']
            if 'frequency' in data or 'frequency_type' in data:
                merged = {
                    'frequency': data.get('frequency', plan.frequency),
                    'frequency_type': data.get('frequency_type', plan.frequency_type),
                }
                new_frequency, new_frequency_type = parse_frequency(merged)
                if new_frequency_type is None:
                    return {'message': f"frequency_type inválido: {data.get('frequency_type')}. Use days, weeks, months ou years."}, 400
                plan.frequency = new_frequency
                plan.frequency_type = new_frequency_type
            if 'features' in data:
                plan.features = data['features']
            if 'max_vehicles' in data:
                plan.max_vehicles = data['max_vehicles']
            if 'free_days' in data:
                plan.free_days = data['free_days']
            if 'is_active' in data:
                plan.is_active = data['is_active']

            plan.updated_by = current_user
            plan.save()

            logger.info(f"Subscription plan updated: {plan.name} by user {current_user.email}")

            return plan.to_dict(), 200

        except Exception as e:
            logger.error(f"Error updating subscription plan: {str(e)}")
            return {'message': 'Error updating subscription plan'}, 500

    @api.doc('delete_subscription_plan_pagarme', security='Bearer')
    @token_required
    @require_permission('subscription_plan', 'delete')
    def delete(self, current_user, plan_id):
        """Delete a subscription plan (soft delete, admin only)"""
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id,
                company_id=current_user.company_id,
                visible=True
            ).first()

            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            plan.visible = False
            plan.is_active = False
            plan.updated_by = current_user
            plan.save()

            logger.info(f"Subscription plan deleted: {plan.name} by user {current_user.email}")

            return {'message': 'Subscription plan deleted successfully'}, 200

        except Exception as e:
            logger.error(f"Error deleting subscription plan: {str(e)}")
            return {'message': 'Error deleting subscription plan'}, 500


@api.route('/int/<max_vehicles>')
@api.param('max_vehicles', 'The maximum number of vehicles for the subscription plan')
class SubscriptionPlanPagarmeMaxVehiclesResource(Resource):
    @api.doc('list_subscription_plans_pagarme_by_max_vehicles', security=None)
    @api.marshal_list_with(subscription_plan_response)
    def get(self, max_vehicles):
        """List subscription plans by max_vehicles (public endpoint)"""
        try:
            try:
                max_vehicles_int = int(max_vehicles)
            except (ValueError, TypeError):
                return {'message': 'Invalid max_vehicles value. Must be an integer.'}, 400

            plans = SubscriptionPlan.objects(max_vehicles=max_vehicles_int, is_active=True)

            return [plan.to_dict() for plan in plans], 200

        except Exception as e:
            logger.error(f"Error listing subscription plans by max_vehicles: {str(e)}")
            return {'message': 'Error listing subscription plans'}, 500


@api.route('/<plan_id>/sync')
@api.param('plan_id', 'The subscription plan identifier (local)')
class SubscriptionPlanPagarmeSyncResource(Resource):
    """Extra (não existe no namespace da Stripe): cria no Pagar.me o plano remoto
    de um SubscriptionPlan que já existe localmente sem provider_plan_id do
    Pagar.me. A aplicação pode ignorar este endpoint."""

    @api.doc('sync_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.marshal_with(subscription_plan_response)
    def post(self, current_user, plan_id):
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id, company_id=current_user.company_id, visible=True
            ).first()
            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            existing = plan.provider_plan_id
            if existing and existing.startswith('plan_'):
                return plan.to_dict(), 200
            if existing and not existing.startswith('plan_'):
                return {
                    'message': 'Este plano já está vinculado a outro provedor (Stripe). '
                               'Crie um plano dedicado em POST /api/subscription-plans-pagarme.'
                }, 409

            provider_plan_id, err = _create_pagarme_plan(plan)
            if err:
                return err

            plan.provider_plan_id = provider_plan_id
            plan.updated_by = current_user
            plan.save()

            logger.info(f"Pagar.me plan synced: {provider_plan_id} for local plan {plan.id} by {current_user.email}")
            return plan.to_dict(), 200
        except Exception as e:
            logger.error(f"Error syncing Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error syncing subscription plan'}, 500
