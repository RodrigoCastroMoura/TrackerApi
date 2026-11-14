from flask import request
from flask_restx import Namespace, Resource
from zoneinfo import ZoneInfo
import logging
import pandas as pd

from app.domain.models import Customer, Vehicle, User, Company
from app.presentation.auth_routes import token_required, require_permission

logger = logging.getLogger(__name__)

api = Namespace('stats', description='Estatísticas para o dashboard')

TZ = ZoneInfo('America/Sao_Paulo')


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _base_query(current_user):
    """
    Exclusão lógica (visible ausente conta como visível) + escopo por perfil:
    - admin: visão geral, sem filtro de empresa
    - demais: somente a empresa do usuário
    """
    query = {'visible': {'$ne': False}}
    if current_user.role != 'admin':
        query['company_id'] = current_user.company_id.id
    return query


def _now_local():
    return pd.Timestamp.now(tz=TZ)


def _to_utc_naive(ts):
    """created_at é salvo com datetime.utcnow() (naive, UTC)."""
    return ts.tz_convert('UTC').tz_localize(None).to_pydatetime()


def _load_created(model, base_query, since_local, until_local=None):
    """
    Carrega só created_at/created_by/company_id dos registros criados em
    [since_local, until_local) direto da collection (sem dereferenciar) e devolve um
    DataFrame com created_at já convertido para America/Sao_Paulo.
    """
    window = {'$gte': _to_utc_naive(since_local)}
    if until_local is not None:
        window['$lt'] = _to_utc_naive(until_local)
    query = {**base_query, 'created_at': window}
    docs = model._get_collection().find(
        query, {'created_at': 1, 'created_by': 1, 'company_id': 1})
    df = pd.DataFrame(list(docs)).reindex(
        columns=['_id', 'created_at', 'created_by', 'company_id'])
    df['created_at'] = pd.to_datetime(df['created_at'], utc=True).dt.tz_convert(TZ)
    return df


def _parse_month(raw):
    """'YYYY-MM' -> (início do mês, início do mês seguinte, é o mês atual?). Meses futuros viram o atual."""
    current = _now_local().normalize().replace(day=1)
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


def _load_users(base_query):
    docs = User._get_collection().find(base_query, {'name': 1, 'company_id': 1})
    return pd.DataFrame(list(docs)).reindex(columns=['_id', 'name', 'company_id'])


def _load_companies():
    docs = Company._get_collection().find({'visible': {'$ne': False}}, {'name': 1})
    return pd.DataFrame(list(docs)).reindex(columns=['_id', 'name'])


def _daily_counts(df, days):
    """Série com a contagem de registros por dia, preenchendo dias sem registro com 0."""
    if df.empty:
        return pd.Series(0, index=days)
    counts = df['created_at'].dt.normalize().value_counts()
    return counts.reindex(days, fill_value=0)


def _fill_counts(groups, customers, vehicles, key, days):
    """
    Acrescenta ao DataFrame `groups` (um registro por empresa ou por usuário)
    as colunas customers/vehicles/total/avg_per_day, contando por `key`
    (company_id ou created_by). Grupos sem cadastro ficam com 0.
    """
    groups['customers'] = groups['_id'].map(customers[key].dropna().value_counts()).fillna(0).astype(int)
    groups['vehicles'] = groups['_id'].map(vehicles[key].dropna().value_counts()).fillna(0).astype(int)
    groups['total'] = groups['customers'] + groups['vehicles']
    groups['avg_per_day'] = (groups['total'] / days).round(2)
    return groups


def _items(groups, extra=()):
    items = []
    for _, r in groups.sort_values('total', ascending=False).iterrows():
        item = {
            'id': str(r['_id']),
            'name': r['name'],
            'customers': int(r['customers']),
            'vehicles': int(r['vehicles']),
            'total': int(r['total']),
            'avg_per_day': float(r['avg_per_day']),
        }
        for col in extra:
            item[col] = int(r[col])
        items.append(item)
    return items


def _first_name(name):
    parts = str(name or '').split()
    return parts[0] if parts else 'N/A'


