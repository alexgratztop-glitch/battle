"""Player-facing HTTP + WebSocket routes."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import (APIRouter, Body, HTTPException, Query, Request,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..state import (MANAGER, PHASE_LOBBY, PHASE_PLANNING, ROLE_CAPTAIN,
                     ROLE_CREW, ROLE_RADIST)
from ..views import game_lobby_view, game_play_view
from ..ws import HUB
from .admin import _broadcast_state


router = APIRouter()


class JoinPayload(BaseModel):
    name: str = Field(min_length=1, max_length=24)


@router.get("/resolve")
async def resolve_game(code: str = Query(...), key: str = Query(...)) -> dict:
    g = MANAGER.resolve_by_public(code, key)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена: проверьте ID и ключ")
    return {"gid": g.gid, "public_id": g.public_id, "mode": g.mode}


def _must_get(gid: str):
    g = MANAGER.get(gid)
    if g is None:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    return g


def _auth_player(game, token: Optional[str]):
    if not token:
        raise HTTPException(status_code=401, detail="Нужен токен игрока")
    p = game.player_by_token(token)
    if p is None:
        raise HTTPException(status_code=401, detail="Игрок не найден")
    return p


@router.get("/{gid}")
async def get_lobby(gid: str) -> dict:
    g = _must_get(gid)
    return game_lobby_view(g)


@router.get("/{gid}/state")
async def get_state(gid: str, token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    return game_play_view(g, p)


@router.post("/{gid}/join")
async def join(gid: str, payload: JoinPayload) -> dict:
    g = _must_get(gid)
    player = g.add_player(payload.name)
    await _broadcast_state(gid)
    return {"pid": player.pid, "token": player.token,
            "state": game_play_view(g, player)}


@router.post("/{gid}/pick_team")
async def pick_team(gid: str,
                    token: str = Query(...),
                    team: str = Query(..., pattern="^[ABC]$")) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.pick_team(p.pid, team)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/claim_role")
async def claim_role(gid: str,
                     token: str = Query(...),
                     role: str = Query(...)) -> dict:
    if role not in (ROLE_CAPTAIN, ROLE_RADIST, ROLE_CREW):
        raise HTTPException(status_code=400, detail="Неизвестная роль")
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.claim_role(p.pid, role)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/rename_team")
async def rename_team(gid: str,
                      token: str = Query(...),
                      name: str = Body(..., embed=True)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.rename_team(p.pid, name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


class PoolPayload(BaseModel):
    pool: list[str]


@router.post("/{gid}/pool")
async def set_pool(gid: str,
                   payload: PoolPayload,
                   token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.set_pool(p.pid, payload.pool)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


class AssignPayload(BaseModel):
    crew_pid: str
    ship_id: str


@router.post("/{gid}/assign")
async def assign(gid: str, payload: AssignPayload,
                 token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.assign_ship(p.pid, payload.crew_pid, payload.ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/unassign")
async def unassign(gid: str,
                   ship_id: str = Body(..., embed=True),
                   token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.unassign_ship(p.pid, ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/ready")
async def ready(gid: str,
                ready: bool = Body(..., embed=True),
                token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.captain_ready(p.pid, ready)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    # If all 3 teams are ready, auto-start the game.
    if g.all_teams_ready() and g.phase == PHASE_LOBBY:
        g.start_game()
    await _broadcast_state(gid)
    return game_play_view(g, p)


class ActionPayload(BaseModel):
    ship_id: str
    action_type: str
    tx: Optional[int] = None
    ty: Optional[int] = None
    tz: Optional[int] = None
    note: str = ""


@router.post("/{gid}/action")
async def action(gid: str, payload: ActionPayload,
                 token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.submit_action(p.pid, payload.ship_id,
                              payload.action_type,
                              payload.tx, payload.ty, payload.tz)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    # Auto-resolve if everyone has queued actions.
    g.process_turn_if_ready()
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/clear_action")
async def clear_action(gid: str,
                       ship_id: str = Body(..., embed=True),
                       token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.clear_action(p.pid, ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/order")
async def order(gid: str, payload: ActionPayload,
                token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.submit_order(p.pid, payload.ship_id, payload.action_type,
                             payload.tx, payload.ty, payload.tz,
                             note=payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/clear_order")
async def clear_order(gid: str,
                      ship_id: str = Body(..., embed=True),
                      token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.clear_order(p.pid, ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/suggest")
async def suggest(gid: str, payload: ActionPayload,
                  token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.submit_suggestion(p.pid, payload.ship_id, payload.action_type,
                                  payload.tx, payload.ty, payload.tz,
                                  note=payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/promote_suggestion")
async def promote_suggestion(gid: str,
                             ship_id: str = Body(..., embed=True),
                             token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.promote_suggestion(p.pid, ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.post("/{gid}/share_intel")
async def share_intel(gid: str,
                      enemy_ship_id: str = Body(..., embed=True),
                      token: str = Query(...)) -> dict:
    g = _must_get(gid)
    p = _auth_player(g, token)
    ok, msg = g.share_intel(p.pid, enemy_ship_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await _broadcast_state(gid)
    return game_play_view(g, p)


@router.websocket("/{gid}/ws")
async def player_socket(ws: WebSocket, gid: str, token: str) -> None:
    g = MANAGER.get(gid)
    if g is None:
        await ws.close(code=4404)
        return
    p = g.player_by_token(token)
    if p is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    await HUB.register_player(gid, p.pid, ws)
    try:
        # Initial snapshot.
        await ws.send_text(json.dumps(
            {"type": "state", "data": game_play_view(g, p)},
            default=str))
        while True:
            # Listen for pings to keep connection alive; no commands yet.
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await HUB.unregister_player(gid, p.pid)
