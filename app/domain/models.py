from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine import *
from bson.objectid import ObjectId
from typing import Optional

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

class Company(BaseDocument):
    """Company model for multi-tenancy"""
    name = StringField(required=True, max_length=200)
    cnpj = StringField(unique=True, max_length=18)  # CNPJ da empresa
    email = StringField(max_length=120)
    phone = StringField(max_length=15)
    status = StringField(choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)
    
    meta = {
        'collection': 'companies',
        'indexes': [
            {'fields': ['cnpj'], 'unique': True, 'sparse': True},
        ]
    }
    
    def to_dict(self):
        base_dict = super(Company, self).to_dict()
        base_dict.update({
            'name': self.name,
            'cnpj': self.cnpj,
            'email': self.email,
            'phone': self.phone,
            'status': self.status,
            'visible': self.visible
        })
        return base_dict

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

class User(BaseDocument):
    name = StringField(required=True, max_length=100)
    document = StringField(required=True, unique=True, max_length=25)
    matricula = StringField(unique=True, max_length=20, sparse=True)
    cpf = StringField(max_length=14)  # CPF do usuário
    email = StringField(required=True, unique=True, max_length=120)
    phone = StringField(max_length=15)
    password_hash = StringField(required=True, max_length=256)
    role = StringField(required=True, choices=['admin', 'user'])
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy
    status = StringField(required=True, choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)  # Campo para exclusão lógica
    password_changed = BooleanField(default=False)  # Indica se o usuário já trocou a senha inicial
    permissions = ListField(ReferenceField(Permission))
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
            'company_id': str(self.company_id.id) if self.company_id else None,
            'status': self.status,
            'permissions': [p.to_dict() for p in self.permissions] if self.permissions else []
        })
        return base_dict

class Vehicle(BaseDocument):
    """Vehicle information model - estrutura conforme solicitado"""
    # Campo obrigatório
    IMEI = StringField(required=True, max_length=50)
    dsplaca = StringField(max_length=10)  # Placa do veículo
    dsmodelo = StringField(max_length=100)  # Modelo do veículo
    dsmarca = StringField(max_length=100)  # Marca do veículo
    tipo = StringField(max_length=50, choices=['carro', 'moto', 'caminhao', 'van', 'onibus', 'outro'])  # Tipo do veículo
    ano = IntField()  # Ano do veículo
    customer_id = ReferenceField('Customer')  # Cliente associado ao veículo
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy
    comandobloqueo = BooleanField(default=None)  # True = bloquear, False = desbloquear, None = sem comando
    bloqueado = BooleanField(default=False)  # Status atual de bloqueio
    comandotrocarip = BooleanField(default=None)  # True = comando para trocar IP pendente
    ignicao = BooleanField(default=False)  # Status da ignição
    bateriavoltagem = FloatField()  # Voltagem atual da bateria
    bateriabaixa = BooleanField(default=False)  # True se bateria estiver baixa
    ultimoalertabateria = DateTimeField()  # Timestamp do último alerta
    status = StringField(choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)  # Campo para exclusão lógica

    meta = {
        'collection': 'vehicles',
        'indexes': [
            # Use explicit names to avoid conflicts
            {'fields': ['IMEI'], 'unique': True, 'name': 'idx_vehicle_imei_unique'},
            {'fields': ['dsplaca'], 'unique': True, 'name': 'idx_vehicle_placa_unique', 'sparse': True},
        ]
    }
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        base_dict = super(Vehicle, self).to_dict()

        base_dict.update({
            'IMEI': self.IMEI,
            'dsplaca': self.dsplaca,
            'dsmodelo': self.dsmodelo,
            'tipo': self.tipo,
            'ano': self.ano,
            'dsmarca': self.dsmarca,
            'customer_id': str(self.customer_id.id) if self.customer_id else None,
            'company_id': str(self.company_id.id) if self.company_id else None,
            'comandobloqueo': self.comandobloqueo,
            'bloqueado': self.bloqueado,
            'comandotrocarip': self.comandotrocarip,
            'ignicao': self.ignicao,
            'bateriavoltagem': self.bateriavoltagem,
            'bateriabaixa': self.bateriabaixa,
            'ultimoalertabateria': self.ultimoalertabateria.isoformat() if self.ultimoalertabateria else None,
            'status': self.status,
            'visible': self.visible
        })
        return base_dict
    
