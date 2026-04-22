"""Role-aware serialization of game state for clients.

Captain sees: own ships + intel explicitly relayed by the radist.
Radist sees:  own ships + full intel auto-aggregated from crew vision.
Crew sees:   only the ships they personally control + what those ships see.
GM sees:     full world (all ships, holograms, mines).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from shared_simple import Team

from .engine import TEAM_LETTER
from .state import (
    Game,
    Player,
    PHASE_FINISHED,
    PHASE_LOBBY,
    PHASE_PLANNING,
    ROLE_CAPTAIN,
    ROLE_CREW,
    ROLE_RADIST,
    TeamState,
)


def game_lobby_view(game: Game) -> dict:
    return {
        "gid": game.gid,
        "public_id": game.public_id,
        "mode": game.mode,
        "phase": game.phase,
        "turn": game.turn,
        "teams": [t.lobby_view() for t in game.teams.values()],
        "players": [p.public_view() for p in game.players.values()],
        "planning_deadline": game.planning_deadline,
        "all_ready": game.all_teams_ready() if game.phase == PHASE_LOBBY else False,
    }


def _team_roster(game: Game, team: Team) -> List[dict]:
    tstate = game.teams[team]
    return [game.players[pid].public_view()
            for pid in tstate.player_ids if pid in game.players]


def _own_ships(game: Game, team: Team) -> Dict[str, dict]:
    if game.engine is None:
        return {}
    return game.engine.team_ships(team)


def _ships_for_crew(game: Game, player: Player) -> Dict[str, dict]:
    """Only the crew member's own ships (fully detailed)."""
    if game.engine is None or player.team is None:
        return {}
    own = game.engine.team_ships(player.team)
    return {sid: own[sid] for sid in player.assigned_ships if sid in own}


def _enemies_visible_to(game: Game, player: Player) -> Dict[str, dict]:
    """Visibility from THIS player's ships only (not the whole team)."""
    if game.engine is None or player.team is None:
        return {}
    # Temporarily compute: strict per-ship visibility. We reuse engine logic
    # by filtering the team-wide visible_enemies set down to ones within
    # range of any of the player's ships.
    team_visible = game.engine.get_visible_enemies(player.team)
    if not player.assigned_ships:
        return team_visible  # observers still see whatever their team sees
    own_ships = [s for sid, s in game.engine.game_state["ships"].items()
                 if sid in player.assigned_ships and s.alive]
    result: Dict[str, dict] = {}
    for eid, e in team_visible.items():
        for own in own_ships:
            dx = abs(e["x"] - own.x)
            dy = abs(e["y"] - own.y)
            dz = abs(e["z"] - own.z)
            # Radius 4 matches GameServer.get_visible_enemies.
            if max(dx, dy, dz) <= 4:
                result[eid] = e
                break
            # Radiovyshka (radio) sees whole Z plane.
            if getattr(own, "scan_whole_z", False) and own.z == e["z"]:
                result[eid] = e
                break
    return result


def game_play_view(game: Game, player: Player) -> dict:
    """State payload for a player (shape depends on their role)."""
    out: dict = {
        "gid": game.gid,
        "public_id": game.public_id,
        "mode": game.mode,
        "phase": game.phase,
        "turn": game.turn,
        "you": player.public_view(),
        "teams": [t.lobby_view() for t in game.teams.values()],
        "players": [p.public_view() for p in game.players.values()],
        "planning_deadline": game.planning_deadline,
    }

    if player.team is None:
        return out

    tstate: TeamState = game.teams[player.team]
    out["team"] = {
        "letter": TEAM_LETTER[player.team],
        "name": tstate.display_name,
        "pool": [t.value for t in tstate.pool],
        "ready": tstate.ready,
        "captain_pid": tstate.captain_pid,
        "radist_pid": tstate.radist_pid,
        "roster": _team_roster(game, player.team),
    }

    # In lobby/setup we only need lobby-level info.
    if game.phase == PHASE_LOBBY:
        return out

    # Game is running — role-specific payloads.
    if game.engine is None:
        return out

    all_own = _own_ships(game, player.team)
    out["team"]["ships"] = all_own
    out["team"]["orders"] = dict(tstate.orders)
    if player.role == ROLE_RADIST:
        out["team"]["suggestions"] = dict(tstate.radist_suggestions)
        # Crew actions are visible to radist only.
        out["team"]["planned_actions"] = {
            sid: {
                "ship_id": act.ship_id,
                "action_type": act.action_type.value,
                "target": [act.target_x, act.target_y, act.target_z]
                    if act.target_x is not None else None,
            } for sid, act in tstate.planned_actions.items()
        }
    out["recent_turn"] = (game.turn_results[-1]
                          if game.turn_results else None)

    if player.role == ROLE_CAPTAIN:
        # Captain sees only own ships map; enemy intel is hidden.
        out["intel"] = {}
        out["role_view"] = "captain"
    elif player.role == ROLE_RADIST:
        out["intel"] = dict(tstate.radist_intel)
        out["role_view"] = "radist"
    else:  # crew
        out["intel"] = _enemies_visible_to(game, player)
        out["role_view"] = "crew"
        # Crew only sees their own ships in detail; teammates shown as icons.
        out["my_ships"] = _ships_for_crew(game, player)

    return out


def gm_view(game: Game) -> dict:
    out: dict = {
        "gid": game.gid,
        "public_id": game.public_id,
        "join_key": game.join_key,
        "mode": game.mode,
        "phase": game.phase,
        "turn": game.turn,
        "teams": [t.lobby_view() for t in game.teams.values()],
        "players": [p.public_view() for p in game.players.values()],
        "planning_deadline": game.planning_deadline,
        "planning_timeout": game.planning_timeout,
    }
    if game.engine is not None:
        out["ships"] = game.engine.ships_snapshot()
        out["holograms"] = list(game.engine.game_state.get("holograms", {}).values())
        out["mines"] = list(game.engine.game_state.get("mines", []))
        out["hit_history"] = list(game.engine.game_state.get("hit_history", []))
        out["game_over"] = game.engine.game_state.get("game_over", False)
        out["winner"] = game.engine.game_state.get("winner")
    out["team_intel"] = {
        TEAM_LETTER[t]: {
            "captain_intel": tstate.captain_intel,
            "radist_intel": tstate.radist_intel,
            "orders": tstate.orders,
            "suggestions": tstate.radist_suggestions,
        } for t, tstate in game.teams.items()
    }
    return out
