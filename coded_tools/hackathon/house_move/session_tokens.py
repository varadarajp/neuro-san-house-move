"""Signed session tokens: the only thing the client holds."""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from coded_tools.hackathon.house_move.key_folder import KeyFolder

logger = logging.getLogger(__name__)


class SessionTokens:
    """
    Issues and verifies HMAC-SHA256 signed tokens that say who the caller is (owner_id) and which bank customer
    records they may open. The sign-in page issues them; every stateful tool verifies them through CaseSession.
    A client can read a token but cannot forge or change one without the secret.
    The secret comes from HOUSE_MOVE_SESSION_SECRET, or a key file in the owner-only key folder (KeyFolder), away
    from the case data. In production the bank's own sign-in would issue the identity instead.
    """

    SECRET_ENV: str = "HOUSE_MOVE_SESSION_SECRET"
    # Repository root is three folders up: coded_tools/hackathon/house_move/session_tokens.py
    # Where earlier versions kept the secret; moved into the key folder on first use.
    LEGACY_KEY_FILE: Path = Path(__file__).resolve().parents[3] / "case_data" / "session.key"
    KEY_NAME: str = "session.key"
    LIFETIME_SECONDS: int = 7 * 24 * 3600
    BANK_PREFIX: str = "bank:"
    VERSION: str = "v1"

    def __init__(self, secret: bytes | None = None, key_file: Path | None = None):
        """
        :param secret: an explicit secret, used by unit tests.
        :param key_file: where to keep a generated secret when HOUSE_MOVE_SESSION_SECRET is not set.
        """
        self._secret: bytes = secret or self._load_or_create_secret(key_file)

    def issue(self, owner_id: str, customers: list[str], lifetime_seconds: int | None = None) -> str:
        """
        :param owner_id: who the caller is, for example "bank:C-10042" or "guest:<random>".
        :param customers: bank customer IDs this caller may open (empty for a standalone user).
        :return: the signed token.
        """
        payload: dict[str, Any] = {
            "sub": owner_id,
            "customers": sorted(set(customers)),
            "exp": int(time.time()) + (lifetime_seconds or SessionTokens.LIFETIME_SECONDS),
        }
        body: str = self._encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return f"{SessionTokens.VERSION}.{body}.{self._sign(body)}"

    def verify(self, token: Any) -> dict[str, Any] | None:
        """
        :return: the token's claims if it is well formed, correctly signed and not expired, otherwise None.
        """
        if not isinstance(token, str) or token.count(".") != 2:
            return None
        version, body, signature = token.split(".")
        if version != SessionTokens.VERSION or not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            claims: dict[str, Any] = json.loads(self._decode(body))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(claims.get("sub"), str) or int(claims.get("exp", 0)) < time.time():
            return None
        return claims

    @staticmethod
    def bank_owner_id(customer_id: str) -> str:
        """
        :return: the owner ID for a signed-in bank customer, for example "bank:C-10042".
        """
        return SessionTokens.BANK_PREFIX + customer_id

    @staticmethod
    def is_bank_owner(owner_id: str | None) -> bool:
        """
        :return: true if the owner signed in as a bank customer (not as a guest).
        """
        return bool(owner_id) and owner_id.startswith(SessionTokens.BANK_PREFIX)

    @staticmethod
    def new_guest_id() -> str:
        """
        :return: an unguessable owner ID for someone who is not a bank customer.
        """
        return "guest:" + secrets.token_urlsafe(16)

    def _sign(self, body: str) -> str:
        """
        :return: the encoded HMAC of the token body.
        """
        return self._encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())

    @staticmethod
    def _encode(raw: bytes) -> str:
        """
        URL-safe base64 without padding.
        """
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(text: str) -> bytes:
        """
        Reverses _encode.
        """
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    @staticmethod
    def _load_or_create_secret(key_file: Path | None) -> bytes:
        """
        Reads the secret from the environment, then the key file, and creates the file if neither exists.
        :param key_file: an explicit file; by default, the key folder's session key.
        """
        from_env: str | None = os.environ.get(SessionTokens.SECRET_ENV)
        if from_env:
            return from_env.encode("utf-8")
        key_file = key_file or KeyFolder.path(SessionTokens.KEY_NAME, legacy=SessionTokens.LEGACY_KEY_FILE)
        if key_file.exists():
            return key_file.read_bytes().strip()
        logger.warning("No %s set: creating a local session secret at %s", SessionTokens.SECRET_ENV, key_file)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        secret: bytes = secrets.token_hex(32).encode("ascii")
        key_file.write_bytes(secret)
        key_file.chmod(0o600)
        return secret
