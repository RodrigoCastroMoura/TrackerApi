import logging

import pandas as pd
from flask import request
from flask_restx import Namespace, Resource
from zoneinfo import ZoneInfo

from app.domain.models import Subscription, Customer, Company
from app.presentation.auth_routes import token_required, require_permission

logger = logging.getLogger(__name__)

api = Namespace('billing', description='Faturamento e inadimplência')

TZ = ZoneInfo('America/Sao_Paulo')

CANCEL_AFTER_DEFAULT = 3        # meses em débito para sugerir cancelamento
FIRST_CHARGE_GRACE_DAYS = 15    # prazo da 1ª cobrança quando não há payment_deadline

SITUACOES = ('em_dia', 'em_debito', 'pendente', 'cancelado')


# ----------------------------------------------------------------------
# Regras de negócio (funções puras, fáceis de testar)
# ----------------------------------------------------------------------
def _ts(value):
    """Converte para pd.Timestamp naive (as datas são salvas em UTC sem tz)."""
    if not value:
        return None
    ts = pd.Timestamp(value)
    return ts.tz_convert('UTC').tz_localize(None) if ts.tzinfo else ts


def _to_utc_naive(ts):
    return ts.tz_convert('UTC').tz_localize(None)


def _offset(frequency, cycle, n=1):
    """Duração de n períodos de cobrança (meses de calendário ou dias)."""
    amount = (frequency or 1) * n
    return pd.DateOffset(months=amount) if cycle == 'months' else pd.Timedelta(days=amount)


def _periods_overdue(covered_until, as_of, frequency, cycle):
    """
    Quantas cobranças já venceram sem pagamento até `as_of`. Cada vez que passa
    `covered_until + k * período` sem pagamento, conta mais um mês em débito.
    """
    n = 0
    due = covered_until
    while due < as_of and n < 240:
        n += 1
        due = covered_until + _offset(frequency, cycle, n)
    return n


def _analyze(sub, as_of, month_start, is_current, cancel_after):
    """
    Situação de pagamento de uma assinatura (documento cru do Mongo) na data `as_of`.
    Usa o histórico de pagamentos aprovados: cada um traz paid_at, period_start e period_end.
    "Pago até" = maior period_end entre os pagamentos aprovados até `as_of`; por isso dá pra
    conferir qualquer mês passado exatamente como estava naquela data.
    """
    freq = sub.get('frequency') or 1
    cycle = sub.get('billing_cycle') or 'months'
    amount = float(sub.get('amount') or 0)
    period = _offset(freq, cycle, 1)
    created = _ts(sub.get('created_at'))

    # Pagamentos aprovados até o fim do período consultado: (pago em, valor, início, fim)
    payments = []
    for p in (sub.get('payment_history') or []):
        if p.get('status') != 'approved':
            continue
        paid_at = _ts(p.get('paid_at'))
        if paid_at is None or paid_at > as_of:
            continue
        payments.append((
            paid_at,
            float(p.get('amount') or amount),
            _ts(p.get('period_start')) or paid_at,
            _ts(p.get('period_end')) or (paid_at + period),
        ))

    # Sem histórico, mas com cobrança inicial confirmada pelo provedor
    if not payments and sub.get('provider_status') == 'succeeded' and sub.get('payment_date'):
        first = _ts(sub['payment_date'])
        if first is not None and first <= as_of:
            payments.append((first, amount, first, first + period))

    never_paid = not payments
    last_payment = max((p[0] for p in payments), default=None)
    paid_in_month = [p[1] for p in payments if p[0] >= month_start]

    if never_paid:
        period_start = period_end = None
        covered_until = (_ts(sub.get('payment_deadline'))
                         or ((created or as_of) + pd.Timedelta(days=FIRST_CHARGE_GRACE_DAYS)))
    else:
        latest = max(payments, key=lambda p: p[3])      # pagamento que cobre o período mais longe
        period_start, period_end = latest[2], latest[3]
        covered_until = period_end

    canceled_at = _ts(sub.get('canceled_at'))
    is_canceled = sub.get('status') == 'canceled' and (canceled_at is None or canceled_at <= as_of)

    if is_canceled:
        situacao, months = 'cancelado', 0
    else:
        months = _periods_overdue(covered_until, as_of, freq, cycle)
        situacao = 'em_debito' if months > 0 else ('pendente' if never_paid else 'em_dia')

    return {
        'plan_name': sub.get('plan_name') or '',
        'amount': round(amount, 2),
        'status': sub.get('status'),
        'situacao': situacao,
        'last_payment_at': last_payment,
        'period_start': period_start,
        'period_end': period_end,
        'months_overdue': months,
        'days_overdue': max((as_of - covered_until).days, 0) if months > 0 else 0,
        'amount_due': round(months * amount, 2),
        'suggest_cancel': bool(situacao == 'em_debito' and months >= cancel_after),
        'access_blocked': bool(sub.get('access_blocked')) if is_current else False,
        'paid_in_month': bool(paid_in_month),
        'received_month': round(sum(paid_in_month), 2),
    }


