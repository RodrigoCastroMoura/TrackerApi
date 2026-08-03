import logging
import os
import uuid
from urllib.parse import quote, unquote, urlparse

import firebase_admin
from firebase_admin import credentials, storage

from config import Config

logger = logging.getLogger(__name__)

_firebase_app = None


def _get_bucket():
    global _firebase_app

    bucket_name = (Config.FIREBASE_BUCKET_NAME or '').replace('gs://', '').strip('/')
    if not bucket_name:
        raise ValueError("FIREBASE_BUCKET_NAME não configurado")

    if not Config.FIREBASE_CREDENTIALS or not os.path.exists(Config.FIREBASE_CREDENTIALS):
        raise ValueError(
            "FIREBASE_CREDENTIALS não configurado ou arquivo não encontrado")

    if _firebase_app is None:
        cred = credentials.Certificate(Config.FIREBASE_CREDENTIALS)
        _firebase_app = firebase_admin.initialize_app(
            cred, {'storageBucket': bucket_name})

    return storage.bucket(bucket_name, app=_firebase_app)


class FirebaseStorage:
    """Upload/download de arquivos no Firebase Cloud Storage."""

    def save_file(self, file):
        bucket = _get_bucket()

        filename = getattr(file, 'filename', None) or f"{uuid.uuid4()}.pdf"
        blob_path = f"documents/{uuid.uuid4()}_{filename}"

        blob = bucket.blob(blob_path)
        download_token = str(uuid.uuid4())
        blob.metadata = {'firebaseStorageDownloadTokens': download_token}
        blob.upload_from_file(file, content_type='application/pdf')

        return (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/"
            f"{quote(blob_path, safe='')}?alt=media&token={download_token}"
        )

    def delete_file(self, url):
        bucket = _get_bucket()

        path = urlparse(url).path  # /v0/b/<bucket>/o/<blob_path>
        marker = '/o/'
        if marker not in path:
            logger.warning(f"URL do Firebase Storage não reconhecida: {url}")
            return
        blob_path = unquote(path.split(marker, 1)[1])

        bucket.blob(blob_path).delete()
