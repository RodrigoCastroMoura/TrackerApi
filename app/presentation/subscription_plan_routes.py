from flask import request
from flask_restx import Namespace, Resource, fields
from app.domain.models import SubscriptionPlan, Company
from app.presentation.auth_routes import token_required, require_permission
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

api = Namespace('subscription-plans', description='Subscription plan management operations')

subscription_plan_model = api.model('SubscriptionPlan', {
    'name': fields.String(required=True, description='Plan name', example='Plano Básico'),
    'description': fields.String(description='Plan description', example='Até 10 veículos'),
    'amount': fields.Float(required=True, description='Monthly amount in BRL', example=99.90),
    'billing_cycle': fields.String(description='Billing cycle', enum=['monthly', 'yearly'], example='monthly'),
    'features': fields.List(fields.String, description='List of features', example=['Rastreamento em tempo real', 'Relatórios mensais']),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles', example=10),
    'is_active': fields.Boolean(description='If plan is available for new subscriptions', example=True)
})

subscription_plan_response = api.model('SubscriptionPlanResponse', {
    'id': fields.String(description='Plan ID'),
    'company_id': fields.String(description='Company ID'),
    'name': fields.String(description='Plan name'),
    'description': fields.String(description='Plan description'),
    'amount': fields.Float(description='Monthly amount in BRL'),
    'currency': fields.String(description='Currency'),
    'billing_cycle': fields.String(description='Billing cycle'),
    'mp_preapproval_plan_id': fields.String(description='Mercado Pago plan ID'),
    'features': fields.List(fields.String, description='List of features'),
    'max_vehicles': fields.Integer(description='Maximum number of vehicles'),
    'is_active': fields.Boolean(description='If plan is active'),
    'created_at': fields.String(description='Creation date'),
    'updated_at': fields.String(description='Last update date')
})

@api.route('/')
class SubscriptionPlanListResource(Resource):
    @api.doc('list_subscription_plans', security=None)
    @api.marshal_list_with(subscription_plan_response)
    def get(self):
        """List all active subscription plans (public endpoint for customers to see available plans)"""
        try:
            company_id = request.args.get('company_id')
            
            query = {'visible': True, 'is_active': True}
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
    
    @api.doc('create_subscription_plan', security='Bearer')
    @token_required
    @require_permission('subscription_plan', 'write')
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response, code=201)
    def post(current_user, self):
        """Create a new subscription plan (admin only)"""
        try:
            data = request.json
            
            if not data.get('name') or not data.get('amount'):
                return {'message': 'Name and amount are required'}, 400
            
            if data['amount'] <= 0:
                return {'message': 'Amount must be greater than zero'}, 400
            
            plan = SubscriptionPlan(
                company_id=current_user.company_id,
                name=data['name'],
                description=data.get('description', ''),
                amount=data['amount'],
                currency='BRL',
                billing_cycle=data.get('billing_cycle', 'monthly'),
                features=data.get('features', []),
                max_vehicles=data.get('max_vehicles'),
                is_active=data.get('is_active', True),
                created_by=current_user,
                updated_by=current_user
            )
            plan.save()
            
            logger.info(f"Subscription plan created: {plan.name} by user {current_user.email}")
            
            return plan.to_dict(), 201
            
        except Exception as e:
            logger.error(f"Error creating subscription plan: {str(e)}")
            return {'message': 'Error creating subscription plan'}, 500

@api.route('/<plan_id>')
@api.param('plan_id', 'The subscription plan identifier')
class SubscriptionPlanResource(Resource):
    @api.doc('get_subscription_plan', security=None)
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
    
    @api.doc('update_subscription_plan', security='Bearer')
    @token_required
    @require_permission('subscription_plan', 'update')
    @api.expect(subscription_plan_model)
    @api.marshal_with(subscription_plan_response)
    def put(current_user, self, plan_id):
        """Update a subscription plan (admin only)"""
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
            if 'billing_cycle' in data:
                plan.billing_cycle = data['billing_cycle']
            if 'features' in data:
                plan.features = data['features']
            if 'max_vehicles' in data:
                plan.max_vehicles = data['max_vehicles']
            if 'is_active' in data:
                plan.is_active = data['is_active']
            
            plan.updated_by = current_user
            plan.save()
            
            logger.info(f"Subscription plan updated: {plan.name} by user {current_user.email}")
            
            return plan.to_dict(), 200
            
        except Exception as e:
            logger.error(f"Error updating subscription plan: {str(e)}")
            return {'message': 'Error updating subscription plan'}, 500
    
    @api.doc('delete_subscription_plan', security='Bearer')
    @token_required
    @require_permission('subscription_plan', 'delete')
    def delete(current_user, self, plan_id):
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
