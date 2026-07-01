from __future__ import annotations

import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext
from dotenv import load_dotenv

from app import db

load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ROLE_LEVELS = {"viewer": 1, "manager": 2, "admin": 3}
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def bootstrap_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or os.getenv("APP_PASSWORD") or "admin12345"
    name = os.getenv("ADMIN_NAME", "Admin").strip()
    existing = db.fetch_one("select id from users where lower(email) = lower(%s)", (email,))
    if existing:
        return
    db.execute(
        """
        insert into users (email, name, password_hash, role)
        values (%s, %s, %s, 'admin')
        returning id
        """,
        (email, name, hash_password(password)),
    )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    return db.fetch_one("select * from users where lower(email) = lower(%s)", (email.strip().lower(),))


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    return db.fetch_one("select * from users where id = %s and active = true", (user_id,))


def login_user(request: Request, user: dict[str, Any]) -> None:
    now = int(time.time())
    request.session.clear()
    request.session.update(
        {
            "user_id": str(user["id"]),
            "last_seen": now,
            "csrf_token": secrets.token_urlsafe(32),
        }
    )
    db.execute("update users set last_login_at = now() where id = %s returning id", (user["id"],))


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    last_seen = request.session.get("last_seen")
    if not user_id or not last_seen:
        return None
    if int(time.time()) - int(last_seen) > SESSION_TIMEOUT_SECONDS:
        request.session.clear()
        return None
    user = get_user_by_id(user_id)
    if not user:
        request.session.clear()
        return None
    request.session["last_seen"] = int(time.time())
    return user


def require_user(request: Request, min_role: str = "viewer") -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if ROLE_LEVELS[user["role"]] < ROLE_LEVELS[min_role]:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    return user


def can(user: dict[str, Any], min_role: str) -> bool:
    return ROLE_LEVELS[user["role"]] >= ROLE_LEVELS[min_role]


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=400, detail="Invalid form token. Refresh the page and try again.")
