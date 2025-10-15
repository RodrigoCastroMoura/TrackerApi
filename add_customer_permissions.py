import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.domain.models import User, Permission
from mongoengine import connect
from config import Config

# Connect to MongoDB
connect(host=Config.MONGODB_URI)

# Create customer permissions
customer_permissions = [
    {'name': 'customer_read', 'description': 'View Customer', 'resource_type': 'customer', 'action_type': 'read'},
    {'name': 'customer_write', 'description': 'Create Customer', 'resource_type': 'customer', 'action_type': 'write'},
    {'name': 'customer_update', 'description': 'Edit Customer', 'resource_type': 'customer', 'action_type': 'update'},
    {'name': 'customer_delete', 'description': 'Delete Customer', 'resource_type': 'customer', 'action_type': 'delete'},
]

print("Creating customer permissions...")
created_perm_ids = []
for perm_data in customer_permissions:
    existing = Permission.objects(name=perm_data['name']).first()
    if not existing:
        perm = Permission(**perm_data)
        perm.save()
        created_perm_ids.append(perm.id)
        print(f"✓ Created permission: {perm_data['name']}")
    else:
        created_perm_ids.append(existing.id)
        print(f"✓ Permission already exists: {perm_data['name']}")

# Find rodrigo.moura user
user = User.objects(document='rodrigo.moura').first()
if user:
    print(f"\nFound user: {user.name} ({user.email})")
    print(f"User company_id: {user.company_id}")
    
    # Get current permission IDs
    current_perm_ids = [p.id for p in user.permissions] if user.permissions else []
    
    # Add new permissions
    for perm_id in created_perm_ids:
        if perm_id not in current_perm_ids:
            current_perm_ids.append(perm_id)
    
    # Update using MongoDB update
    User.objects(id=user.id).update_one(set__permissions=current_perm_ids)
    
    # Reload user to show updated permissions
    user.reload()
    print(f"\n✓ Updated permissions for {user.name}")
    print(f"Total permissions: {len(user.permissions)}")
    for p in user.permissions:
        print(f"  - {p.name}")
else:
    print("\n✗ User rodrigo.moura not found")
