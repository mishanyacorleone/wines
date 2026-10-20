"""Пароли, токены сессий, разбор логина (почта или телефон)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

# scrypt из стандартной библиотеки — без лишних зависимостей. Параметры —
# рекомендованный минимум OWASP для интерактивного входа (~50 мс на хэш)
_N, _R, _P = 2**14, 8, 1
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginError(ValueError):
    """Логин не похож ни на почту, ни на российский номер телефона."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(actual.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_login(raw: str) -> tuple[str, str]:
    """('email', адрес) или ('phone', +7XXXXXXXXXX).

    Телефон принимается в любом привычном виде: 8 912 …, +7 (912) …, 912…
    """
    value = raw.strip()
    if "@" in value:
        email = value.lower()
        if not _EMAIL.match(email) or len(email) > 254:
            raise LoginError("Проверьте адрес почты")
        return "email", email
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    else:
        raise LoginError("Введите почту или номер телефона: +7 912 345-67-89")
    return "phone", "+" + digits


def mask_login(login: str | None) -> str:
    """Логин для логов: «m***@gmail.com», «+7***4567» — узнаваемо, но не целиком."""
    if not login:
        return "—"
    if "@" in login:
        name, domain = login.split("@", 1)
        return f"{name[:1]}***@{domain}"
    return f"{login[:2]}***{login[-4:]}"
