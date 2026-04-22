"""Web-friendly wrapper around the existing game engine.

Reuses `GameServer` from `server_full_visibility.py` for all battle rules
(process_turn, visibility, collisions, phase/mine/hologram logic, victory)
but skips all TCP/socket/thread concerns. The webapp drives ticks manually
via `step_turn()` after collecting actions from WebSocket clients.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

# Make repo root importable so we can reuse existing modules without packaging.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared_simple import Action, ActionType, Ship, ShipType, Team  # noqa: E402
from server_full_visibility import GameServer  # noqa: E402


TEAM_LETTER = {Team.TEAM_A: "A", Team.TEAM_B: "B", Team.TEAM_C: "C"}
TEAM_FROM_LETTER = {"A": Team.TEAM_A, "B": Team.TEAM_B, "C": Team.TEAM_C}

# All types a captain can pick for the pool (advanced mode).
PICKABLE_TYPES: List[ShipType] = [
    ShipType.ARTILLERY,
    ShipType.RADIO,
    ShipType.JUMPER,
    ShipType.TORCH,
    ShipType.SILENCE,
    ShipType.DRILL,
    ShipType.PROVOCATEUR,
    ShipType.SPIDER,
    ShipType.CRUISER,
]

# Type-name → ShipType (Russian names used by the rest of the codebase).
TYPE_BY_NAME: Dict[str, ShipType] = {t.value: t for t in PICKABLE_TYPES}


def resolve_type(name: str) -> Optional[ShipType]:
    return TYPE_BY_NAME.get(name)


class WebEngine(GameServer):
    """GameServer without TCP plumbing.

    We still let the parent `__init__` allocate a socket object (it doesn't
    bind), but we never call `start()`. All ticking is driven by the webapp.

    The captain-driven ship pool is applied in `create_ships_from_pools()`,
    which replaces the default `create_ships()` behavior. We keep the same
    axis-based random spawn as the balance v7 rules.
    """

    def __init__(self, spawn_seed: Optional[int] = None,
                 pools: Optional[Dict[Team, List[ShipType]]] = None):
        # Parent __init__ calls self.create_ships(). We short-circuit that by
        # temporarily monkey-patching on the instance before super().__init__.
        self._pending_pools = pools
        super().__init__(host="127.0.0.1", port=0, game_mode="advanced",
                         gui=None, spawn_seed=spawn_seed)

    # --- Logging: just swallow. The webapp captures events via hit_history. --
    def log(self, message, tag="info"):  # type: ignore[override]
        # No-op: the rich event log already lives in game_state['last_events']
        # / hit_history. Dropping verbose text keeps HTTP payloads small.
        pass

    # --- Ship creation honoring captain-picked pools ------------------------
    def create_ships(self):  # type: ignore[override]
        pools = getattr(self, "_pending_pools", None)
        if not pools:
            # No pool provided — fall back to the default advanced loadout.
            super().create_ships()
            return

        # Mirror the balance-v7 spawn: pick a random axis per team, place the
        # pool along 8 non-conflicting cells on that axis.
        import random as _random
        spawn_rng = _random.Random(self.spawn_seed)

        ships: Dict[str, Ship] = {}
        used_cells = set()

        for team_letter, team in (("A", Team.TEAM_A),
                                  ("B", Team.TEAM_B),
                                  ("C", Team.TEAM_C)):
            pool = pools.get(team)
            if not pool:
                continue
            n = len(pool)
            chosen_line = None
            for _ in range(200):
                axis = spawn_rng.choice(["x", "y", "z"])
                if axis == "x":
                    fy = spawn_rng.randrange(10)
                    fz = spawn_rng.randrange(10)
                    xs = spawn_rng.sample(range(10), n)
                    line = [(x, fy, fz) for x in xs]
                elif axis == "y":
                    fx = spawn_rng.randrange(10)
                    fz = spawn_rng.randrange(10)
                    ys = spawn_rng.sample(range(10), n)
                    line = [(fx, y, fz) for y in ys]
                else:
                    fx = spawn_rng.randrange(10)
                    fy = spawn_rng.randrange(10)
                    zs = spawn_rng.sample(range(10), n)
                    line = [(fx, fy, z) for z in zs]
                if not any(cell in used_cells for cell in line):
                    chosen_line = line
                    break
            if chosen_line is None:
                fallback_y = ord(team_letter) - ord("A")
                chosen_line = [(0, fallback_y, z) for z in range(n)]
            used_cells.update(chosen_line)

            for idx, ship_type in enumerate(pool):
                x, y, z = chosen_line[idx]
                sid = f"{team_letter}_{idx + 1}"
                ships[sid] = Ship(
                    sid,
                    f"{ship_type.value} {team_letter}{idx + 1}",
                    team, x, y, z, ship_type,
                )

        self.game_state["ships"] = ships

    # --- Driving the turn manually ------------------------------------------
    def submit_actions(self, team: Team, actions: List[Action]) -> None:
        """Store per-team actions ahead of the turn tick."""
        self.actions_received[team] = list(actions)

    def step_turn(self) -> Dict:
        """Apply all queued actions via the parent `process_turn` and return
        a summary of what happened this turn (for broadcast)."""
        prev_turn = self.game_state["turn"]
        self.process_turn()
        summary = {
            "turn": self.game_state["turn"],
            "prev_turn": prev_turn,
            "last_hits": list(self.game_state.get("last_hits", [])),
            "last_events": list(self.game_state.get("last_events", [])),
            "game_over": self.game_state.get("game_over", False),
            "winner": self.game_state.get("winner"),
        }
        # Clear buffered actions for next tick.
        self.actions_received = {}
        return summary

    # --- Public queries -----------------------------------------------------
    def visible_enemies_for(self, team: Team) -> Dict[str, dict]:
        return self.get_visible_enemies(team)

    def ships_snapshot(self) -> Dict[str, dict]:
        return {sid: s.to_dict() for sid, s in self.game_state["ships"].items()}

    def team_ships(self, team: Team) -> Dict[str, dict]:
        return {sid: s.to_dict()
                for sid, s in self.game_state["ships"].items()
                if s.team == team}
