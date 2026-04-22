"""WebSocket connection registry.

Keeps track of player and GM sockets per game so we can broadcast updates.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        # gid -> pid -> websocket
        self._players: Dict[str, Dict[str, WebSocket]] = {}
        # gid -> set of GM sockets
        self._gms: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register_player(self, gid: str, pid: str, ws: WebSocket) -> None:
        async with self._lock:
            self._players.setdefault(gid, {})[pid] = ws

    async def unregister_player(self, gid: str, pid: str) -> None:
        async with self._lock:
            table = self._players.get(gid)
            if table and table.get(pid):
                table.pop(pid, None)

    async def register_gm(self, gid: str, ws: WebSocket) -> None:
        async with self._lock:
            self._gms.setdefault(gid, set()).add(ws)

    async def unregister_gm(self, gid: str, ws: WebSocket) -> None:
        async with self._lock:
            s = self._gms.get(gid)
            if s and ws in s:
                s.remove(ws)

    async def send_to_player(self, gid: str, pid: str, payload: Any) -> None:
        ws = self._players.get(gid, {}).get(pid)
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps(payload, default=str))
        except Exception:
            # Socket probably dead; drop it silently. HTTP heartbeat will rehydrate.
            await self.unregister_player(gid, pid)

    async def broadcast_to_players(self, gid: str,
                                   payload_builder) -> None:
        """`payload_builder(pid) -> dict` is called per player so we can send
        role-specific views without leaking info across players."""
        table = dict(self._players.get(gid, {}))
        for pid, ws in table.items():
            try:
                payload = payload_builder(pid)
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                await self.unregister_player(gid, pid)

    async def broadcast_to_gms(self, gid: str, payload: Any) -> None:
        table = list(self._gms.get(gid, set()))
        for ws in table:
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                await self.unregister_gm(gid, ws)


HUB = Hub()
