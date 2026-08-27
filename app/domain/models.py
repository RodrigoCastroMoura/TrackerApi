from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine import *
from enum import Enum
from config import Config

# Unidades de frequência de cobrança suportadas em toda a aplicação (plano e assinatura).
# O Mercado Pago só aceita 'days' e 'months' na API — 'weeks' e 'years' são convertidos
# para essas duas na fronteira com o MP via to_mercadopago_frequency().
FREQUENCY_TYPES = ('days', 'weeks', 'months', 'years')

_DAYS_PER_UNIT = {'days': 1, 'weeks': 7, 'months': 30, 'years': 365}

def period_days_for_frequency(frequency: int, frequency_type: str) -> int:
    """Duração aproximada (em dias) de um período de cobrança."""
    frequency = frequency or 1
    return frequency * _DAYS_PER_UNIT.get(frequency_type, 30)

def to_mercadopago_frequency(frequency: int, frequency_type: str) -> tuple:
    """Converte (frequency, frequency_type) do domínio — days/weeks/months/years —
    para o formato aceito pela API do Mercado Pago, que só suporta 'days' e 'months'."""
    frequency = frequency or 1
    if frequency_type == 'weeks':
        return frequency * 7, 'days'
    if frequency_type == 'years':
        return frequency * 12, 'months'
    return frequency, frequency_type

# O Pagar.me aceita as quatro unidades no singular (day/week/month/year) + interval_count,
# então o vocabulário do domínio mapeia 1:1 (sem conversão de semanas/anos como no MP).
_PAGARME_INTERVAL = {'days': 'day', 'weeks': 'week', 'months': 'month', 'years': 'year'}

def to_pagarme_interval(frequency: int, frequency_type: str) -> tuple:
    """Converte (frequency, frequency_type) do domínio para (interval_count, interval)
    no formato do Pagar.me."""
    return (frequency or 1), _PAGARME_INTERVAL.get(frequency_type, 'month')