class VehicleData(BaseDocument):
    """Vehicle tracking data model - apenas dados de localização"""

    imei = StringField(required=True, max_length=50)
    longitude = StringField(max_length=20)  # Mantido como string conforme original
    latitude = StringField(max_length=20)   # Mantido como string conforme original
    altitude = StringField(max_length=20)   # Mantido como string conforme original
    timestamp = DateTimeField()  # Data do servidor
    deviceTimestamp = DateTimeField()  # Data do dispositivo convertida para datetime
    mensagem_raw = StringField()  # Mensagem original recebida
    
    meta = {
        'collection': 'vehicle_data',
        'indexes': [
          
            'timestamp',
            'deviceTimestamp',  # Índice composto para consultas eficientes
        ]
    }

    def to_dict(self):
        """Convert to dictionary for API responses"""
        base_dict = super(VehicleData, self).to_dict()
        base_dict.update({
            'imei': self.imei,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'altitude': self.altitude,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'deviceTimestamp': self.deviceTimestamp.isoformat() if self.deviceTimestamp else None,
            'mensagem_raw': self.mensagem_raw,
        })
        return base_dict

class Customer(BaseDocument):
    # Dados básicos
    name = StringField(required=True)
    email = StringField(required=True, unique=True)
    document = StringField(required=True, unique=True)
    phone = StringField(required=True)
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy
    
    # Endereço
    street = StringField(required=True)
    number = StringField(required=True)
    complement = StringField()
    district = StringField(required=True)
    city = StringField(required=True)
    state = StringField(required=True)  # SP, RJ, etc.
    postal_code = StringField(required=True)
    
    # Dados do cartão (se cadastrado)
    card_token = StringField()  # Token do PagSeguro
    card_brand = StringField()
    card_last_digits = StringField()
    
    # Status
    status = StringField(choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)

    # Password
    role = StringField(required=True, choices=['customer'], default='customer')
    password_hash = StringField(required=True, max_length=256)
    password_changed = BooleanField(default=False)  # Indica se o cliente já trocou a senha inicial
    
    meta = {
        'collection': 'customers',
        'indexes': [
            {'fields': ['email'], 'unique': True},
            {'fields': ['document'], 'unique': True},
        ],
        'strict': False  # Allow extra fields in DB from old schema versions
    }

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
        """Convert to dictionary for API responses"""
        base_dict = super(Customer, self).to_dict()
        base_dict.update({
            'name': self.name,
            'email': self.email,
            'document': self.document,
            'phone': self.phone,
            'company_id': str(self.company_id.id) if self.company_id else None,
            'street': self.street,
            'number': self.number,
            'complement': self.complement,
            'district': self.district,
            'city': self.city,
            'state': self.state,
            'postal_code': self.postal_code,
            'card_brand': self.card_brand,
            'card_last_digits': self.card_last_digits,
            'status': self.status,
            'visible': self.visible,
            'role': self.role,
            'password_changed': self.password_changed
        })
        return base_dict

class SubscriptionPlan(BaseDocument):
    """Subscription Plan model for managing available plans"""
    company_id = ReferenceField('Company', required=True)
    
    # Plan details
    name = StringField(required=True, max_length=100)
    description = StringField(max_length=500)
    amount = FloatField(required=True)  # Monthly amount in BRL
    currency = StringField(default='BRL')
    billing_cycle = StringField(choices=['monthly', 'yearly'], default='monthly')
    
    # Mercado Pago integration
    mp_preapproval_plan_id = StringField(unique=True, sparse=True)  # Mercado Pago plan ID
    
    # Features and limits (optional, for display purposes)
    features = ListField(StringField(), default=list)  # List of features included
    max_vehicles = IntField()  # Maximum number of vehicles (optional)
    
    # Status
    is_active = BooleanField(default=True)  # If plan is available for new subscriptions
    visible = BooleanField(default=True)
    
    meta = {
        'collection': 'subscription_plans',
        'indexes': [
            {'fields': ['company_id']},
            {'fields': ['is_active']},
            {'fields': ['mp_preapproval_plan_id'], 'unique': True, 'sparse': True},
        ]
    }
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        base_dict = super(SubscriptionPlan, self).to_dict()
        base_dict.update({
            'company_id': str(self.company_id.id) if self.company_id else None,
            'name': self.name,
            'description': self.description,
            'amount': self.amount,
            'currency': self.currency,
            'billing_cycle': self.billing_cycle,
            'mp_preapproval_plan_id': self.mp_preapproval_plan_id,
            'features': self.features,
            'max_vehicles': self.max_vehicles,
            'is_active': self.is_active,
        })
        return base_dict

