#!/usr/bin/env python3
"""
Bootstrap script to create the first admin user for DocSmart API.
This script should only be run once during initial setup.

Usage:
    python bootstrap.py --email admin@example.com --password yourpassword --name "Admin Name"

Or run interactively:
    python bootstrap.py
"""

import argparse
import sys
import getpass
from mongoengine import connect, DoesNotExist
from app.domain.models import User, Permission
from config import Config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validate_email(email):
    """Basic email validation"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def create_admin_user(name, email, password, document=None):
    """Create the first admin user in the system"""
    try:
        # Check if admin already exists
        existing_admin = User.objects(role='admin').first()
        if existing_admin:
            logger.warning(f"An admin user already exists: {existing_admin.email}")
            response = input("Do you want to create another admin? (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                logger.info("Aborting. No user created.")
                return False
        
        # Validate email format
        if not validate_email(email):
            logger.error(f"Invalid email format: {email}")
            return False
        
        # Check if email already exists
        try:
            existing_user = User.objects.get(email=email.lower())
            logger.error(f"User with email {email} already exists")
            return False
        except DoesNotExist:
            pass
        
        # Use email as document if not provided
        if not document:
            document = email
        
        # Create admin user
        admin = User(
            name=name,
            email=email.lower(),
            document=document,
            role='admin',
            status='active',
            password_changed=True
        )
        admin.set_password(password)
        
        # Save the user
        admin.save()
        logger.info(f"✅ Admin user created successfully!")
        logger.info(f"   Name: {name}")
        logger.info(f"   Email: {email}")
        logger.info(f"   Role: admin")
        
        # Create default permissions if they don't exist
        create_default_permissions(admin)
        
        return True
        
    except Exception as e:
        logger.error(f"Error creating admin user: {str(e)}")
        return False

def create_default_permissions(admin_user=None):
    """Create default permissions for the system"""
    try:
        logger.info("Creating default permissions...")
        
        # Define resources
        resources = {
            'vehicle': 'Vehicle',
            'user': 'User',
            'customer': 'Customer',
            'permission': 'Permission'
        }
        
        # Define actions
        actions = {
            'read': 'View',
            'write': 'Create',
            'update': 'Edit',
            'delete': 'Delete'
        }
        
        created_permissions = []
        
        # Create permissions for each resource
        for resource_type, resource_name in resources.items():
            for action_type, action_desc in actions.items():
                permission_name = f'{resource_type}_{action_type}'
                description = f'{action_desc} {resource_name}'
                
                # Check if permission already exists
                try:
                    existing_permission = Permission.objects.get(name=permission_name)
                    logger.debug(f"Permission already exists: {permission_name}")
                    created_permissions.append(existing_permission)
                except DoesNotExist:
                    permission = Permission(
                        name=permission_name,
                        description=description,
                        resource_type=resource_type,
                        action_type=action_type
                    )
                    permission.save()
                    created_permissions.append(permission)
                    logger.info(f"Created permission: {permission_name}")
        
        # Assign all permissions to admin user
        if admin_user:
            admin_user.permissions = created_permissions
            admin_user.save()
            logger.info(f"✅ Assigned {len(created_permissions)} permissions to admin user")
        
        # Update all existing admin users with all permissions
        admin_users = User.objects(role='admin')
        for admin in admin_users:
            admin.permissions = created_permissions
            admin.save()
        
        logger.info(f"✅ Default permissions created successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error creating default permissions: {str(e)}")
        return False

def interactive_mode():
    """Run in interactive mode to collect user details"""
    print("\n=== DocSmart Admin User Bootstrap ===\n")
    
    name = input("Admin Name: ").strip()
    if not name:
        print("Error: Name is required")
        return False
    
    email = input("Admin Email: ").strip()
    if not email:
        print("Error: Email is required")
        return False
    
    if not validate_email(email):
        print(f"Error: Invalid email format: {email}")
        return False
    
    password = getpass.getpass("Admin Password: ")
    if not password:
        print("Error: Password is required")
        return False
    
    password_confirm = getpass.getpass("Confirm Password: ")
    if password != password_confirm:
        print("Error: Passwords do not match")
        return False
    
    if len(password) < 8:
        print("Warning: Password should be at least 8 characters long")
        response = input("Continue anyway? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            return False
    
    document = input("Admin Document/CPF (press Enter to use email): ").strip()
    
    print("\nCreating admin user...")
    return create_admin_user(name, email, password, document or None)

def main():
    parser = argparse.ArgumentParser(description='Bootstrap DocSmart API with first admin user')
    parser.add_argument('--name', help='Admin user full name')
    parser.add_argument('--email', help='Admin user email')
    parser.add_argument('--password', help='Admin user password')
    parser.add_argument('--document', help='Admin user document/CPF (optional)')
    parser.add_argument('--permissions-only', action='store_true', 
                       help='Only create/update permissions without creating user')
    
    args = parser.parse_args()
    
    try:
        # Connect to MongoDB
        logger.info("Connecting to MongoDB...")
        connect(host=Config.MONGODB_URI)
        logger.info("✅ Connected to MongoDB successfully")
        
        # Check if only permissions should be created
        if args.permissions_only:
            logger.info("Creating/updating permissions only...")
            success = create_default_permissions()
            sys.exit(0 if success else 1)
        
        # If all arguments provided, use them
        if args.name and args.email and args.password:
            success = create_admin_user(args.name, args.email, args.password, args.document)
            sys.exit(0 if success else 1)
        
        # If some arguments missing, use interactive mode
        elif args.name or args.email or args.password:
            print("Error: When using arguments, --name, --email, and --password are all required")
            print("Run without arguments for interactive mode")
            sys.exit(1)
        
        # Interactive mode
        else:
            success = interactive_mode()
            sys.exit(0 if success else 1)
            
    except Exception as e:
        logger.error(f"Bootstrap failed: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()
