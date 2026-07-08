import logging
from typing import Optional, Dict, Any, List
import requests
from config import Config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.abacatepay.com/v2"


class AbacatePayService:
    """Service for handling AbacatePay payment operations (assinaturas recorrentes via PIX/cartão)"""

    @staticmethod
    def _headers() -> Optional[Dict[str, str]]:
        if not Config.ABACATEPAY_API_KEY:
            logger.error("ABACATEPAY_API_KEY not configured")
            return None
        return {
            "Authorization": f"Bearer {Config.ABACATEPAY_API_KEY}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _request(method: str, path: str, **kwargs) -> Optional[Dict[str, Any]]:
        headers = AbacatePayService._headers()
        if not headers:
            return None

        url = f"{BASE_URL}{path}"
        try:
            logger.info(f"[AbacatePay REQUEST] {method} {path} | body={kwargs.get('json')}")
            response = requests.request(method, url, headers=headers, timeout=15, **kwargs)
            body = response.json()
            logger.info(f"[AbacatePay RESPONSE] {method} {path} | status={response.status_code} | body={body}")

            if not body.get('success'):
                error = body.get('error')
                message = error.get('message') if isinstance(error, dict) else error
                return {'error': True, 'message': message or 'Unknown error', 'status': response.status_code}

            return body.get('data')

        except requests.RequestException as e:
            logger.error(f"Error calling AbacatePay {method} {path}: {str(e)}")
            return None

    @staticmethod
    def create_customer(name: str, email: str, tax_id: str, cellphone: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cria (ou reaproveita) um cliente no AbacatePay. Retorna dict com 'id', ou None/erro."""
        payload = {"name": name, "email": email, "taxId": tax_id}
        if cellphone:
            payload["cellphone"] = cellphone
        return AbacatePayService._request("POST", "/customers/create", json=payload)

    @staticmethod
    def create_product(
        external_id: str,
        name: str,
        amount: float,
        cycle: Optional[str] = None,
        description: Optional[str] = None,
        trial_days: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Cria um produto no AbacatePay (equivalente a um plano de assinatura).
        cycle: WEEKLY | MONTHLY | QUARTERLY | SEMIANNUALLY | ANNUALLY (omitir para pagamento único)
        """
        payload: Dict[str, Any] = {
            "externalId": external_id,
            "name": name,
            "price": int(round(float(amount) * 100)),
            "currency": "BRL",
        }
        if cycle:
            payload["cycle"] = cycle
        if description:
            payload["description"] = description
        if trial_days:
            payload["trialDays"] = trial_days

        return AbacatePayService._request("POST", "/products/create", json=payload)

    @staticmethod
    def create_subscription_checkout(
        product_id: str,
        customer_id: Optional[str] = None,
        external_id: Optional[str] = None,
        return_url: Optional[str] = None,
        completion_url: Optional[str] = None,
        methods: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Cria o checkout de assinatura recorrente. Retorna dict com 'id', 'url', 'status'."""
        payload: Dict[str, Any] = {"items": [{"id": product_id, "quantity": 1}]}
        if customer_id:
            payload["customerId"] = customer_id
        if external_id:
            payload["externalId"] = external_id
        if return_url:
            payload["returnUrl"] = return_url
        if completion_url:
            payload["completionUrl"] = completion_url
        if methods:
            payload["methods"] = methods
        if metadata:
            payload["metadata"] = metadata

        return AbacatePayService._request("POST", "/subscriptions/create", json=payload)

    @staticmethod
    def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
        return AbacatePayService._request("GET", f"/subscriptions/get?id={subscription_id}")

    @staticmethod
    def cancel_subscription(subscription_id: str) -> bool:
        result = AbacatePayService._request("POST", "/subscriptions/cancel", json={"id": subscription_id})
        if not result:
            return False
        if isinstance(result, dict) and result.get('error'):
            logger.error(f"Error canceling AbacatePay subscription {subscription_id}: {result.get('message')}")
            return False
        return True

    @staticmethod
    def change_subscription_plan(subscription_id: str, product_id: str) -> Optional[Dict[str, Any]]:
        return AbacatePayService._request(
            "POST",
            "/subscriptions/change-plan",
            json={"id": subscription_id, "productId": product_id, "quantity": 1},
        )