class TipoVeiculo(Enum):
    """Vehicle type enum with numeric and string values"""
    CARRO = (1, 'carro')
    MOTO = (2, 'moto')
    CAMINHAO = (3, 'caminhao')
    VAN = (4, 'van')
    ONIBUS = (5, 'onibus')
    OUTRO = (6, 'outro')
    
    def __init__(self, numero, descricao):
        self.numero = numero
        self.descricao = descricao

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

    def _ref_id(self, field_name):
        """Get the id of a reference field without dereferencing it.

        Avoids DoesNotExist errors when the referenced document (e.g. a
        deleted user) no longer exists — we only need its id, not the
        full document.
        """
        value = self._data.get(field_name)
        return str(value.id) if value else None

    def to_dict(self):
        """Base method for consistent dictionary representation"""
        return {
            'id': str(self.id) if self.id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': self._ref_id('created_by'),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'updated_by': self._ref_id('updated_by')
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
    must_change_password = BooleanField(default=False)  # Força troca de senha no próximo login
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
    tipo = StringField(max_length=50, choices=[t.descricao for t in TipoVeiculo])  # Tipo do veículo
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
    tsusermanu = DateTimeField()  # Timestamp de atualização do usuário/sistema
    longitude = StringField(max_length=50)  # Última longitude conhecida
    latitude = StringField(max_length=50)  # Última latitude conhecida
    altitude = StringField(max_length=50)  # Última altitude conhecida
    status = StringField(choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)  # Campo para exclusão 
    numberSendMessageWhatsApp = StringField(max_length=20)  # Número para enviar a mensagem via WhatsApp
    curso = IntField()  # Curso do veículo (direção)
    velocidade = FloatField()  # Velocidade do veículo

    meta = {
        'collection': 'vehicles',
        'auto_create_index': False,
        'indexes': [
            {'fields': ['IMEI'], 'unique': True, 'name': 'idx_v_imei'},
            {'fields': ['dsplaca'], 'unique': True, 'name': 'idx_v_placa', 'sparse': True},
            {'fields': ['company_id', 'visible'], 'name': 'idx_v_company_visible'},
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
            'curso': self.curso,
            'velocidade': self.velocidade,
            'bateriavoltagem': self.bateriavoltagem,
            'bateriabaixa': self.bateriabaixa,
            'ultimoalertabateria': self.ultimoalertabateria.isoformat() if self.ultimoalertabateria else None,
             'tsusermanu': self.tsusermanu.isoformat() if hasattr(self, 'tsusermanu') and self.tsusermanu else None,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'altitude': self.altitude,
            'status': self.status,
            'visible': self.visible,
            'numberSendMessageWhatsApp': self.numberSendMessageWhatsApp
        })
        return base_dict
    
class VehicleLocation(EmbeddedDocument):
    longitude = StringField(max_length=20)
    latitude = StringField(max_length=20)
    altitude = StringField(max_length=20)
    speed = FloatField()
    course = FloatField()

class VehicleData(Document):
    """Vehicle tracking data model - apenas dados de localização"""

    imei = StringField(required=True, max_length=50)
    timestamp = DateTimeField()
    location = EmbeddedDocumentField(VehicleLocation)

    meta = {
        'collection': 'vehicle_data',
        'auto_create_index': False,
        'indexes': [
            {'fields': ['imei', '-timestamp'], 'name': 'idx_vd_imei_ts'},
        ]
    }

    def to_dict(self):
        
        return{
                'imei': self.imei,
                'timestamp': self.timestamp.isoformat() if self.timestamp else None,
                'location': {
                    'longitude': self.location.longitude if self.location else None,
                    'latitude': self.location.latitude if self.location else None,
                    'altitude': self.location.altitude if self.location else None,
                    'speed': self.location.speed if self.location else None,
                    'course': self.location.course if self.location else None,
                } if self.location else None,
        }
        
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
    
    # Status
    status = StringField(choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)

    # Password
    role = StringField(required=True, choices=['customer'], default='customer')
    password_hash = StringField(required=True, max_length=256)
    password_changed = BooleanField(default=False)  # Indica se o cliente já trocou a senha inicial
    must_change_password = BooleanField(default=False)  # Força troca de senha no próximo login

    # FCM Token para notificações push
    fcm_token = StringField(max_length=500)

    # Imagens de assinatura e rubrica (base64) usadas na assinatura de documentos
    signature = StringField()
    rubric = StringField()
    signatureDoc = StringField()
    rubricDoc = StringField()
    type_font = StringField()

    has_accepted_terms = BooleanField(default=False)
    require_payment_method = BooleanField(default=True)
    can_change_plan = BooleanField(default=False)  # Flag que indica se o usuario pode trocar de plano

    #flag configuração de notificação do push
    fl_notification_ignicao_enabled = BooleanField(default=True)
    fl_notification_command_enabled = BooleanField(default=True)

    meta = {
        'collection': 'customers',
        'indexes': [
            {'fields': ['email'], 'unique': True},
            {'fields': ['document'], 'unique': True},
            {'fields': ['phone'], 'unique': True},
        ],
        'strict': False  # Allow extra fields in DB from old schema versions
    }

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def check_password_chatbot(self, password): 
        return Config.PASSWORG_CHATBOT_SALT == password

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
            'status': self.status,
            'visible': self.visible,
            'role': self.role,
            'password_changed': self.password_changed,
            'fcm_token': self.fcm_token,
            'has_accepted_terms': self.has_accepted_terms,
            'require_payment_method': self.require_payment_method,
            'can_change_plan': self.can_change_plan,
            'fl_notification_ignicao_enabled': self.fl_notification_ignicao_enabled,
            'fl_notification_command_enabled': self.fl_notification_command_enabled,
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
    frequency = IntField(default=1)
    frequency_type = StringField(default='months', choices=list(FREQUENCY_TYPES))

    # ID do plano no provedor de pagamento (Mercado Pago, Pagar.me, ...)
    provider_plan_id = StringField(unique=True, sparse=True)
    
    features = ListField(StringField())  # Lista de funcionalidades do plano
    max_vehicles = IntField()  # Maximum number of vehicles (optional)
    free_days = IntField(default=0)  # Number of free trial days before first billing

    # Status
    is_active = BooleanField(default=True)  # If plan is available for new subscriptions
    visible = BooleanField(default=True)
    
    meta = {
        'collection': 'subscription_plans',
        'indexes': [
            {'fields': ['company_id']},
            {'fields': ['is_active']},
            {'fields': ['provider_plan_id'], 'unique': True, 'sparse': True},
        ],
        'strict': False  # Allow extra fields from old schema versions
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
            'frequency': self.frequency,
            'frequency_type': self.frequency_type,
            'provider_plan_id': self.provider_plan_id,
            'features': self.features or [],
            'max_vehicles': self.max_vehicles,
            'free_days': self.free_days or 0,
            'is_active': self.is_active,
            'visible': self.visible 
        })
        return base_dict

