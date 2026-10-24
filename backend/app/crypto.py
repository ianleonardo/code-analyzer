import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings_encryption_key

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    raw = settings_encryption_key().encode()
    try:
        return Fernet(raw)
    except ValueError:
        # Any other secret is stretched into a valid 32-byte urlsafe key.
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Return the plain text, or an empty string if it cannot be decrypted."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("A stored secret could not be decrypted; the encryption secret has changed")
        return ""
