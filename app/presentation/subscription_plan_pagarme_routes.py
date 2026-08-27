from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import SubscriptionPlan, Company, FREQUENCY_TYPES, to_pagarme_interval
from app.presentation.auth_routes import token_required, require_permission
from app.presentation.subscription_plan_routes import parse_frequency
from app.infrastructure.pagarme_service import PagarmeService
import logging

logger = logging.getLogger(__name__)

api = Namespace('subscription-plans-pagarme', description='Gestão de planos de assinatura no Pagar.me (Stone)')


def _amount_cents(amount) -> int:
    return int(round((amount or 0) * 100))


subscription_plan_model = api.model('SubscriptionPlanPagarme', {
    'name': fields.String(required=True, description='Plan name', example='Plano Básico'),
    'description': fields.String(description='Plan description', example='Até 10 veículos'),
    'amount': fields.Float(required=True, description='Amount in BRL', example=39.99),
    'frequency': fields.Integer(description='Multiplicador do ciclo (ex: 2 + frequency_type=weeks = a cada 2 semanas)', example=1),
    'frequency_type': fields.String(description='Unidade do ciclo de cobrança', enum=list(FREQUENCY_TYPES), example='months'),
    'features': fields.List(fields.String, description='List of features', example=['Rastreamento em tempo real']),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles', example=10),
    'free_days': fields.Integer(description='Dias de trial antes da primeira cobrança', example=0),
    'is_active': fields.Boolean(description='If plan is available for new subscriptions', example=True),
})

subscription_plan_response = api.model('SubscriptionPlanPagarmeResponse', {
    'id': fields.String(description='Plan ID (local)'),
    'company_id': fields.String(description='Company ID'),
    'name': fields.String(description='Plan name'),
    'description': fields.String(description='Plan description'),
    'amount': fields.Float(description='Amount in BRL'),
    'currency': fields.String(description='Currency'),
    'frequency': fields.Integer(description='Billing frequency'),
    'frequency_type': fields.String(description='Frequency type'),
    'provider_plan_id': fields.String(description='ID do plano no Pagar.me (plan_...)'),
    'features': fields.List(fields.String, description='List of features'),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles'),
    'free_days': fields.Integer(description='Dias de trial antes da primeira cobrança'),
    'is_active': fields.Boolean(description='If plan is active'),
    'visible': fields.Boolean(description='If plan is visible'),
    'created_at': fields.String(description='Creation date'),
    'updated_at': fields.String(description='Last update date'),
})


def _create_pagarme_plan(plan) -> tuple:
    """Cria o plano no Pagar.me a partir de um SubscriptionPlan local já salvo.
    Retorna (plan_id, error_response_or_None)."""
    interval_count, interval = to_pagarme_interval(plan.frequency, plan.frequency_type)
    result = PagarmeService.create_plan(
        name=plan.name,
        amount_cents=_amount_cents(plan.amount),
        interval=interval,
        interval_count=interval_count,
        trial_period_days=plan.free_days or 0,
    )
    if not result or result.get('error'):
        msg = result.get('message', '') if result else ''
        return None, ({'message': msg or 'Erro ao criar plano no Pagar.me'}, 502)
    return result['plan_id'], None


