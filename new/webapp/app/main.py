"""FastAPI entry point for the web edition of space-battle."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .routes.admin import router as admin_router
from .routes.play import router as play_router


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


app = FastAPI(title="Space Battle — Web Edition")

# Static assets (shared CSS/JS + the two SPAs).
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# REST / WS routers.
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(play_router, prefix="/api/play", tags=["play"])


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Космический бой</title>
<style>body{background:#0a0e27;color:#e0e0ff;font-family:Inter,Arial,sans-serif;
 display:flex;flex-direction:column;align-items:center;justify-content:center;
 min-height:100vh;margin:0;padding:16px;text-align:center}
 h1{color:#00d4ff} a{display:inline-block;margin:8px;padding:14px 24px;
 background:#1a1f3a;color:#00d4ff;border-radius:8px;text-decoration:none;
 border:1px solid #22305a;font-weight:600}
 a:hover{background:#22305a}</style></head>
<body>
<h1>🚀 Космический бой 10×10×10</h1>
<p>сделано Циппом 04.2026</p>
<div>
  <a href="/play">Играть</a>
  <a href="/admin">Мастер</a>
</div>
</body></html>"""
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "admin" / "index.html")


@app.get("/play", response_class=HTMLResponse)
async def play_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "play" / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
