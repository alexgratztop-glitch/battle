"""Runtime configuration for the webapp."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gm_password: str
    session_secret: str
    debug: bool

    @classmethod
    def from_env(cls) -> "Settings":
        pwd = os.environ.get("GM_PASSWORD", "").strip()
        if not pwd:
            # Dev default — prominent so ops remember to set it in prod.
            pwd = "admin"
        secret = os.environ.get("SESSION_SECRET", "").strip()
        if not secret:
            secret = secrets.token_urlsafe(32)
        debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
        return cls(gm_password=pwd, session_secret=secret, debug=debug)


SETTINGS = Settings.from_env()