class SubscriptionPayment(EmbeddedDocument):
    """Registro de um pagamento mensal da assinatura"""
    provider_payment_id = StringField(required=True)
    amount = FloatField(required=True)
    currency = StringField(default='BRL')
    status = StringField(choices=['approved', 'rejected', 'pending'], default='pending')
    paid_at = DateTimeField()
    period_start = DateTimeField()
    period_end = DateTimeField()

    def to_dict(self):
        return {
            'provider_payment_id': self.provider_payment_id,
            'amount': self.amount,
            'currency': self.currency,
            'status': self.status,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end': self.period_end.isoformat() if self.period_end else None,
        }

class Bairro(Document):
    """Bairro para enriquecimento da busca de CEP"""
    bai_nu_sequencial = IntField()
    loc_nu_sequencial = IntField()
    ufe_sg = StringField(max_length=2)
    bai_no = StringField()
    bai_no_abrev = StringField()
    Localidade = StringField()

    meta = {
        'collection': 'bairro',
        'indexes': [{'fields': ['bai_nu_sequencial']}],
        'strict': False,
    }

class Localidade(Document):
    """Município para enriquecimento da busca de CEP"""
    loc_nu_sequencial = IntField()
    loc_nosub = StringField()
    loc_no = StringField()
    cep = StringField(max_length=8)
    ufe_sg = StringField(max_length=2)
    loc_in_situacao = IntField()
    loc_in_tipo_localidade = StringField(max_length=1)
    loc_nu_sequencial_sub = IntField()
    temp = StringField()

    meta = {
        'collection': 'municipio',
        'indexes': [{'fields': ['loc_nu_sequencial']}, {'fields': ['cep']}],
        'strict': False,
    }

class Logradouro(Document):
    """Modelo de logradouro para busca de CEP (Correios)"""
    log_nu_sequencial = IntField()
    ufe_sg = StringField(max_length=2)
    loc_nu_sequencial = IntField()
    log_no = StringField()
    log_nome = StringField()
    bai_nu_sequencial_ini = IntField()
    bai_nu_sequencial_fim = IntField()
    cep = StringField(max_length=8)
    log_complemento = StringField()
    log_status_tipo_log = StringField(max_length=1)
    log_no_sem_acento = StringField()
    ind_uop = StringField(max_length=1)
    ind_gru = StringField(max_length=1)
    temp = StringField()
    Bairro = StringField()

    meta = {
        'collection': 'Logradouro',
        'indexes': [{'fields': ['cep']}],
        'strict': False,
    }

    def to_dict(self):
        return {
            'cep': self.cep,
            'logradouro': self.log_nome,
            'complemento': self.log_complemento or '',
            'bairro': self.Bairro or '',
            'uf': self.ufe_sg,
            'log_nu_sequencial': self.log_nu_sequencial,
            'loc_nu_sequencial': self.loc_nu_sequencial,
        }

