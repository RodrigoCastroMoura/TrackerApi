from flask import Flask
from flask_restx import Api
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from app.infrastructure.database import init_app
from app.presentation.auth_routes import api as auth_ns, limiter
from app.presentation.user_routes import api as user_ns
from app.presentation.permission_routes import api as permission_ns
from app.presentation.link_token_routes import api as link_token_ns
from app.presentation.vehicle_routes import api as vehicle_ns
from app.presentation.customer_routes import api as customer_ns
from app.presentation.tracking_routes import api as tracking_ns
from app.presentation.alert_routes import api as alert_ns
from app.presentation.report_routes import api as report_ns
from app.domain.models import User, Permission
from config import Config
import os
import logging
import sys
import pymongo


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format=
    '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log')
    ])

logger = logging.getLogger(__name__)

def verify_mongodb_connection():
    """Verify MongoDB connection is working"""
    try:
        logger.debug("Verifying MongoDB connection...")
        mongodb_uri = Config.MONGODB_URI
        if not mongodb_uri:
            logger.error("MONGODB_URI not set in config.py")
            return False

        logger.debug(f"Attempting to connect to MongoDB...")
        client = pymongo.MongoClient(mongodb_uri,
                                     serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        logger.info("MongoDB connection verified successfully")
        return True
    except Exception as e:
        logger.error(f"MongoDB connection error: {str(e)}")
        return False


def create_default_permissions():
    """Create default permissions with simplified operations (read, write, update, delete)"""
    try:
        # Define resources
        resources = {
            'vehicle': 'Vehicle',
            'user': 'User'
        }

        # Define simplified actions
        actions = {
            'read': 'View',
            'write': 'Create',
            'update': 'Edit',
            'delete': 'Delete'
        }

        # Create permissions for each resource
        for resource_type, resource_name in resources.items():
            for action_type, action_desc in actions.items():
                permission_name = f'{resource_type}_{action_type}'
                description = f'{action_desc} {resource_name}'

                # Check if permission already exists
                existing_permission = Permission.objects(
                    name=permission_name).first()
                if not existing_permission:
                    permission = Permission(name=permission_name,
                                            description=description,
                                            resource_type=resource_type,
                                            action_type=action_type)
                    permission.save()
                    logger.info(
                        f"Created permission: {permission_name} - {description}"
                    )
                else:
                    logger.debug(
                        f"Permission already exists: {permission_name}")

        # Update all admin users to have the new permissions
        all_permissions = Permission.objects.all()
        admin_users = User.objects(role='admin')
        for admin in admin_users:
            admin.permissions = list(all_permissions)
            admin.save()
            logger.info(f"Updated permissions for admin user: {admin.email}")
    except Exception as e:
        logger.error(f"Error creating default permissions: {str(e)}")
        raise


def create_app():
    """Create and configure the Flask application"""
    try:
        app = Flask(__name__)
        app.config.from_object(Config)

        if not verify_mongodb_connection():
            logger.error("Failed to verify MongoDB connection")
            return None

        # Initialize CORS
        CORS(app, 
             origins=Config.CORS_ORIGINS,
             supports_credentials=True,
             allow_headers=['Content-Type', 'Authorization'],
             methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
        logger.info(f"CORS enabled for origins: {Config.CORS_ORIGINS}")

        # Initialize database
        init_app(app)

        # Initialize Flask-Mail
        from app.infrastructure.email_service import mail
        mail.init_app(app)

        # Create default permissions
        #create_default_permissions()

        # Create API
        authorizations = {
            'Bearer Auth': {
                'type': 'apiKey',
                'in': 'header',
                'name': 'Authorization',
                'description':
                'Add a JWT token to the header with Bearer prefix.'
            }
        }

        api = Api(app,
                  title='Sistema de Rastreamento Veicular - API',
                  version='2.0',
                  description='API completa para gerenciamento de rastreamento veicular multi-tenant com alertas e relatórios.',
                  authorizations=authorizations,
                  security='Bearer Auth')

        # Initialize limiter with storage URL from config
        limiter.init_app(app)
        if Config.RATELIMIT_STORAGE_URL.startswith('memory://'):
            logger.warning("⚠️  Rate limiting using in-memory storage - not recommended for production!")
            logger.warning("   Set RATELIMIT_STORAGE_URL environment variable to use Redis/Memcached")
        else:
            logger.info(f"Rate limiting configured with: {Config.RATELIMIT_STORAGE_URL}")

        # Add namespaces
        api.add_namespace(auth_ns, path='/api/auth')
        api.add_namespace(user_ns, path='/api/users')
        api.add_namespace(permission_ns, path='/api/permissions')
        api.add_namespace(vehicle_ns, path='/api/vehicles')
        api.add_namespace(customer_ns, path='/api/customers')
        api.add_namespace(link_token_ns, path='/api/links')
        api.add_namespace(tracking_ns, path='/api/tracking')
        api.add_namespace(alert_ns, path='/api/alerts')
        api.add_namespace(report_ns, path='/api/reports')

        return app
    except Exception as e:
        logger.error(f"Error creating Flask application: {str(e)}")
        return None


if __name__ == '__main__':
    app = create_app()
    if app:
        logger.info("Starting Flask application...")
        app.run(host='0.0.0.0', port=Config.PORT)
    else:
        logger.error("Failed to create Flask application")
        sys.exit(1)