def _summary(df, cancel_after):
    if df.empty:
        return {'total': 0, 'em_dia': 0, 'em_debito': 0, 'pendente': 0, 'cancelado': 0,
                'a_cancelar': 0, 'pagaram_mes': 0, 'valor_em_aberto': 0.0, 'recebido_mes': 0.0,
                'cancel_after': cancel_after}
    debito = df[df['situacao'] == 'em_debito']
    return {
        'total': int(len(df)),
        'em_dia': int((df['situacao'] == 'em_dia').sum()),
        'em_debito': int(len(debito)),
        'pendente': int((df['situacao'] == 'pendente').sum()),
        'cancelado': int((df['situacao'] == 'cancelado').sum()),
        'a_cancelar': int(df['suggest_cancel'].sum()),
        'pagaram_mes': int(df['paid_in_month'].sum()),
        'valor_em_aberto': round(float(debito['amount_due'].sum()), 2),
        'recebido_mes': round(float(df['received_month'].sum()), 2),
        'cancel_after': cancel_after,
    }


# ----------------------------------------------------------------------
# Carga de dados
# ----------------------------------------------------------------------
def _is_admin(current_user):
    return current_user.role == 'admin'


def _base_query(current_user):
    """admin: visão geral; demais: somente a empresa do usuário."""
    query = {'visible': {'$ne': False}}
    if not _is_admin(current_user):
        query['company_id'] = current_user.company_id.id
    return query


def _parse_cancel_after():
    try:
        value = int(request.args.get('cancel_after', CANCEL_AFTER_DEFAULT))
    except ValueError:
        value = CANCEL_AFTER_DEFAULT
    return max(1, min(value, 24))


def _parse_month(raw):
    """'YYYY-MM' -> (início do mês, início do mês seguinte, é o mês atual?). Meses futuros viram o atual."""
    current = pd.Timestamp.now(tz=TZ).normalize().replace(day=1)
    if not raw:
        start = current
    else:
        try:
            year, month = (int(x) for x in raw.split('-'))
            start = pd.Timestamp(year=year, month=month, day=1, tz=TZ)
        except Exception:
            raise ValueError('month deve estar no formato YYYY-MM')
        if start > current:
            start = current
    return start, start + pd.DateOffset(months=1), bool(start == current)


def _load_df(current_user, cancel_after, month_start, month_end, is_current):
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    as_of = now if is_current else _to_utc_naive(month_end) - pd.Timedelta(seconds=1)
    month_start_utc = _to_utc_naive(month_start)

    fields = ['customer_id', 'company_id', 'plan_name', 'amount', 'frequency', 'billing_cycle',
              'status', 'provider_status', 'payment_date', 'payment_deadline', 'created_at',
              'canceled_at', 'access_blocked', 'payment_history']
    subs = list(Subscription._get_collection().find(_base_query(current_user), {f: 1 for f in fields}))

    # Assinaturas criadas depois do mês consultado ainda não existiam
    subs = [s for s in subs if not (_ts(s.get('created_at')) and _ts(s.get('created_at')) > as_of)]

    customer_ids = list({s['customer_id'] for s in subs if s.get('customer_id')})
    customers = {
        c['_id']: c for c in Customer._get_collection().find(
            {'_id': {'$in': customer_ids}}, {'name': 1, 'document': 1, 'phone': 1})
    }
    companies = {}
    if _is_admin(current_user):
        companies = {c['_id']: c.get('name') for c in Company._get_collection().find({}, {'name': 1})}

    rows = []
    for s in subs:
        row = _analyze(s, as_of, month_start_utc, is_current, cancel_after)
        cust = customers.get(s.get('customer_id'), {})
        row.update({
            'subscription_id': str(s['_id']),
            'customer_id': str(s['customer_id']) if s.get('customer_id') else None,
            'customer_name': cust.get('name') or 'Cliente removido',
            'document': cust.get('document') or '',
            'phone': cust.get('phone') or '',
            'company_name': companies.get(s.get('company_id'), '') if companies else '',
        })
        rows.append(row)

    columns = ['subscription_id', 'customer_id', 'customer_name', 'document', 'phone', 'company_name',
               'plan_name', 'amount', 'status', 'situacao', 'last_payment_at', 'period_start',
               'period_end', 'months_overdue', 'days_overdue', 'amount_due', 'suggest_cancel',
               'access_blocked', 'paid_in_month', 'received_month']
    return pd.DataFrame(rows, columns=columns)


