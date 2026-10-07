import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: int, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> int | None:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
        return int(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header GitHub sends with every webhook delivery."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest raises on non-ASCII str input, so compare bytes.
    return hmac.compare_digest(expected.encode(), signature_header[7:].encode())


class SecretBox:
    """Encrypts env var values at rest so a database dump alone does not leak them."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    def encrypt_env(self, env: dict[str, str]) -> str:
        return self.encrypt(json.dumps(env, sort_keys=True))

    def decrypt_env(self, token: str) -> dict[str, str]:
        return json.loads(self.decrypt(token))
