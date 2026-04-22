"""GM password auth using a signed-cookie session."""
from __future__ import annotations

from typing import Optional

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from .config import SETTINGS


_SERIALIZER = URLSafeSerializer(SETTINGS.session_secret, salt="gm-auth")
SESSION_COOKIE = "gm_session"


def make_gm_cookie() -> str:
    return _SERIALIZER.dumps({"gm": True})


def is_gm_cookie_valid(raw: Optional[str]) -> bool:
    if not raw:
        return False
    try:
        data = _SERIALIZER.loads(raw)
    except BadSignature:
        return False
    return bool(isinstance(data, dict) and data.get("gm") is True)


def require_gm(request: Request) -> None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not is_gm_cookie_valid(raw):
        raise HTTPException(status_code=401, detail="GM authentication required")


def require_gm_ws(cookies: dict) -> bool:
    raw = cookies.get(SESSION_COOKIE)
    return is_gm_cookie_valid(raw)


def check_password(candidate: str) -> bool:
    # Constant-time-ish compare.
    if len(candidate) != len(SETTINGS.gm_password):
        return False
    ok = 0
    for a, b in zip(candidate.encode(), SETTINGS.gm_password.encode()):
        ok |= a ^ b
    return ok == 0
