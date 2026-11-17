import logging
import os
from pathlib import Path

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
)
from config import Config

logger = logging.getLogger(__name__)


def _cfg(name, default=None):
    return getattr(Config, name, None) or os.environ.get(name, default)


class AppleService:
    _root_certs = None

    @classmethod
    def _load_root_certs(cls):
        if cls._root_certs is None:
            folder = Path(_cfg('APPLE_ROOT_CERTS_DIR', ''))
            cls._root_certs = [p.read_bytes() for p in sorted(folder.glob('*.cer'))]
            if not cls._root_certs:
                logger.error("Nenhum certificado raiz da Apple encontrado em %s", folder)
        return cls._root_certs

    @classmethod
    def _verifier(cls, environment):
        app_id = int(_cfg('APPLE_APP_ID', 0) or 0) or None
        return SignedDataVerifier(
            cls._load_root_certs(),
            True,
            environment,
            _cfg('APPLE_BUNDLE_ID'),
            app_id if environment == Environment.PRODUCTION else None,
        )

    @classmethod
    def decode_notification(cls, signed_payload):
        """Valida a assinatura (JWS) e devolve (notificação, ambiente).
        Tenta produção e depois sandbox. Retorna (None, None) se for inválida."""
        for env in (Environment.PRODUCTION, Environment.SANDBOX):
            try:
                return cls._verifier(env).verify_and_decode_notification(signed_payload), env
            except VerificationException:
                continue
        return None, None

    @classmethod
    def decode_transaction(cls, signed_transaction, environment):
        return cls._verifier(environment).verify_and_decode_signed_transaction(signed_transaction)
