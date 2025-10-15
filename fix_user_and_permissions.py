import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.domain.models import User, Permission, Company
from mongoengine import connect
from bson import ObjectId
from config import Config

# Connect to MongoDB
connect(host=Config.MONGODB_URI)

print("=== FIXING USER AND PERMISSIONS ===\n")

# Find or create a company
company = Company.objects.first()
if not company:
    print("Creating default company...")
    company = Company(
        name="Empresa Teste",
        cnpj="00.000.000/0001-00",
        email="contato@empresa.com",
        phone="11999999999",
        status="active"
    )
    company.save()
    print(f"✓ Created company: {company.name}")
else:
    print(f"✓ Using existing company: {company.name} (ID: {company.id})")

# Get all permissions
all_permissions = list(Permission.objects.all())
print(f"\n✓ Found {len(all_permissions)} permissions in database")

# Find rodrigo.moura user
user = User.objects(document='rodrigo.moura').first()
if user:
    print(f"\n✓ Found user: {user.name} ({user.email})")
    print(f"  Current company_id: {user.company_id}")
    
    # Update user with company and all permissions using raw update
    User.objects(id=user.id).update_one(
        set__company_id=company.id,
        set__permissions=[p.id for p in all_permissions]
    )
    
    print(f"\n✓ Updated user {user.name}:")
    print(f"  - company_id: {company.id}")
    print(f"  - Total permissions: {len(all_permissions)}")
    
    # List permissions
    for p in all_permissions:
        print(f"    • {p.name} ({p.resource_type}:{p.action_type})")
        
    print("\n✓ User configuration complete!")
else:
    print("\n✗ User rodrigo.moura not found")