class Subscription(BaseDocument):
    """Subscription model for monthly recurring payments"""
    customer_id = ReferenceField('Customer', required=True)
    company_id = ReferenceField('Company', required=True)  # Multi-tenancy

    # Dados do provedor de pagamento (Mercado Pago, Pagar.me, ...) — nomes
    # neutros para padronizar entre meios de pagamento.
    provider_subscription_id = StringField(unique=True, sparse=True)
    provider_customer_id = StringField()
    provider_plan_id = StringField()
    provider_status = StringField(
        choices=['pending', 'processing', 'succeeded', 'failed', 'canceled', 'refunded'],
        default='pending'
    )
    payment_url = StringField()
    payment_date = DateTimeField()
    failure_message = StringField()
    refunded_at = DateTimeField()

    # Subscription details
    plan_name = StringField(required=True)  # Nome do plano
    amount = FloatField(required=True)  # Valor mensal em reais
    currency = StringField(default='BRL')
    billing_cycle = StringField(default='months', choices=['days', 'months'])  # frequency_type já convertido pra API do MP
    frequency = IntField(default=1)  # frequency já convertido pra API do MP (ex: 7 + billing_cycle='days' = semanal)

    # Status and dates
    status = StringField(
        choices=['active', 'canceled', 'past_due', 'unpaid', 'incomplete', 'pending', 'paused', 'pendingPayment'],
        default='incomplete'
    )
    current_period_start = DateTimeField()
    current_period_end = DateTimeField()
    grace_period_end = DateTimeField()  # Prazo de pagamento (15 dias após vencimento)
    cancel_at_period_end = BooleanField(default=False)
    canceled_at = DateTimeField()

    visible = BooleanField(default=True)

    # Payment deadline
    payment_deadline = DateTimeField()  # Data limite para pagamento (vencimento + 15 dias)
    access_blocked = BooleanField(default=False)  # Bloqueia acesso após prazo

    # Historical monthly payments
    payment_history = EmbeddedDocumentListField(SubscriptionPayment, default=list)

    meta = {
        'collection': 'subscriptions',
        'indexes': [
            {'fields': ['customer_id']},
            {'fields': ['provider_subscription_id'], 'unique': True, 'sparse': True},
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
            'provider_subscription_id': self.provider_subscription_id,
            'provider_plan_id': self.provider_plan_id,
            'provider_status': self.provider_status,
            'payment_url': self.payment_url,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'failure_message': self.failure_message,
            'refunded_at': self.refunded_at.isoformat() if self.refunded_at else None,
            'plan_name': self.plan_name,
            'amount': self.amount,
            'currency': self.currency,
            'billing_cycle': self.billing_cycle,
            'frequency': self.frequency,
            'status': self.status,
            'current_period_start': self.current_period_start.isoformat() if self.current_period_start else None,
            'current_period_end': self.current_period_end.isoformat() if self.current_period_end else None,
            'grace_period_end': self.grace_period_end.isoformat() if self.grace_period_end else None,
            'payment_deadline': self.payment_deadline.isoformat() if self.payment_deadline else None,
            'access_blocked': self.access_blocked,
            'cancel_at_period_end': self.cancel_at_period_end,
            'canceled_at': self.canceled_at.isoformat() if self.canceled_at else None,
            'payment_history': [p.to_dict() for p in (self.payment_history or [])],
        })
        return base_dict

class Street(Document):
    """Via/logradouro geografico (OpenStreetMap) - usado para reverse geocoding"""
    name = StringField(required=True)
    geometry = LineStringField(required=True)  # coordenadas simplificadas da via
 
    meta = {
        'collection': 'streets',
        'indexes': ['geometry'],  # mongoengine cria indice 2dsphere automaticamente
        'strict': False,
    }
 
    def to_dict(self):
        return {
            'id': str(self.id) if self.id else None,
            'name': self.name,
        }
 
class Address(Document):
    """Ponto de endereco exato (numero + rua) - usado para reverse geocoding"""
    location = PointField(required=True)
    street = StringField()
    housenumber = StringField()
 
    meta = {
        'collection': 'addresses',
        'indexes': ['location'],
        'strict': False,
    }
 
    def to_dict(self):
        return {
            'id': str(self.id) if self.id else None,
            'street': self.street,
            'housenumber': self.housenumber,
        }
 
class Boundary(Document):
    """Limite administrativo (municipio/bairro/estado) - fallback do reverse geocoding
    quando nao ha rua/endereco proximo o suficiente (comum em area rural)."""
    name = StringField(required=True)
    admin_level = IntField(required=True)  # 4=estado, 8=municipio, 9/10=bairro/distrito
    geometry = MultiPolygonField(required=True)
 
    meta = {
        'collection': 'boundaries',
        'indexes': ['geometry', 'admin_level'],
        'strict': False,
    }
 
    def to_dict(self):
        return {
            'id': str(self.id) if self.id else None,
            'name': self.name,
            'admin_level': self.admin_level,
        }

class Neighbourhood(Document):
    """Bairro como ponto (OSM place=suburb/neighbourhood/quarter) - fonte principal
    de bairro no Brasil, muito mais completa que Boundary (poligono administrativo,
    que aqui quase nao tem cobertura de bairro - a maioria e' mapeada como NODE)."""
    name = StringField(required=True)
    place = StringField()  # 'suburb' | 'neighbourhood' | 'quarter'
    location = PointField(required=True)

    meta = {
        'collection': 'neighbourhoods',
        'indexes': ['location'],
        'strict': False,
    }

    def to_dict(self):
        return {
            'id': str(self.id) if self.id else None,
            'name': self.name,
            'place': self.place,
        }

