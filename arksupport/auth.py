"""Password, token, and credential validation helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1


def normalize_username(username: str) -> str:
    """Validate and normalize a login username."""
    value = str(username or "").strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("用户名必须为 3-32 位字母、数字、点、下划线或连字符。")
    return value.casefold()


def validate_password(password: str) -> str:
    """Validate a plaintext password without changing it."""
    value = str(password or "")
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise ValueError("密码长度必须为 10-128 个字符。")
    return value


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a random salt."""
    value = validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against the stored scrypt representation."""
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def generate_token(bytes_count: int = 32) -> str:
    """Generate a URL-safe opaque token."""
    return secrets.token_urlsafe(bytes_count)


def token_digest(token: str) -> str:
    """Return the storage-safe SHA-256 digest of an opaque token."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def generate_temporary_password() -> str:
    """Generate a one-time password that satisfies the password policy."""
    return secrets.token_urlsafe(18)
