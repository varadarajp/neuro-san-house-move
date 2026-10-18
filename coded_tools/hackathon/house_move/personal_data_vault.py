"""Field-level encryption for the standalone app's personal data."""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

logger = logging.getLogger(__name__)


class PersonalDataVault:
    """
    Encrypts and decrypts personal-data values (names, street lines, email) with Fernet (AES with an HMAC).
    The key comes from HOUSE_MOVE_VAULT_KEY. If that is unset, a key file is created next to the case database
    with owner-only permissions: fine for a hackathon, replaced by a managed key service in production.
    """

    KEY_ENV: str = "HOUSE_MOVE_VAULT_KEY"

    def __init__(self, key_file: Path, key: bytes | None = None):
        """
        :param key_file: where to keep a generated key when HOUSE_MOVE_VAULT_KEY is not set.
        :param key: an explicit key, used by unit tests.
        """
        self._fernet: Fernet = Fernet(key or self._load_or_create_key(key_file))

    def encrypt(self, value: str) -> bytes:
        """
        :return: the encrypted token for value.
        """
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, token: bytes) -> str | None:
        """
        :return: the original value, or None if the token was made with a different key.
        """
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            logger.error("A vault value could not be decrypted: the key has changed or the data is corrupt")
            return None

    @staticmethod
    def _load_or_create_key(key_file: Path) -> bytes:
        """
        Reads the key from the environment, then the key file, and creates the file if neither exists.
        """
        from_env: str | None = os.environ.get(PersonalDataVault.KEY_ENV)
        if from_env:
            return from_env.encode("utf-8")
        if key_file.exists():
            return key_file.read_bytes().strip()
        logger.warning("No %s set: creating a local vault key at %s", PersonalDataVault.KEY_ENV, key_file)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key: bytes = Fernet.generate_key()
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        return key
