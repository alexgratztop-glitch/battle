"""GM (admin) HTTP + WebSocket routes."""
from __future__ import annotations

import asyncio
import json

from fastapi import (APIRouter, Depends, Form, HTTPException, Query, Request,
                     Response, WebSocket, WebSocketDisconnect)
from fastapi.responses import JSONResponse

from ..auth import (SESSION_COOKIE, check_password, is_gm_cookie_valid,
                    make_gm_cookie, require_gm, require_gm_ws)
from ..state import MANAGER, MODE_ADVANCED, MODE_NORMAL, PHASE_PLANNING
from ..views import game_lobby_view, gm_view
from ..ws import HUB


router = APIRouter()


@router.post("/login")
async def login(password: str = Form(...)) -> JSONResponse:
    if not check_password(password):
        raise HTTPException(status_code=403, detail="Неверный пароль")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, make_gm_cookie(),
                    httponly=True, samesite="lax")
    return resp


@router.post("/logout")
async def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/session")
async def session(request: Request) -> dict:
    raw = request.cookies.get(SESSION_COOKIE)
    return {"authenticated": is_gm_cookie_valid(raw)}


@router.get("/games")
async def list_games(_: None = Depends(require_gm)) -> dict:
    return {"games": MANAGER.list_summary()}


@router.post("/games")
async def create_game(mode: str = Form(MODE_ADVANCED),
                      _: None = Depends(require_gm)) -> dict:
    if mode not in (MODE_ADVANCED, MODE_NORMAL):
        raise HTTPException(status_code=400, detail="Неизвестный режим")
    g = MANAGER.create(mode=mode)
    return {"gid": g.gid}


@router.get("/games/{gid}")
async def get_game(gid: str, _: None = Depends(require_gm)) -> dict:
    g = MANAGER.get(gid)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    return gm_view(g)


@router.delete("/games/{gid}")
async def delete_game(gid: str, _: None = Depends(require_gm)) -> dict:
    ok = MANAGER.delete(gid)
    return {"ok": ok}


@router.post("/games/{gid}/start")
async def force_start(gid: str, _: None = Depends(require_gm)) -> dict:
    g = MANAGER.get(gid)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    ok, msg = g.start_game()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return {"ok": True}


@router.post("/games/{gid}/force_turn")
async def force_turn(gid: str, _: None = Depends(require_gm)) -> dict:
    """Force resolve the current turn even if not all actions queued."""
    g = MANAGER.get(gid)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    if g.phase != PHASE_PLANNING or g.engine is None:
        raise HTTPException(status_code=400, detail="Не фаза планирования")
    # Expire the deadline so process_turn_if_ready fires.
    g.planning_deadline = 0
    summary = g.process_turn_if_ready()
    await _broadcast_state(gid)
    return {"ok": True, "summary": summary}


@router.post("/games/{gid}/command")
async def gm_command(gid: str,
                     command: str = Form(...),
                     ship_id: str | None = Form(None),
                     x: int | None = Form(None),
                     y: int | None = Form(None),
                     z: int | None = Form(None),
                     alive: bool | None = Form(None),
                     hits: int | None = Form(None),
                     seconds: int | None = Form(None),
                     _: None = Depends(require_gm)) -> dict:
    g = MANAGER.get(gid)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена")

    if command == "start_turn":
        # Web loop starts planning immediately after game start, so command is
        # kept as compatibility no-op for the desktop GM workflow.
        ok, msg = True, "ok"
    elif command == "end_planning":
        if g.phase != PHASE_PLANNING or g.engine is None:
            raise HTTPException(status_code=400, detail="Не фаза планирования")
        g.planning_deadline = 0
        g.process_turn_if_ready()
        ok, msg = True, "ok"
    elif command == "stop":
        ok, msg = g.gm_stop()
    elif command == "set_timeout":
        if seconds is None:
            raise HTTPException(status_code=400, detail="Нужно поле seconds")
        ok, msg = g.gm_set_timeout(seconds)
    elif command == "override_ship":
        if not ship_id:
            raise HTTPException(status_code=400, detail="Нужно поле ship_id")
        ok, msg = g.gm_override_ship(ship_id=ship_id, x=x, y=y, z=z,
                                     alive=alive, hits=hits)
    else:
        raise HTTPException(status_code=400, detail="Неизвестная команда GM")

    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return {"ok": True}


@router.websocket("/games/{gid}/ws")
async def gm_socket(ws: WebSocket, gid: str) -> None:
    cookies = dict(ws.cookies) if ws.cookies else {}
    if not require_gm_ws(cookies):
        await ws.close(code=4401)
        return
    g = MANAGER.get(gid)
    if g is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    await HUB.register_gm(gid, ws)
    try:
        # Send initial snapshot.
        await ws.send_text(json.dumps({"type": "state", "data": gm_view(g)}, default=str))
        while True:
            # We don't listen to any GM commands through WS yet — pure push.
            # Keep alive by awaiting receive (will raise on disconnect).
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await HUB.unregister_gm(gid, ws)


# --- shared broadcast helper ---------------------------------------------


async def _broadcast_state(gid: str) -> None:
    """Push updated state to all connected sockets for this game."""
    g = MANAGER.get(gid)
    if g is None:
        return

    def per_player(pid: str) -> dict:
        from ..views import game_play_view
        player = g.players.get(pid)
        if player is None:
            return {"type": "state", "data": game_lobby_view(g)}
        return {"type": "state", "data": game_play_view(g, player)}

    await HUB.broadcast_to_players(gid, per_player)
    await HUB.broadcast_to_gms(gid, {"type": "state", "data": gm_view(g)})


def get_broadcaster():
    return _broadcast_state