class Subscription(BaseDocument):
    """Subscription model for monthly recurring payments"""
    customer_id = ReferenceField('Customer', required=True)
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy
    
    # Mercado Pago data
    mp_subscription_id = StringField(unique=True, sparse=True)
    mp_payer_id = StringField()  # Mercado Pago customer/payer ID
    mp_preapproval_plan_id = StringField()  # Preapproval plan ID
    
    # Subscription details
    plan_name = StringField(required=True)  # Nome do plano
    amount = FloatField(required=True)  # Valor mensal em reais
    currency = StringField(default='BRL')
    billing_cycle = StringField(choices=['monthly', 'yearly'], default='monthly')
    
    # Status and dates
    status = StringField(
        choices=['active', 'canceled', 'past_due', 'unpaid', 'incomplete'],
        default='incomplete'
    )
    current_period_start = DateTimeField()
    current_period_end = DateTimeField()
    cancel_at_period_end = BooleanField(default=False)
    canceled_at = DateTimeField()
    
    # Card info (for display only)
    card_brand = StringField()
    card_last_digits = StringField()
    
    visible = BooleanField(default=True)
    
    meta = {
        'collection': 'subscriptions',
        'indexes': [
            {'fields': ['customer_id']},
            {'fields': ['mp_subscription_id'], 'unique': True, 'sparse': True},
            {'fields': ['company_id']},
            {'fields': ['status']},
        ]
    }
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        base_dict = super(Subscription, self).to_dict()
        base_dict.update({
            'customer_id': str(self.customer_id.id) if self.customer_id else None,
            'company_id': str(self.company_id.id) if self.company_id else None,
            'mp_subscription_id': self.mp_subscription_id,
            'plan_name': self.plan_name,
            'amount': self.amount,
            'currency': self.currency,
            'billing_cycle': self.billing_cycle,
            'status': self.status,
            'current_period_start': self.current_period_start.isoformat() if self.current_period_start else None,
            'current_period_end': self.current_period_end.isoformat() if self.current_period_end else None,
            'cancel_at_period_end': self.cancel_at_period_end,
            'canceled_at': self.canceled_at.isoformat() if self.canceled_at else None,
            'card_brand': self.card_brand,
            'card_last_digits': self.card_last_digits,
        })
        return base_dict

class Payment(BaseDocument):
    """Payment history model for tracking all transactions"""
    customer_id = ReferenceField('Customer', required=True)
    subscription_id = ReferenceField('Subscription')
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy
    
    # Mercado Pago data
    mp_payment_id = StringField(unique=True, sparse=True)
    mp_transaction_id = StringField()
    
    # Payment details
    amount = FloatField(required=True)
    currency = StringField(default='BRL')
    description = StringField()
    
    # Status
    status = StringField(
        choices=['pending', 'processing', 'succeeded', 'failed', 'canceled', 'refunded'],
        default='pending'
    )
    failure_message = StringField()  # Error message if payment failed
    
    # Dates
    payment_date = DateTimeField()
    refunded_at = DateTimeField()
    
    # Card info (for display only)
    card_brand = StringField()
    card_last_digits = StringField()
    
    # Metadata
    payment_method = StringField(default='credit_card')
    receipt_url = StringField()
    
    visible = BooleanField(default=True)
    
    meta = {
        'collection': 'payments',
        'indexes': [
            {'fields': ['customer_id']},
            {'fields': ['subscription_id']},
            {'fields': ['company_id']},
            {'fields': ['mp_payment_id'], 'unique': True, 'sparse': True},
            {'fields': ['status']},
            {'fields': ['-payment_date']},  # Descending order for recent first
        ]
    }
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        base_dict = super(Payment, self).to_dict()
        base_dict.update({
            'customer_id': str(self.customer_id.id) if self.customer_id else None,
            'subscription_id': str(self.subscription_id.id) if self.subscription_id else None,
            'company_id': str(self.company_id.id) if self.company_id else None,
            'mp_payment_id': self.mp_payment_id,
            'amount': self.amount,
            'currency': self.currency,
            'description': self.description,
            'status': self.status,
            'failure_message': self.failure_message,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'refunded_at': self.refunded_at.isoformat() if self.refunded_at else None,
            'card_brand': self.card_brand,
            'card_last_digits': self.card_last_digits,
            'payment_method': self.payment_method,
            'receipt_url': self.receipt_url,
        })
        return base_dict