# --------------------------------------------------------------------------
# Helper de reverse geocoding, usando as 3 entidades acima
# --------------------------------------------------------------------------
 
ESTADO_PARA_UF = {
    'Acre': 'AC', 'Alagoas': 'AL', 'Amapá': 'AP', 'Amazonas': 'AM',
    'Bahia': 'BA', 'Ceará': 'CE', 'Distrito Federal': 'DF',
    'Espírito Santo': 'ES', 'Goiás': 'GO', 'Maranhão': 'MA',
    'Mato Grosso': 'MT', 'Mato Grosso do Sul': 'MS', 'Minas Gerais': 'MG',
    'Pará': 'PA', 'Paraíba': 'PB', 'Paraná': 'PR', 'Pernambuco': 'PE',
    'Piauí': 'PI', 'Rio de Janeiro': 'RJ', 'Rio Grande do Norte': 'RN',
    'Rio Grande do Sul': 'RS', 'Rondônia': 'RO', 'Roraima': 'RR',
    'Santa Catarina': 'SC', 'São Paulo': 'SP', 'Sergipe': 'SE',
    'Tocantins': 'TO',
}

def _bairro_mais_proximo(lon, lat, cidade=None, max_distance_m=3000):
    """
    Bairro mais proximo via Neighbourhood (nos de ponto place=suburb/neighbourhood/
    quarter do OSM) - e' assim que o Brasil mapeia bairro na pratica, Boundary
    (poligono administrativo) quase nao tem cobertura disso aqui.

    max_distance_m limita a busca a 3km por padrao: alem disso "bairro mais
    proximo" deixa de ser uma resposta que faz sentido.

    Retorna (nome, distancia_km) ou (None, None) se nao houver nenhum a menos
    de max_distance_m.
    """
    collection = Neighbourhood._get_collection()
    pipeline = [
        {
            '$geoNear': {
                'near': {'type': 'Point', 'coordinates': [lon, lat]},
                'distanceField': 'dist_m',
                'spherical': True,
                'maxDistance': max_distance_m,
            }
        },
        {'$limit': 1},
    ]
    results = list(collection.aggregate(pipeline))
    if results and results[0]['name'] != cidade:
        return results[0]['name'], results[0]['dist_m'] / 1000.0
    return None, None

def _montar_endereco_completo(dados):
    """Monta 'Rua, Numero, Bairro, Cidade - UF' a partir do dict retornado por reverse_geocode()."""
    if not dados or not any((dados.get('rua'), dados.get('bairro'), dados.get('cidade'), dados.get('estado'))):
        return None

    parts = []

    if dados.get('rua'):
        rua = dados['rua']
        if dados.get('numero'):
            rua += f", {dados['numero']}"
        parts.append(rua)

    if dados.get('bairro'):
        parts.append(dados['bairro'])

    cidade_estado = [v for v in (dados.get('cidade'), dados.get('estado')) if v]
    if cidade_estado:
        parts.append(' - '.join(cidade_estado))

    return ', '.join(parts) if parts else None