@api.route('/')
class SubscriptionPlanPagarmeListResource(Resource):

    @api.doc('list_subscription_plans_pagarme', security=None)
    @api.marshal_list_with(subscription_plan_response)
    def get(self):
        """Lista os planos já sincronizados com o Pagar.me (endpoint público)"""
        try:
            company_id = request.args.get('company_id')
            query = {'visible': True, 'provider_plan_id__startswith': 'plan_'}
            if company_id:
                company = Company.objects(id=company_id, visible=True).first()
                if not company:
                    return {'message': 'Company not found'}, 404
                query['company_id'] = company

            plans = SubscriptionPlan.objects(**query)
            return [plan.to_dict() for plan in plans], 200
        except Exception as e:
            logger.error(f"Error listing Pagar.me subscription plans: {str(e)}")
            return {'message': 'Error listing subscription plans'}, 500

    @api.doc('create_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response, code=201)
    def post(self, current_user):
        """Cria um plano local e o plano correspondente no Pagar.me (admin)"""
        try:
            data = request.json or {}

            if not data.get('name') or not data.get('amount'):
                return {'message': 'Name and amount are required'}, 400
            if data['amount'] <= 0:
                return {'message': 'Amount must be greater than zero'}, 400

            frequency, frequency_type = parse_frequency(data)
            if frequency_type is None:
                return {'message': f"frequency_type inválido: {data.get('frequency_type')}. Use days, weeks, months ou years."}, 400

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
                updated_by=current_user,
            )
            plan.save()

            plan_id, err = _create_pagarme_plan(plan)
            if err:
                # Sem plano remoto o registro local não serve para assinatura Pagar.me
                plan.visible = False
                plan.is_active = False
                plan.save()
                return err

            plan.provider_plan_id = plan_id
            plan.save()

            logger.info(f"Pagar.me plan created: {plan_id} for plan {plan.name} by {current_user.email}")
            return plan.to_dict(), 201
        except Exception as e:
            logger.error(f"Error creating Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error creating subscription plan'}, 500


@api.route('/<plan_id>')
@api.param('plan_id', 'The subscription plan identifier (local)')
class SubscriptionPlanPagarmeResource(Resource):

    @api.doc('get_subscription_plan_pagarme', security=None)
    @api.marshal_with(subscription_plan_response)
    def get(self, plan_id):
        """Detalhe de um plano (endpoint público)"""
        try:
            plan = SubscriptionPlan.objects(id=plan_id, visible=True).first()
            if not plan:
                return {'message': 'Subscription plan not found'}, 404
            return plan.to_dict(), 200
        except Exception as e:
            logger.error(f"Error getting Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error getting subscription plan'}, 500

    @api.doc('update_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response)
    def put(self, current_user, plan_id):
        """Atualiza os campos locais do plano (admin).

        Não re-sincroniza valor/intervalo com o Pagar.me — o plano remoto é
        imutável nesses campos; troca de valor é feita na assinatura."""
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id, company_id=current_user.company_id, visible=True
            ).first()
            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            data = request.json or {}
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

            logger.info(f"Pagar.me subscription plan updated: {plan.name} by {current_user.email}")
            return plan.to_dict(), 200
        except Exception as e:
            logger.error(f"Error updating Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error updating subscription plan'}, 500

    @api.doc('delete_subscription_plan_pagarme', security='Bearer')
    @token_required
    @require_permission('subscription_plan', 'delete')
    def delete(self, current_user, plan_id):
        """Soft delete do plano local (admin)"""
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id, company_id=current_user.company_id, visible=True
            ).first()
            if not plan:
                return {'message': 'Subscription plan not found'}, 404

            plan.visible = False
            plan.is_active = False
            plan.updated_by = current_user
            plan.save()

            logger.info(f"Pagar.me subscription plan deleted: {plan.name} by {current_user.email}")
            return {'message': 'Subscription plan deleted successfully'}, 200
        except Exception as e:
            logger.error(f"Error deleting Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error deleting subscription plan'}, 500


@api.route('/<plan_id>/sync')
@api.param('plan_id', 'The subscription plan identifier (local)')
class SubscriptionPlanPagarmeSyncResource(Resource):

    @api.doc('sync_subscription_plan_pagarme', security='Bearer')
    @token_required
    @api.marshal_with(subscription_plan_response)
    def post(self, current_user, plan_id):
        """Cria no Pagar.me o plano correspondente a um SubscriptionPlan que já
        existe localmente (ex.: criado antes só para o Mercado Pago)."""
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
                    'message': 'Este plano já está vinculado a outro provedor (Mercado Pago). '
                               'Crie um plano dedicado para o Pagar.me em POST /api/subscription-plans-pagarme.'
                }, 409

            plan_id_remote, err = _create_pagarme_plan(plan)
            if err:
                return err

            plan.provider_plan_id = plan_id_remote
            plan.updated_by = current_user
            plan.save()

            logger.info(f"Pagar.me plan synced: {plan_id_remote} for local plan {plan.id} by {current_user.email}")
            return plan.to_dict(), 200
        except Exception as e:
            logger.error(f"Error syncing Pagar.me subscription plan: {str(e)}")
            return {'message': 'Error syncing subscription plan'}, 500
