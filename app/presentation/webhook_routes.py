from flask import request
from flask_restx import Namespace, Resource
from app.domain.models import Subscription, SubscriptionPayment, Customer, period_days_for_frequency, SubscriptionPlan, to_provider_frequency
from app.infrastructure.stripe_service import StripeService
from app.infrastructure.apple_service import AppleService
from datetime import datetime, timedelta, timezone
import logging
from bson import ObjectId
import hmac
from config import Config

logger = logging.getLogger(__name__)

api = Namespace('webhooks', description='Webhooks de integração - Stripe, Apple e Pagar.me')


def _epoch_to_dt(value):
    """Converte epoch seconds (int) da Stripe para datetime UTC, ou None."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _stripe_sub_id_from_obj(obj):
    """Extrai o id da assinatura (sub_...) de um objeto de evento da Stripe,
    cobrindo tanto a API antiga (invoice.subscription / session.subscription no
    topo) quanto a nova 2025-10 (invoice.parent.subscription_details.subscription
    e lines[].parent.subscription_item_details.subscription)."""
    obj = obj or {}
    if str(obj.get('object')) == 'subscription':
        return obj.get('id')

    sub_id = obj.get('subscription')
    if sub_id:
        return sub_id

    parent = obj.get('parent') or {}
    sub_id = (parent.get('subscription_details') or {}).get('subscription')
    if sub_id:
        return sub_id

    for line in ((obj.get('lines') or {}).get('data') or []):
        l_sub = ((line.get('parent') or {}).get('subscription_item_details') or {}).get('subscription')
        if l_sub:
            return l_sub
    return None


def _stripe_local_id_from_obj(obj):
    """Procura o metadata.local_subscription_id em todos os lugares onde a Stripe
    pode colocá-lo, dependendo do tipo de objeto e da versão da API."""
    obj = obj or {}
    parent = obj.get('parent') or {}
    buckets = [
        obj.get('metadata') or {},
        (parent.get('subscription_details') or {}).get('metadata') or {},
    ]
    for line in ((obj.get('lines') or {}).get('data') or []):
        buckets.append(line.get('metadata') or {})
    for md in buckets:
        if md.get('local_subscription_id'):
            return md['local_subscription_id']
    return obj.get('client_reference_id')


def _find_stripe_subscription(obj):
    """Localiza a Subscription local de um objeto de evento da Stripe: primeiro
    pelo provider_subscription_id (sub_... já preenchido), senão pelo
    metadata.local_subscription_id / client_reference_id enviado no checkout."""
    sub_id = _stripe_sub_id_from_obj(obj)
    if sub_id:
        found = Subscription.objects(provider_subscription_id=str(sub_id), visible=True).first()
        if found:
            return found

    local_id = _stripe_local_id_from_obj(obj)
    if local_id:
        try:
            return Subscription.objects(id=local_id, visible=True).first()
        except Exception:
            return None
    return None


@api.route('/stripe')
class StripeWebhook(Resource):

    @api.doc('stripe_webhook', description='Webhook da Stripe para assinaturas e faturas recorrentes')
    def post(self):
        """Processar notificações da Stripe (checkout.session.completed,
        customer.subscription.*, invoice.*, charge.refunded)"""
        try:
            event = StripeService.construct_webhook_event(
                request.data, request.headers.get('Stripe-Signature', '')
            )
            if not event:
                return {'message': 'Invalid signature'}, 401

            event_type = event.get('type')
            obj = (event.get('data') or {}).get('object') or {}

            logger.info(f"Received Stripe webhook - Event: {event_type}, ID: {obj.get('id')}")

            handled = (
                event_type == 'checkout.session.completed'
                or event_type.startswith('customer.subscription.')
                or event_type.startswith('invoice.')
                or event_type == 'charge.refunded'
            )
            if not handled:
                logger.info(f"Stripe webhook event ignored: {event_type}")
                return {'message': 'Webhook recebido'}, 200

            subscription = _find_stripe_subscription(obj)
            if not subscription:
                logger.warning(
                    f"Stripe subscription ainda não persistida localmente para evento {event_type}; "
                    f"solicitando reenvio"
                )
                return {'message': 'Assinatura ainda não disponível localmente'}, 503

            customer = subscription.customer_id
            now = datetime.now(timezone.utc)
            period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)

            if event_type == 'checkout.session.completed':
                if obj.get('mode') != 'subscription':
                    return {'message': 'Webhook recebido'}, 200

                stripe_sub_id = _stripe_sub_id_from_obj(obj)
                if stripe_sub_id:
                    subscription.provider_subscription_id = str(stripe_sub_id)
                subscription.provider_customer_id = obj.get('customer') or subscription.provider_customer_id

                info = StripeService.get_subscription(str(stripe_sub_id)) if stripe_sub_id else None
                period_end = _epoch_to_dt(info.get('current_period_end')) if info else None
                next_payment_date = period_end or (now + timedelta(days=period_days))

                subscription.status = 'active'
                subscription.provider_status = 'succeeded'
                if not subscription.current_period_start:
                    subscription.current_period_start = now
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = next_payment_date + timedelta(days=Config.STRIPE_DAYS_TO_EXPIRE)
                subscription.access_blocked = False
                subscription.failure_message = None
                subscription.canceled_at = None
                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Stripe checkout completed for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}"
                )

            elif event_type in ('customer.subscription.created', 'customer.subscription.updated'):
                sub_status = obj.get('status')
                if not subscription.provider_subscription_id:
                    subscription.provider_subscription_id = str(obj.get('id'))
                subscription.provider_customer_id = obj.get('customer') or subscription.provider_customer_id

                if sub_status in ('active', 'trialing'):
                    # na API 2025-10 o current_period_end saiu da subscription e
                    # passou a ficar em cada item (items.data[].current_period_end).
                    sub_items = ((obj.get('items') or {}).get('data') or [])
                    item_period_end = sub_items[0].get('current_period_end') if sub_items else None
                    period_end = _epoch_to_dt(obj.get('current_period_end') or item_period_end)
                    next_payment_date = period_end or (now + timedelta(days=period_days))
                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    if not subscription.current_period_start:
                        subscription.current_period_start = now
                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = next_payment_date + timedelta(
                        days=Config.STRIPE_DAYS_TO_EXPIRE
                    )
                    subscription.access_blocked = False
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()
                elif sub_status in ('past_due', 'unpaid'):
                    subscription.status = 'pendingPayment'
                    subscription.provider_status = 'failed'
                    subscription.save()
                elif sub_status in ('canceled', 'incomplete_expired'):
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()
                else:
                    logger.info(
                        f"Stripe subscription event {event_type} with status {sub_status} - no local change"
                    )

            elif event_type == 'customer.subscription.deleted':
                subscription.status = 'canceled'
                subscription.provider_status = 'canceled'
                subscription.canceled_at = now
                if customer.require_payment_method is False:
                    customer.can_change_plan = True
                subscription.save()
                customer.save()

            elif event_type == 'invoice.paid':
                invoice_id = str(obj.get('id'))
                already_registered = any(
                    p.provider_payment_id == invoice_id
                    for p in subscription.payment_history
                )
                line_items = ((obj.get('lines') or {}).get('data') or [])
                line_period_end = (line_items[0].get('period') or {}).get('end') if line_items else None
                # o period_end do topo da invoice, em faturas de criação de assinatura,
                # vem igual ao period_start — prioriza o fim do período da linha.
                period_end = _epoch_to_dt(line_period_end or obj.get('period_end'))
                next_payment_date = period_end or (now + timedelta(days=period_days))
                grace_period_end = next_payment_date + timedelta(days=Config.STRIPE_DAYS_TO_EXPIRE)

                subscription.status = 'active'
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = grace_period_end
                subscription.provider_status = 'succeeded'
                subscription.failure_message = None
                subscription.canceled_at = None
                if not subscription.provider_subscription_id:
                    recovered = _stripe_sub_id_from_obj(obj)
                    if recovered:
                        subscription.provider_subscription_id = str(recovered)

                if not already_registered:
                    amount = obj.get('amount_paid')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=invoice_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=(obj.get('currency') or 'BRL').upper(),
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=next_payment_date,
                    ))

                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Stripe invoice paid for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}, grace ends: {grace_period_end.date()}"
                )

            elif event_type in ('invoice.payment_failed', 'invoice.marked_uncollectible'):
                invoice_id = str(obj.get('id'))
                already_registered = any(
                    p.provider_payment_id == invoice_id
                    for p in subscription.payment_history
                )
                subscription.provider_status = 'failed'
                subscription.status = 'pendingPayment'
                subscription.failure_message = f'Cobrança recorrente rejeitada (evento: {event_type})'
                if not already_registered:
                    amount = obj.get('amount_due')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=invoice_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=(obj.get('currency') or 'BRL').upper(),
                        status='rejected',
                        paid_at=now,
                    ))
                subscription.save()
                logger.warning(
                    f"Stripe invoice failed for subscription {subscription.id} (event: {event_type})"
                )

            elif event_type == 'charge.refunded':
                subscription.provider_status = 'refunded'
                subscription.refunded_at = now
                subscription.save()
                logger.info(f"Stripe charge refunded for subscription {subscription.id}")

            else:
                logger.info(f"Stripe webhook event not handled: {event_type}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing Stripe webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500

def _resolve_plan(plan_id_input):
    """Aceita ObjectId de 24 chars ou o provider_plan_id (price_... da Stripe)."""
    plan = None
    if plan_id_input and len(plan_id_input) == 24:
        try:
            plan = SubscriptionPlan.objects(
                id=plan_id_input, is_active=True, visible=True
            ).first()
        except Exception:
            plan = None
    if not plan:
        plan = SubscriptionPlan.objects(
            provider_plan_id=plan_id_input,
            is_active=True,
            visible=True,
        ).first()
    return plan


def _ms_to_dt(value):
    """Converte epoch em milissegundos (Apple) para datetime UTC, ou None."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _as_utc(value):
    """O MongoEngine costuma devolver datetime naive (UTC); normaliza para comparar."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _apple_amount(tx, fallback):
    """tx.price vem em milésimos da moeda (29900 = R$ 29,90). Em trial vem 0."""
    price = getattr(tx, 'price', None)
    if isinstance(price, (int, float)):
        return price / 1000.0
    return fallback


def _customer_from_account_token(token):
    """O app manda como appAccountToken um UUID montado a partir do ObjectId do cliente
    (8 zeros + 24 hex). Aqui desfazemos a montagem e buscamos o cliente."""
    try:
        raw = str(token or '').replace('-', '').lower()
        if len(raw) != 32 or not raw.startswith('0' * 8):
            return None
        return Customer.objects(id=ObjectId(raw[8:])).first()
    except Exception:
        return None


def _apple_grace_days():
    """Tolerância após o vencimento. APPLE_DAYS_TO_EXPIRE; se não existir, usa a da Stripe."""
    days = getattr(Config, 'APPLE_DAYS_TO_EXPIRE', None)
    return int(days) if days is not None else Config.STRIPE_DAYS_TO_EXPIRE


def _apple_register_payment(subscription, tx, now, period_end, status='approved', payment_id=None):
    """Registra o pagamento no histórico sem duplicar (a Apple pode reenviar a notificação)."""
    payment_id = payment_id or str(tx.transactionId)
    if any(p.provider_payment_id == payment_id for p in subscription.payment_history):
        return
    kwargs = dict(
        provider_payment_id=payment_id,
        amount=_apple_amount(tx, subscription.amount),
        currency=(getattr(tx, 'currency', None) or 'BRL').upper(),
        status=status,
        paid_at=now,
    )
    if status == 'approved':
        kwargs['period_start'] = _ms_to_dt(tx.purchaseDate) or now
        kwargs['period_end'] = period_end
    subscription.payment_history.append(SubscriptionPayment(**kwargs))


@api.route('/apple')
class AppleWebhook(Resource):

    @api.doc('apple_webhook', description='App Store Server Notifications V2 (assinaturas via Apple IAP)')
    def post(self):
        """Processar notificações da Apple (SUBSCRIBED, DID_RENEW, DID_FAIL_TO_RENEW,
        DID_CHANGE_RENEWAL_STATUS, EXPIRED, GRACE_PERIOD_EXPIRED, REFUND, REVOKE)"""
        try:
            body = request.get_json(silent=True) or {}
            signed_payload = body.get('signedPayload')
            if not signed_payload:
                return {'message': 'Invalid webhook data'}, 400

            # A segurança vem da assinatura (JWS) validada com os certificados raiz da Apple.
            notification, env = AppleService.decode_notification(signed_payload)
            if not notification:
                logger.error("Invalid Apple webhook signature - rejecting request")
                return {'message': 'Invalid signature'}, 401

            kind = notification.rawNotificationType
            subtype = notification.rawSubtype
            data = notification.data

            logger.info(f"Received Apple webhook - Type: {kind}, Subtype: {subtype}, UUID: {notification.notificationUUID}")

            if kind == 'TEST' or not data or not data.signedTransactionInfo:
                return {'message': 'Webhook recebido'}, 200

            handled = (
                'SUBSCRIBED', 'DID_RENEW', 'DID_CHANGE_RENEWAL_STATUS',
                'DID_FAIL_TO_RENEW', 'EXPIRED', 'GRACE_PERIOD_EXPIRED', 'REFUND', 'REVOKE',
            )
            if kind not in handled:
                logger.info(f"Apple webhook event ignored: {kind}")
                return {'message': 'Webhook recebido'}, 200

            # Campos resgatados da transação assinada pela Apple
            tx = AppleService.decode_transaction(data.signedTransactionInfo, env)
            original_id = str(tx.originalTransactionId)   # identifica a assinatura
            transaction_id = str(tx.transactionId)        # esta cobrança / renovação
            product_id = tx.productId                     # id do plano
            account_customer = _customer_from_account_token(getattr(tx, 'appAccountToken', None))
            user_id = str(account_customer.id) if account_customer else None
            logger.info(
                f"Apple tx - transaction_id={transaction_id} original={original_id} "
                f"product_id={product_id} user_id={user_id}"
            )

            now = datetime.now(timezone.utc)
            expires = _ms_to_dt(tx.expiresDate)
            grace_days = _apple_grace_days()
            next_payment_date = expires or (now + timedelta(days=30))

            # Todo evento é amarrado à assinatura pelo originalTransactionId.
            subscription = Subscription.objects(provider_subscription_id=original_id).first()

            if kind == 'SUBSCRIBED':
                if subscription is None:
                    # Primeira compra (INITIAL_BUY), ou RESUBSCRIBE de uma assinatura que nunca chegou a ser gravada.
                    if not account_customer:
                        # Sem appAccountToken não há como saber de quem é a compra; reenviar não resolve.
                        logger.error(
                            f"Apple SUBSCRIBED {original_id}: appAccountToken ausente ou cliente não encontrado"
                        )
                        return {'message': 'Cliente não identificado'}, 200
                    customer = account_customer

                    plan = _resolve_plan(product_id)
                    if not plan:
                        logger.error(f"Plano não encontrado Apple: {product_id}")
                        return {'message': 'Plano de assinatura não encontrado ou inativo'}, 404

                    already_active = Subscription.objects(customer_id=customer, status='active').first()
                    if already_active:
                        logger.warning(
                            f"Apple {original_id}: cliente {customer.id} já tem a assinatura "
                            f"{already_active.id} ativa; criando a da Apple mesmo assim"
                        )

                    frequency, billing_cycle = to_provider_frequency(plan.frequency, plan.frequency_type)

                    try:
                        subscription = Subscription(
                            id=ObjectId(),
                            customer_id=customer,
                            company_id=customer.company_id,
                            provider_plan_id=None,
                            provider_subscription_id=original_id,
                            plan_name=plan.name,
                            amount=plan.amount,
                            status='active',
                            provider_status='succeeded',
                            billing_cycle=billing_cycle,
                            frequency=frequency,
                            currency='BRL',
                            current_period_start=now,
                            current_period_end=next_payment_date,
                            grace_period_end=next_payment_date + timedelta(days=grace_days),
                            access_blocked=False,
                            failure_message=None,
                            canceled_at=None,
                            require_payment_method=False,
                            can_change_plan=False,
                            payment_url=None,
                            created_by=None,
                            updated_by=None,
                        )
                        _apple_register_payment(subscription, tx, now, next_payment_date)
                        customer.require_payment_method = False
                        customer.can_change_plan = False
                        subscription.save()
                        customer.save()
                    except Exception as db_error:
                        logger.error(f"DB save failed for Apple subscription: {db_error}")
                        return {'message': 'Erro ao salvar assinatura. Tente novamente.'}, 500
                    logger.info(f"Apple subscription {subscription.id} criada para o cliente {customer.id}")

                else:
                    # RESUBSCRIBE (cliente voltou depois de expirar/cancelar) ou reentrega do INITIAL_BUY.
                    customer = subscription.customer_id
                    if not customer:
                        logger.error(f"Customer não encontrado Apple - SUBSCRIBED: {original_id}")
                        return {'message': 'Usuário inativo'}, 401

                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    if not subscription.current_period_start:
                        subscription.current_period_start = now
                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = next_payment_date + timedelta(days=grace_days)
                    subscription.access_blocked = False
                    subscription.failure_message = None
                    subscription.canceled_at = None
                    _apple_register_payment(subscription, tx, now, next_payment_date)

                    # TODO: se vier outro productId (upgrade/downgrade), atualize plan_name, amount e frequência
                    # a partir de _resolve_plan(product_id).

                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()

            else:
                # Os demais eventos só atualizam uma assinatura que já existe.
                if subscription is None:
                    logger.error(f"subscription não encontrada Apple - {kind}: {original_id}")
                    return {'message': 'Webhook recebido'}, 400

                customer = subscription.customer_id
                if not customer:
                    logger.error(f"Customer não encontrado Apple - {kind}: {original_id}")
                    return {'message': 'Usuário inativo'}, 401

                if kind == 'DID_RENEW':
                    # Notificações podem chegar fora de ordem: nunca encurta um período já gravado.
                    current_end = _as_utc(subscription.current_period_end)
                    if current_end and next_payment_date < current_end:
                        logger.info(f"Apple DID_RENEW antigo ignorado para subscription {subscription.id}")
                        return {'message': 'Webhook processado com sucesso'}, 200

                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    subscription.current_period_start = _ms_to_dt(tx.purchaseDate) or now
                    subscription.current_period_end = next_payment_date
                    subscription.grace_period_end = next_payment_date + timedelta(days=grace_days)
                    subscription.access_blocked = False
                    subscription.failure_message = None
                    subscription.canceled_at = None
                    _apple_register_payment(subscription, tx, now, next_payment_date)

                    # TODO: se a renovação trouxe outro productId (downgrade agendado), atualize o plano.

                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()

                elif kind == 'DID_CHANGE_RENEWAL_STATUS':
                    if subtype == 'AUTO_RENEW_DISABLED':
                        # Usuário desligou a renovação nos Ajustes do iPhone: acesso segue até o fim do período.
                        subscription.status = 'canceled'
                        subscription.provider_status = 'canceled'
                        subscription.canceled_at = now
                        if expires:
                            subscription.current_period_end = expires
                            subscription.grace_period_end = expires + timedelta(days=grace_days)
                        if customer.require_payment_method is False:
                            customer.can_change_plan = True
                        subscription.save()
                        customer.save()
                    elif subtype == 'AUTO_RENEW_ENABLED':
                        if expires and expires > now:
                            subscription.status = 'active'
                            subscription.provider_status = 'succeeded'
                            subscription.canceled_at = None
                            subscription.access_blocked = False
                            customer.require_payment_method = False
                            customer.can_change_plan = False
                            subscription.save()
                            customer.save()

                elif kind == 'DID_FAIL_TO_RENEW':
                    # A Apple continua tentando cobrar (billing retry / grace period).
                    subscription.provider_status = 'failed'
                    subscription.status = 'pendingPayment'
                    subscription.failure_message = f'Cobrança recorrente rejeitada (evento: {kind})'
                    _apple_register_payment(
                        subscription, tx, now, None, status='rejected', payment_id=f"fail-{transaction_id}")
                    subscription.save()
                    logger.warning(f"Apple renewal failed for subscription {subscription.id}")

                elif kind in ('EXPIRED', 'GRACE_PERIOD_EXPIRED'):
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = subscription.canceled_at or now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()

                elif kind in ('REFUND', 'REVOKE'):
                    # A Apple devolveu o valor / revogou o direito: encerra o acesso.
                    subscription.provider_status = 'refunded'
                    subscription.refunded_at = now
                    subscription.status = 'canceled'
                    subscription.canceled_at = subscription.canceled_at or now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()
                    logger.info(f"Apple {kind} for subscription {subscription.id}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing Apple webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500


def _find_pagarme_subscription(data):
    """Localiza a Subscription local de um evento do Pagar.me: primeiro pelo
    provider_subscription_id (id sub_... já preenchido), senão pelo
    metadata.local_subscription_id enviado no payment link."""
    sub_id = (
        (data.get('subscription') or {}).get('id')
        or (data.get('invoice') or {}).get('subscription_id')
        or (data.get('id') if str(data.get('id', '')).startswith('sub_') else None)
    )
    if sub_id:
        found = Subscription.objects(provider_subscription_id=str(sub_id), visible=True).first()
        if found:
            return found

    metadata = data.get('metadata') or {}
    local_id = metadata.get('local_subscription_id')
    if not local_id:
        # charges trazem o metadata no objeto de subscription aninhado
        local_id = ((data.get('subscription') or {}).get('metadata') or {}).get('local_subscription_id')
    if local_id:
        try:
            return Subscription.objects(id=local_id, visible=True).first()
        except Exception:
            return None
    return None


@api.route('/pagarme')
class PagarmeWebhook(Resource):

    @api.doc('pagarme_webhook', description='Webhook do Pagar.me (Stone) para assinaturas e cobranças')
    def post(self):
        """Processar notificações do Pagar.me (subscription.*, charge.*)"""
        try:
            # Auth: o Pagar.me envia as credenciais Basic Auth configuradas no dashboard.
            user = Config.PAGARME_WEBHOOK_USER
            password = Config.PAGARME_WEBHOOK_PASSWORD
            if user or password:
                auth = request.authorization
                if not auth or not hmac.compare_digest(auth.username or '', user or '') \
                        or not hmac.compare_digest(auth.password or '', password or ''):
                    logger.error("Invalid Pagar.me webhook credentials - rejecting request")
                    return {'message': 'Invalid credentials'}, 401
            else:
                logger.warning("PAGARME_WEBHOOK_USER/PASSWORD not configured - processing without validation")

            body = request.get_json() or {}
            event = body.get('type')
            data = body.get('data') or {}

            logger.info(f"Received Pagar.me webhook - Event: {event}, ID: {data.get('id')}")

            if not event:
                return {'message': 'Invalid webhook data'}, 400

            # Só tratamos eventos de assinatura/cobrança
            if not (event.startswith('subscription.') or event.startswith('charge.')):
                logger.info(f"Pagar.me webhook event ignored: {event}")
                return {'message': 'Webhook recebido'}, 200

            subscription = _find_pagarme_subscription(data)
            if not subscription:
                logger.warning(
                    f"Pagar.me subscription ainda não persistida localmente para evento {event}; solicitando reenvio"
                )
                return {'message': 'Assinatura ainda não disponível localmente'}, 503

            customer = subscription.customer_id
            now = datetime.now(timezone.utc)
            period_days = period_days_for_frequency(subscription.frequency, subscription.billing_cycle)

            if event in ('subscription.created', 'subscription.activated', 'subscription.updated'):
                sub_status = data.get('status')
                if sub_status in ('active', 'trialing', 'trial'):
                    subscription.provider_subscription_id = str(data.get('id') or subscription.provider_subscription_id)
                    subscription.provider_customer_id = (data.get('customer') or {}).get('id') or subscription.provider_customer_id
                    subscription.status = 'active'
                    subscription.provider_status = 'succeeded'
                    if not subscription.current_period_start:
                        subscription.current_period_start = now
                    subscription.current_period_end = now + timedelta(days=period_days)
                    subscription.grace_period_end = subscription.current_period_end + timedelta(
                        days=Config.PAGARME_DAYS_TO_EXPIRE
                    )
                    subscription.access_blocked = False
                    customer.require_payment_method = False
                    customer.can_change_plan = False
                    subscription.save()
                    customer.save()
                elif sub_status in ('canceled', 'expired'):
                    subscription.status = 'canceled'
                    subscription.provider_status = 'canceled'
                    subscription.canceled_at = now
                    if customer.require_payment_method is False:
                        customer.can_change_plan = True
                    subscription.save()
                    customer.save()
                else:
                    logger.info(f"Pagar.me subscription event {event} with status {sub_status} - no local change")

            elif event == 'subscription.canceled':
                subscription.status = 'canceled'
                subscription.provider_status = 'canceled'
                subscription.canceled_at = now
                if customer.require_payment_method is False:
                    customer.can_change_plan = True
                subscription.save()
                customer.save()

            elif event in ('charge.paid', 'invoice.paid'):
                charge_id = str(data.get('id'))
                already_registered = any(
                    p.provider_payment_id == charge_id
                    for p in subscription.payment_history
                )
                next_payment_date = now + timedelta(days=period_days)
                grace_period_end = next_payment_date + timedelta(days=Config.PAGARME_DAYS_TO_EXPIRE)

                subscription.status = 'active'
                subscription.current_period_end = next_payment_date
                subscription.grace_period_end = grace_period_end
                subscription.provider_status = 'succeeded'
                subscription.failure_message = None
                subscription.canceled_at = None
                if not subscription.provider_subscription_id:
                    sub_ref = (data.get('subscription') or {}).get('id')
                    if sub_ref:
                        subscription.provider_subscription_id = str(sub_ref)

                if not already_registered:
                    amount = data.get('amount')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=charge_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=data.get('currency', 'BRL'),
                        status='approved',
                        paid_at=now,
                        period_start=now,
                        period_end=next_payment_date,
                    ))

                customer.require_payment_method = False
                customer.can_change_plan = False
                subscription.save()
                customer.save()
                logger.info(
                    f"Pagar.me charge paid for subscription {subscription.id}. "
                    f"Next payment: {next_payment_date.date()}, grace ends: {grace_period_end.date()}"
                )

            elif event in ('charge.payment_failed', 'invoice.payment_failed'):
                charge_id = str(data.get('id'))
                already_registered = any(
                    p.provider_payment_id == charge_id
                    for p in subscription.payment_history
                )
                subscription.provider_status = 'failed'
                subscription.status = 'pendingPayment'
                subscription.failure_message = f'Cobrança recorrente rejeitada (evento: {event})'
                if not already_registered:
                    amount = data.get('amount')
                    subscription.payment_history.append(SubscriptionPayment(
                        provider_payment_id=charge_id,
                        amount=(amount / 100.0) if isinstance(amount, (int, float)) else subscription.amount,
                        currency=data.get('currency', 'BRL'),
                        status='rejected',
                        paid_at=now,
                    ))
                subscription.save()
                logger.warning(f"Pagar.me charge failed for subscription {subscription.id} (event: {event})")

            elif event in ('charge.refunded', 'charge.chargedback'):
                subscription.provider_status = 'refunded'
                subscription.refunded_at = now
                subscription.save()
                logger.info(f"Pagar.me charge refunded for subscription {subscription.id}")

            else:
                logger.info(f"Pagar.me webhook event not handled: {event}")

            return {'message': 'Webhook processado com sucesso'}, 200

        except Exception as e:
            logger.error(f"Error processing Pagar.me webhook: {str(e)}")
            return {'message': 'Erro ao processar webhook'}, 500