def reverse_geocode(lat, lon, max_distance_m=200):
    """
    Reverse geocoding completo, combinando as entidades geograficas importadas do OSM
    (streets/addresses/neighbourhoods/boundaries).
 
    Cascata:
      1. Street por proximidade (max_distance_m) -> rua (fonte primaria, mais densa
         e confiavel que Address). Se achou Street, tenta complementar com Address
         mais proximo SO' quando o street do Address bate com o nome da Street
         encontrada — evita misturar numero de uma rua com nome de outra, que
         acontecia quando Address (mais esparso) era usado como fonte primaria.
      2. Se nao achou nenhuma Street por perto (raro), usa Address como fallback
         pra rua tambem.
      3. Boundary admin_level=4/8 por point-in-polygon -> estado (UF) e municipio
         (poligono administrativo tem boa cobertura nesses dois niveis).
      4. Neighbourhood (nos de ponto place=suburb/neighbourhood/quarter) por
         proximidade -> bairro. Nao usa Boundary admin_level=9/10 pra isso porque
         no Brasil bairro quase sempre e' mapeado como NODE no OSM, nao como area
         administrativa - Boundary tinha cobertura de bairro praticamente nula.
         bairro_tipo='exato' quando a menos de 500m, 'aproximado' caso contrario
         (ate o limite de 3km definido em _bairro_mais_proximo).
 
    Retorna um dict com os campos encontrados e 'endereco_completo' ja formatado.
    """
    result = {
        'rua': None,
        'numero': None,
        'bairro': None,
        'bairro_tipo': None,  # 'exato' | 'aproximado' | None
        'bairro_distancia_km': None,
        'cidade': None,
        'estado': None,
        'endereco_completo': None,
    }
 
    # --- Rua e numero ---
    street = Street.objects(
        geometry__near=[lon, lat], geometry__max_distance=max_distance_m
    ).first()
 
    if street:
        result['rua'] = street.name
 
        # Complementa o numero com o Address mais proximo, mas so' aceita se
        # for da mesma rua (comparacao case-insensitive por substring) - senao
        # fica None mesmo, e' melhor que numero errado de rua diferente.
        address = Address.objects(
            location__near=[lon, lat], location__max_distance=max_distance_m,
        ).first()
        if address and address.street and street.name:
            a = address.street.strip().lower()
            s = street.name.strip().lower()
            if a in s or s in a:
                result['numero'] = address.housenumber
    else:
        # Fallback raro: nenhuma Street por perto, tenta Address mesmo assim
        address = Address.objects(
            location__near=[lon, lat], location__max_distance=max_distance_m
        ).first()
        if address:
            result['rua'] = address.street
            result['numero'] = address.housenumber
 
    # --- Cidade e estado (poligono administrativo, cobertura boa) ---
    # admin_level__in usa string+int porque a importacao original gravou o campo como
    # string (ex: '8'), embora o model declare IntField — sem isso a query nunca casa.
    boundaries = list(Boundary.objects(
        __raw__={
            'geometry': {
                '$geoIntersects': {
                    '$geometry': {'type': 'Point', 'coordinates': [lon, lat]}
                }
            },
            'admin_level': {'$in': [4, 8, '4', '8']},
        }
    ))
 
    def by_level(*levels):
        wanted = set(levels) | {str(l) for l in levels}
        return [b for b in boundaries if b.admin_level in wanted]
 
    estado = by_level(4)
    if estado:
        result['estado'] = ESTADO_PARA_UF.get(estado[0].name, estado[0].name)
 
    cidade = by_level(8)
    if cidade:
        result['cidade'] = cidade[0].name
 
    # --- Bairro (nos de ponto, nao poligono - ver docstring) ---
    nome, distancia_km = _bairro_mais_proximo(lon, lat, cidade=result['cidade'])
    if nome:
        result['bairro'] = nome
        result['bairro_tipo'] = 'exato' if distancia_km < 0.5 else 'aproximado'
        result['bairro_distancia_km'] = round(distancia_km, 2)
 
    result['endereco_completo'] = _montar_endereco_completo(result)
    return result
    
class Document(Document):
    url = StringField(required=True)
    customer_id = ReferenceField('Customer', required=True)
    status = StringField(required=True, choices=['active', 'inactive'], default='active')
    visible = BooleanField(default=True)
    view_count = IntField(default=0)
    download_count = IntField(default=0)
    signature = BooleanField(default=False)  # Indicates if document has signature
    signature_date = DateTimeField()  # Data/hora em que o documento foi assinado
    meta = {
        'collection': 'documents',
        'indexes': [
            {'fields': ['customer_id', 'status']},
            {'fields': ['customer_id', 'visible']}
        ],
        'strict': False  # Ignora campos antigos (created_at/created_by/updated_at/updated_by) já salvos no banco
    }

    def to_dict(self):
        return {
            'id': str(self.id) if self.id else None,
            'url': self.url,
            'customer_id': str(self.customer_id.id) if self.customer_id else None,
            'customer_name': self.customer_id.name if self.customer_id else None,
            'status': self.status,
            'visible': self.visible,
            'view_count': self.view_count,
            'download_count': self.download_count,
            'signature': self.signature,
            'signature_date': self.signature_date.isoformat() if self.signature_date else None
        }

