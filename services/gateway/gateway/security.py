"""Security primitives: API keys, passwords, encrypted upstream credentials."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

KEY_PREFIX = "urk_"
_DISPLAY_PREFIX_LEN = len(KEY_PREFIX) + 8


def new_api_key() -> tuple[str, str, str]:
    """Return (full key, display prefix, sha256 hash). The full key is shown to
    the user exactly once; only the prefix and hash are stored.

    A fast hash (SHA-256) is fine here, unlike passwords: the key has 256 bits
    of randomness, so there is nothing to brute-force or look up in a
    dictionary. A fast hash also keeps per-request key lookup cheap."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:_DISPLAY_PREFIX_LEN], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# Passwords are low-entropy and human-chosen, so they need a slow, salted,
# memory-hard hash: argon2id (library defaults follow RFC 9106 guidance).
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class CredentialBox:
    """Encrypts upstream credentials at rest with Fernet (AES-128-CBC + HMAC-SHA256).
    The key comes from a Kubernetes Secret, never from the database, so a DB
    dump alone does not reveal upstream credentials."""

    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("credential cannot be decrypted (wrong GATEWAY_FERNET_KEY?)") from exc
