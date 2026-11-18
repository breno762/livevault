"""Autenticação: sessão em cookie httpOnly com JWT (ou Bearer). Senhas com scrypt (stdlib)."""
import base64
import hashlib
import hmac
import logging
import os
import time
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import User, utcnow

log = logging.getLogger("livevault.auth")
COOKIE_NAME = "lv_session"
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_token(user: User) -> str:
    now = utcnow()
    payload = {
        "sub": user.id, "username": user.username, "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def validate_auth_config():
    if not settings.auth_enabled:
        log.warning("AUTH_ENABLED=false: a API está ABERTA, sem login. Use apenas em ambiente local.")
        return
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("AUTH_ENABLED=true exige JWT_SECRET com pelo menos 32 caracteres.")


def ensure_admin(db: Session):
    """Cria o administrador inicial a partir do ambiente, se ainda não houver usuários."""
    if not settings.auth_enabled:
        return
    if db.scalar(select(User.id).limit(1)):
        return
    if len(settings.admin_password) < 8:
        raise RuntimeError("Defina ADMIN_PASSWORD (mín. 8 caracteres) para criar o administrador inicial.")
    db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
    db.commit()
    log.info("Administrador inicial '%s' criado.", settings.admin_username)


class Principal:
    def __init__(self, username: str, role: str):
        self.username = username
        self.role = role


ANONYMOUS_ADMIN = Principal("local", "admin")


def current_user(request: Request) -> Principal:
    if not settings.auth_enabled:
        return ANONYMOUS_ADMIN
    token = request.cookies.get(COOKIE_NAME)
    header = request.headers.get("authorization", "")
    if not token and header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        raise HTTPException(401, "Não autenticado")
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sessão inválida ou expirada")
    return Principal(data.get("username", ""), data.get("role", "viewer"))


def require_admin(user: Principal = Depends(current_user)) -> Principal:
    if user.role != "admin":
        raise HTTPException(403, "Sem permissão")
    return user


# Limitação simples de tentativas de login (em memória; suficiente para instância única).
_failures: dict[str, list[float]] = {}
_WINDOW, _MAX_FAILS = 300, 8


def login_throttled(key: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < _WINDOW]
    _failures[key] = recent
    return len(recent) >= _MAX_FAILS


def register_login_failure(key: str):
    _failures.setdefault(key, []).append(time.time())


def clear_login_failures(key: str):
    _failures.pop(key, None)


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # gasta o mesmo tempo para não revelar se o usuário existe
        verify_password(password, "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$" + base64.b64encode(b"0" * 32).decode())
        return None
    return user if verify_password(password, user.password_hash) else None


__all__ = [
    "Principal", "current_user", "require_admin", "authenticate", "create_token", "COOKIE_NAME",
    "login_throttled", "register_login_failure", "clear_login_failures",
]