def _filter(df, status, q):
    if status and status != 'all' and status in SITUACOES:
        df = df[df['situacao'] == status]
    if q:
        term = q.strip().lower()
        if term:
            hay = (df['customer_name'].fillna('') + ' ' + df['document'].fillna('') + ' ' +
                   df['plan_name'].fillna('') + ' ' + df['company_name'].fillna('')).str.lower()
            df = df[hay.str.contains(term, regex=False)]
    return df.sort_values(['months_overdue', 'days_overdue', 'customer_name'],
                          ascending=[False, False, True])


def _iso(ts):
    return ts.isoformat() + 'Z' if ts is not None and not pd.isna(ts) else None


# ----------------------------------------------------------------------
# GET /api/billing/overview
# ----------------------------------------------------------------------
@api.route('/overview')
class BillingOverview(Resource):

    @api.doc('get_billing_overview',
             params={
                 'month': {'type': 'string', 'description': 'Mês de conferência (YYYY-MM). Padrão: mês atual'},
                 'status': {'type': 'string', 'enum': ['all', *SITUACOES], 'description': 'Filtro de situação'},
                 'cancel_after': {'type': 'integer', 'description': 'Meses em débito para sugerir cancelamento (padrão 3)'},
                 'q': {'type': 'string', 'description': 'Busca por cliente, documento, plano ou empresa'},
             })
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Quem pagou, quem está em débito e há quantos meses (no mês escolhido)"""
        try:
            try:
                month_start, month_end, is_current = _parse_month(request.args.get('month'))
            except ValueError as e:
                return {'message': str(e)}, 400

            cancel_after = _parse_cancel_after()
            df = _load_df(current_user, cancel_after, month_start, month_end, is_current)
            summary = _summary(df, cancel_after)  # resumo sempre sobre o escopo todo
            view = _filter(df, request.args.get('status', 'all'), request.args.get('q'))

            items = [
                {
                    'subscription_id': r.subscription_id,
                    'customer_id': r.customer_id,
                    'customer_name': r.customer_name,
                    'document': r.document,
                    'phone': r.phone,
                    'company_name': r.company_name,
                    'plan_name': r.plan_name,
                    'amount': float(r.amount),
                    'situacao': r.situacao,
                    'last_payment_at': _iso(r.last_payment_at),
                    'period_start': _iso(r.period_start),
                    'period_end': _iso(r.period_end),
                    'months_overdue': int(r.months_overdue),
                    'days_overdue': int(r.days_overdue),
                    'amount_due': float(r.amount_due),
                    'suggest_cancel': bool(r.suggest_cancel),
                    'access_blocked': bool(r.access_blocked),
                    'paid_in_month': bool(r.paid_in_month),
                }
                for r in view.itertuples(index=False)
            ]

            return {
                'month': month_start.strftime('%Y-%m'),
                'is_current_month': is_current,
                'scope': 'all' if _is_admin(current_user) else 'company',
                'summary': summary,
                'items': items,
            }, 200

        except Exception as e:
            logger.error(f"Error generating billing overview: {str(e)}", exc_info=True)
            return {'message': 'Erro ao gerar visão de faturamento'}, 500