# ----------------------------------------------------------------------
# GET /api/stats/dashboard
# ----------------------------------------------------------------------
@api.route('/dashboard')
class DashboardStats(Resource):

    @api.doc('get_dashboard_stats',
             params={'month': {'type': 'string', 'description': 'Mês de conferência (YYYY-MM). Padrão: mês atual'}})
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """Dados do dashboard no mês escolhido: totais, instalações, cadastros por dia e ranking de veículos"""
        try:
            try:
                month_start, month_end, is_current = _parse_month(request.args.get('month'))
            except ValueError as e:
                return {'message': str(e)}, 400

            base = _base_query(current_user)
            is_admin = current_user.role == 'admin'

            # --- Totais acumulados até o fim do mês consultado (count direto no banco) ---
            totals_query = dict(base)
            if not is_current:
                totals_query['created_at'] = {'$lt': _to_utc_naive(month_end)}
            total_customers = Customer._get_collection().count_documents(totals_query)
            total_vehicles = Vehicle._get_collection().count_documents(totals_query)

            # --- Cadastros do mês ---
            customers = _load_created(Customer, base, month_start, month_end)
            vehicles = _load_created(Vehicle, base, month_start, month_end)

            # Instalações = veículos cadastrados
            month_installs = int(len(vehicles))
            today = _now_local().normalize()
            today_installs = (
                int((vehicles['created_at'].dt.normalize() == today).sum()) if is_current else 0
            )

            # --- Cadastros por dia (mês inteiro; no mês atual, até hoje) ---
            last_day = (month_end - pd.Timedelta(days=1)).normalize()
            days = pd.date_range(month_start, min(last_day, today) if is_current else last_day, freq='D')
            daily = pd.DataFrame({
                'customers': _daily_counts(customers, days),
                'vehicles': _daily_counts(vehicles, days),
            })
            daily_registrations = [
                {
                    # ISO com offset (-03:00) pro JS não deslocar o dia ao fazer new Date()
                    'date': idx.isoformat(),
                    'customers': int(row.customers),
                    'vehicles': int(row.vehicles),
                }
                for idx, row in daily.iterrows()
            ]

            # --- Ranking do mês: SOMA DE VEÍCULOS cadastrados (admin por empresa, demais por usuário) ---
            key = 'company_id' if is_admin else 'created_by'
            regs = vehicles[key].dropna()

            user_stats = []
            if not regs.empty:
                counts = regs.value_counts().rename_axis('_id').reset_index(name='total')
                names = _load_companies() if is_admin else _load_users(base)
                merged = counts.merge(names[['_id', 'name']], on='_id', how='inner')
                top = merged.nlargest(5, 'total')
                user_stats = [
                    {
                        # mantém a chave user_name pro front: empresa (admin) ou primeiro nome (demais)
                        'user_name': str(r['name']) if is_admin else _first_name(r['name']),
                        'total_vehicles': int(r['total']),
                    }
                    for _, r in top.iterrows()
                ]

            return {
                'month': month_start.strftime('%Y-%m'),
                'is_current_month': is_current,
                'total_customers': total_customers,
                'total_vehicles': total_vehicles,
                'month_installs': month_installs,
                'today_installs': today_installs,
                'daily_registrations': daily_registrations,
                'ranking_by': 'company' if is_admin else 'user',
                'user_stats': user_stats,
            }, 200

        except Exception as e:
            logger.error(f"Error generating dashboard stats: {str(e)}", exc_info=True)
            return {'message': 'Erro ao gerar estatísticas do dashboard'}, 500


# ----------------------------------------------------------------------
# GET /api/stats/averages
# ----------------------------------------------------------------------
@api.route('/averages')
class AveragesStats(Resource):

    @api.doc('get_averages_stats',
             params={'days': {'type': 'integer', 'description': 'Janela em dias (padrão 30, máx 365)'}})
    @token_required
    @require_permission('vehicle', 'read')
    def get(self, current_user):
        """
        Médias de cadastro.
        - admin: agrupado por empresa (group_by = 'company')
        - demais: agrupado por funcionário da própria empresa (group_by = 'user')
        """
        try:
            try:
                days = int(request.args.get('days', 30))
            except ValueError:
                return {'message': 'days deve ser um número inteiro'}, 400
            days = max(1, min(days, 365))

            base = _base_query(current_user)
            since = _now_local().normalize() - pd.Timedelta(days=days - 1)

            customers = _load_created(Customer, base, since)
            vehicles = _load_created(Vehicle, base, since)
            users = _load_users(base)

            total_regs = len(customers) + len(vehicles)
            avg_vehicles_per_customer = round(len(vehicles) / len(customers), 2) if len(customers) else 0.0

            # ---------------- ADMIN: por empresa ----------------
            if current_user.role == 'admin':
                companies = _load_companies()
                companies['users'] = companies['_id'].map(
                    users['company_id'].dropna().value_counts()).fillna(0).astype(int)
                companies = _fill_counts(companies, customers, vehicles, 'company_id', days)

                n = len(companies)
                total_users = len(users)
                overall = {
                    'total_companies': n,
                    'total_users': total_users,
                    'total_registrations': total_regs,
                    'avg_registrations_per_company': round(total_regs / n, 2) if n else 0.0,
                    'avg_customers_per_company': round(companies['customers'].mean(), 2) if n else 0.0,
                    'avg_vehicles_per_company': round(companies['vehicles'].mean(), 2) if n else 0.0,
                    'avg_registrations_per_user': round(total_regs / total_users, 2) if total_users else 0.0,
                    'avg_registrations_per_day': round(total_regs / days, 2),
                    'avg_vehicles_per_customer': avg_vehicles_per_customer,
                }
                return {
                    'period_days': days,
                    'group_by': 'company',
                    'overall': overall,
                    'items': _items(companies, extra=('users',)),
                }, 200

            # ---------------- Demais: por funcionário ----------------
            # Parte da lista de usuários, assim quem não cadastrou nada entra na média com 0
            users = _fill_counts(users, customers, vehicles, 'created_by', days)

            n = len(users)
            overall = {
                'total_users': n,
                'total_registrations': total_regs,
                'avg_registrations_per_user': round(total_regs / n, 2) if n else 0.0,
                'avg_customers_per_user': round(users['customers'].mean(), 2) if n else 0.0,
                'avg_vehicles_per_user': round(users['vehicles'].mean(), 2) if n else 0.0,
                'avg_registrations_per_day': round(total_regs / days, 2),
                'avg_vehicles_per_customer': avg_vehicles_per_customer,
            }
            return {
                'period_days': days,
                'group_by': 'user',
                'overall': overall,
                'items': _items(users),
            }, 200

        except Exception as e:
            logger.error(f"Error generating averages stats: {str(e)}", exc_info=True)
            return {'message': 'Erro ao gerar médias de cadastro'}, 500