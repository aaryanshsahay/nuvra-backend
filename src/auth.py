import hashlib
import secrets
from typing import Tuple


def _hash_with_salt(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = _hash_with_salt(password, salt)
    return f"{salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, stored_digest = password_hash.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(_hash_with_salt(password, salt), stored_digest)


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)
