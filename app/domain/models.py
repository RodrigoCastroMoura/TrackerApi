from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine import *
from bson.objectid import ObjectId

class BaseDocument(Document):
    meta = {'abstract': True}
    created_at = DateTimeField(default=datetime.utcnow)
    created_by = ReferenceField('User', required=False)  # Optional to handle system-created records
    updated_at = DateTimeField(default=datetime.utcnow)
    updated_by = ReferenceField('User', required=False)

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        return super(BaseDocument, self).save(*args, **kwargs)

    def to_dict(self):
        """Base method for consistent dictionary representation"""
        return {
            'id': str(self.id) if self.id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': str(self.created_by.id) if self.created_by else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'updated_by': str(self.updated_by.id) if self.updated_by else None
        }

class Permission(BaseDocument):
    name = StringField(required=True, unique=True)
    description = StringField(required=True)
    resource_type = StringField(required=True)  # document, category, department, user, company
    action_type = StringField(required=True, choices=['read', 'write', 'update', 'delete'])
    meta = {'collection': 'permissions'}

    def to_dict(self):
        base_dict = super(Permission, self).to_dict()
        base_dict.update({
            'name': self.name,
            'description': self.description,
            'resource_type': self.resource_type,
            'action_type': self.action_type
        })
        return base_dict


class Company(BaseDocument):
    name = StringField(required=True, max_length=100, unique=True)
    meta = {'collection': 'companies'}

    def to_dict(self):
        base_dict = super(Company, self).to_dict()
        base_dict.update({
            'name': self.name
        })
        return base_dict


class User(BaseDocument):
    name = StringField(required=True, max_length=100)
    document = StringField(required=True, unique=True, max_length=25)
    matricula = StringField(unique=True, max_length=20)
    cpf = StringField(required=True, unique=True, max_length=11)
    email = StringField(required=True, unique=True, max_length=120)
    phone = StringField(max_length=15)
    password_hash = StringField(required=True, max_length=256)
    role = StringField(required=True, choices=['admin', 'user'])
    status = StringField(required=True, choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)  # Campo para exclusão lógica
    password_changed = BooleanField(default=False)  # Indica se o usuário já trocou a senha inicial
    permissions = ListField(ReferenceField(Permission))
    company_id = ReferenceField('Company', required=True)
    meta = {'collection': 'users'}

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_permission(self, resource_type, action_type):
        """Check if user has a specific permission"""
        if self.role == 'admin':
            return True
        return any(p.resource_type == resource_type and p.action_type == action_type 
                  for p in self.permissions)

    def to_dict(self):
        base_dict = super(User, self).to_dict()
        base_dict.update({
            'name': self.name,
            'document': self.document,
            'matricula': self.matricula,
            'cpf': self.cpf,
            'email': self.email,
            'phone': self.phone,
            'role': self.role,
            'status': self.status,
            'permissions': [p.to_dict() for p in self.permissions] if self.permissions else [],
            'company_id': str(self.company_id.id) if self.company_id else None
        })
        return base_dict


class Document(BaseDocument):
    name = StringField(required=True, max_length=100)
    titulo = StringField(required=True, max_length=200)
    url = StringField(required=True)
    user_id = ReferenceField(User, required=False)
    status = StringField(required=True, choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)
    meta = {
        'collection': 'documents',
        'indexes': [
            {'fields': ['titulo']},  # For text search
            {'fields': ['name']}     # For text search
        ]
    }

    def to_dict(self):
        base_dict = super(Document, self).to_dict()
        user_details = {
            'user_id': str(self.user_id.id),
            'user_name': self.user_id.name,
            'user_matricula': self.user_id.matricula,
            'user_cpf': self.user_id.cpf
        } if self.user_id else {
            'user_id': None,
            'user_name': None,
            'user_matricula': None,
            'user_cpf': None
        }

        base_dict.update({
            'name': self.name,
            'titulo': self.titulo,
            'url': self.url,
            **user_details,
            'status': self.status,
            'visible': self.visible,
        })
        return base_dict