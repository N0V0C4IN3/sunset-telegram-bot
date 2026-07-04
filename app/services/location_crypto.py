import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import Settings


class LocationCrypto:
    def __init__(self, settings: Settings) -> None:
        digest = hashlib.sha256(settings.location_encryption_key.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt_coordinate(self, value: float) -> str:
        normalized = f"{value:.6f}"
        return self._fernet.encrypt(normalized.encode("utf-8")).decode("ascii")

    def decrypt_coordinate(self, value: str) -> float:
        return float(self._fernet.decrypt(value.encode("ascii")).decode("utf-8"))
