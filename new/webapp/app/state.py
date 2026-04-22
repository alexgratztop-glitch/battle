"""In-memory game state: games, teams, players, captain/radist/crew roles."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shared_simple import Action, ActionType, ShipType, Team

from .engine import TEAM_FROM_LETTER, TEAM_LETTER, WebEngine, resolve_type


# --- Phases / roles --------------------------------------------------------

PHASE_LOBBY = "lobby"
PHASE_PLANNING = "planning"
PHASE_FINISHED = "finished"

ROLE_CAPTAIN = "captain"
ROLE_RADIST = "radist"
ROLE_CREW = "crew"

DEFAULT_TEAM_NAMES = {Team.TEAM_A: "Team A",
                      Team.TEAM_B: "Team B",
                      Team.TEAM_C: "Team C"}

MAX_PLAYERS_PER_TEAM = 8
POOL_SIZE = 8
PLANNING_TIMEOUT_SECONDS = 90
MODE_ADVANCED = "advanced"
MODE_NORMAL = "normal"


def new_token(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(8)}"


def new_public_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def new_join_key() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(4))


# --- Player / team dataclasses ---------------------------------------------


@dataclass
class Player:
    pid: str
    token: str
    name: str
    team: Optional[Team] = None
    role: str = ROLE_CREW                         # captain | radist | crew
    assigned_ships: List[str] = field(default_factory=list)
    connected: bool = True

    @property
    def is_captain(self) -> bool:
        return self.role == ROLE_CAPTAIN

    @property
    def is_radist(self) -> bool:
        return self.role == ROLE_RADIST

    def public_view(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "team": TEAM_LETTER.get(self.team) if self.team else None,
            "role": self.role,
            "assigned_ships": list(self.assigned_ships),
            "connected": self.connected,
        }


@dataclass
class TeamState:
    team: Team
    display_name: str
    captain_pid: Optional[str] = None
    radist_pid: Optional[str] = None
    player_ids: List[str] = field(default_factory=list)
    pool: List[ShipType] = field(default_factory=list)
    ready: bool = False
    # Sightings known to the RADIST (auto-aggregated from crew each turn).
    # ship_id -> {x, y, z, type, turn_seen, reported_by}
    radist_intel: Dict[str, dict] = field(default_factory=dict)
    # Sightings explicitly relayed to the CAPTAIN.
    captain_intel: Dict[str, dict] = field(default_factory=dict)
    # Captain orders (per ship). ship_id -> {action_type, target, note, turn}
    orders: Dict[str, dict] = field(default_factory=dict)
    # Radist suggestions (captain can promote to order with 1 click).
    radist_suggestions: Dict[str, dict] = field(default_factory=dict)
    # Crew-queued per-ship actions for the current planning turn.
    planned_actions: Dict[str, Action] = field(default_factory=dict)

    def lobby_view(self) -> dict:
        return {
            "letter": TEAM_LETTER[self.team],
            "name": self.display_name,
            "captain_pid": self.captain_pid,
            "radist_pid": self.radist_pid,
            "player_ids": list(self.player_ids),
            "pool": [t.value for t in self.pool],
            "ready": self.ready,
            "slots_free": MAX_PLAYERS_PER_TEAM - len(self.player_ids),
        }


# --- Game ------------------------------------------------------------------


@dataclass
class Game:
    gid: str
    public_id: str
    join_key: str
    created_at: float
    phase: str = PHASE_LOBBY
    mode: str = MODE_ADVANCED
    teams: Dict[Team, TeamState] = field(default_factory=dict)
    players: Dict[str, Player] = field(default_factory=dict)
    engine: Optional[WebEngine] = None
    planning_deadline: Optional[float] = None
    planning_timeout: int = PLANNING_TIMEOUT_SECONDS
    turn: int = 0
    turn_results: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.teams:
            for team in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C):
                self.teams[team] = TeamState(
                    team=team, display_name=DEFAULT_TEAM_NAMES[team])

    # ---- lobby ----------------------------------------------------------

    def add_player(self, name: str) -> Player:
        pid = new_token("p_")
        token = new_token("t_")
        player = Player(pid=pid, token=token,
                        name=name.strip()[:24] or "Игрок")
        self.players[pid] = player
        return player

    def player_by_token(self, token: str) -> Optional[Player]:
        for p in self.players.values():
            if p.token == token:
                return p
        return None

    def pick_team(self, pid: str, team_letter: str) -> Tuple[bool, str]:
        if self.phase != PHASE_LOBBY:
            return False, "Команды уже сформированы"
        team = TEAM_FROM_LETTER.get(team_letter)
        if team is None:
            return False, "Неизвестная команда"
        player = self.players.get(pid)
        if not player:
            return False, "Игрок не найден"
        if player.team == team:
            return True, "уже в команде"
        # detach from old team
        if player.team is not None:
            old = self.teams[player.team]
            if pid in old.player_ids:
                old.player_ids.remove(pid)
            if old.captain_pid == pid:
                old.captain_pid = None
            if old.radist_pid == pid:
                old.radist_pid = None
        tstate = self.teams[team]
        team_limit = 1 if self.mode == MODE_NORMAL else MAX_PLAYERS_PER_TEAM
        if len(tstate.player_ids) >= team_limit:
            return False, "В команде уже максимум игроков для этого режима"
        tstate.player_ids.append(pid)
        player.team = team
        player.role = ROLE_CAPTAIN if self.mode == MODE_NORMAL else ROLE_CREW
        if self.mode == MODE_NORMAL:
            tstate.captain_pid = pid
        return True, "ok"

    def claim_role(self, pid: str, role: str) -> Tuple[bool, str]:
        """Player claims captain or radist (first-come locks the slot).
        Passing ROLE_CREW releases whatever role they had."""
        if self.phase != PHASE_LOBBY:
            return False, "Роли уже зафиксированы"
        if self.mode == MODE_NORMAL:
            return False, "В обычном режиме роль всегда капитан"
        if role not in (ROLE_CAPTAIN, ROLE_RADIST, ROLE_CREW):
            return False, "Неизвестная роль"
        player = self.players.get(pid)
        if not player or player.team is None:
            return False, "Сначала выберите команду"
        tstate = self.teams[player.team]
        # Release current role first.
        if player.role == ROLE_CAPTAIN and tstate.captain_pid == pid:
            tstate.captain_pid = None
        if player.role == ROLE_RADIST and tstate.radist_pid == pid:
            tstate.radist_pid = None
        player.role = ROLE_CREW
        if role == ROLE_CREW:
            return True, "ok"
        if role == ROLE_CAPTAIN:
            if tstate.captain_pid is not None:
                return False, "Капитан уже выбран"
            tstate.captain_pid = pid
            player.role = ROLE_CAPTAIN
        else:  # ROLE_RADIST
            if tstate.radist_pid is not None:
                return False, "Радист уже выбран"
            tstate.radist_pid = pid
            player.role = ROLE_RADIST
        return True, "ok"

    def leave(self, pid: str) -> None:
        player = self.players.pop(pid, None)
        if not player or player.team is None:
            return
        tstate = self.teams[player.team]
        if pid in tstate.player_ids:
            tstate.player_ids.remove(pid)
        if tstate.captain_pid == pid:
            tstate.captain_pid = None
        if tstate.radist_pid == pid:
            tstate.radist_pid = None

    # ---- captain setup --------------------------------------------------

    def rename_team(self, pid: str, new_name: str) -> Tuple[bool, str]:
        player = self.players.get(pid)
        if not player or not player.is_captain:
            return False, "Только капитан переименовывает команду"
        name = new_name.strip()[:24]
        if not name:
            return False, "Имя не может быть пустым"
        self.teams[player.team].display_name = name  # type: ignore[index]
        return True, "ok"

    def set_pool(self, pid: str, type_names: List[str]) -> Tuple[bool, str]:
        if self.mode == MODE_NORMAL:
            return False, "В обычном режиме пул фиксирован (крейсеры)"
        player = self.players.get(pid)
        if not player or not player.is_captain:
            return False, "Только капитан выбирает пул"
        if self.phase != PHASE_LOBBY:
            return False, "Пул уже зафиксирован"
        if len(type_names) != POOL_SIZE:
            return False, f"Пул должен содержать {POOL_SIZE} кораблей"
        pool: List[ShipType] = []
        for n in type_names:
            t = resolve_type(n)
            if t is None:
                return False, f"Неизвестный тип: {n}"
            pool.append(t)
        self.teams[player.team].pool = pool  # type: ignore[index]
        return True, "ok"

    def assign_ship(self, captain_pid: str, crew_pid: str,
                    ship_id: str) -> Tuple[bool, str]:
        if self.mode == MODE_NORMAL:
            return False, "В обычном режиме распределение не нужно"
        captain = self.players.get(captain_pid)
        if not captain or not captain.is_captain:
            return False, "Только капитан распределяет корабли"
        crew = self.players.get(crew_pid)
        if not crew or crew.team != captain.team:
            return False, "Игрок не в вашей команде"
        if crew.is_captain or crew.is_radist:
            return False, "Капитан и радист не управляют кораблями"
        for p in self.players.values():
            if p.team == captain.team and ship_id in p.assigned_ships:
                p.assigned_ships.remove(ship_id)
        if ship_id not in crew.assigned_ships:
            crew.assigned_ships.append(ship_id)
        return True, "ok"

    def unassign_ship(self, captain_pid: str, ship_id: str) -> Tuple[bool, str]:
        if self.mode == MODE_NORMAL:
            return False, "В обычном режиме распределение не нужно"
        captain = self.players.get(captain_pid)
        if not captain or not captain.is_captain:
            return False, "Только капитан распределяет корабли"
        for p in self.players.values():
            if p.team == captain.team and ship_id in p.assigned_ships:
                p.assigned_ships.remove(ship_id)
        return True, "ok"

    def captain_ready(self, pid: str, ready: bool) -> Tuple[bool, str]:
        player = self.players.get(pid)
        if not player or not player.is_captain:
            return False, "Только капитан"
        tstate = self.teams[player.team]  # type: ignore[index]
        if self.mode == MODE_NORMAL and not tstate.pool:
            tstate.pool = [ShipType.CRUISER] * 10
        required_pool_size = 10 if self.mode == MODE_NORMAL else POOL_SIZE
        if len(tstate.pool) != required_pool_size:
            return False, f"Сначала выберите {required_pool_size} кораблей"
        tstate.ready = ready
        return True, "ok"

    def all_teams_ready(self) -> bool:
        required_pool_size = 10 if self.mode == MODE_NORMAL else POOL_SIZE
        if self.mode == MODE_NORMAL:
            # One captain per team in normal mode.
            for tstate in self.teams.values():
                if tstate.captain_pid is None:
                    return False
        return all(t.ready and len(t.pool) == required_pool_size
                   for t in self.teams.values())

    # ---- game start / actions ------------------------------------------

    def start_game(self) -> Tuple[bool, str]:
        if self.phase != PHASE_LOBBY:
            return False, "Игра уже идёт"
        if not self.all_teams_ready():
            return False, "Не все капитаны готовы"
        if self.mode == MODE_NORMAL:
            for tstate in self.teams.values():
                if not tstate.pool:
                    tstate.pool = [ShipType.CRUISER] * 10
        pools = {team: ts.pool for team, ts in self.teams.items()}
        self.engine = WebEngine(pools=pools)
        self.engine.game_state["phase"] = "planning"
        self.phase = PHASE_PLANNING
        self.turn = 0
        self.planning_deadline = time.time() + self.planning_timeout
        # Distribute ships: captain+radist don't get ships; crew round-robin.
        for tstate in self.teams.values():
            ship_ids = sorted([sid for sid, s in self.engine.game_state["ships"].items()
                               if s.team == tstate.team])
            pre_assigned = {sid for p in self.players.values()
                            if p.team == tstate.team for sid in p.assigned_ships}
            remaining = [sid for sid in ship_ids if sid not in pre_assigned]
            crew = [self.players[pid] for pid in tstate.player_ids
                    if self.players[pid].role == ROLE_CREW]
            crew.sort(key=lambda p: p.pid)
            # If no crew at all, captain gets the ships as fallback.
            if not crew:
                if tstate.captain_pid:
                    crew = [self.players[tstate.captain_pid]]
                else:
                    # No humans at all — leave unassigned.
                    continue
            for i, sid in enumerate(remaining):
                crew[i % len(crew)].assigned_ships.append(sid)
        # Auto-fill first intel snapshot for radists.
        self._refresh_radist_intel()
        return True, "ok"

    def _refresh_radist_intel(self) -> None:
        """Aggregate everything any crew ship can currently see into the
        radist's view for that team."""
        if self.engine is None:
            return
        for tstate in self.teams.values():
            visible = self.engine.get_visible_enemies(tstate.team)
            for sid, payload in visible.items():
                entry = dict(payload)
                entry["turn_seen"] = self.turn
                entry["reported_by"] = "auto"
                tstate.radist_intel[sid] = entry

    def submit_action(self, pid: str, ship_id: str,
                      action_type: str,
                      tx: Optional[int] = None,
                      ty: Optional[int] = None,
                      tz: Optional[int] = None) -> Tuple[bool, str]:
        player = self.players.get(pid)
        if not player or player.team is None:
            return False, "Нет команды"
        if self.phase != PHASE_PLANNING or self.engine is None:
            return False, "Сейчас не фаза планирования"
        if self.mode == MODE_ADVANCED and player.role != ROLE_CREW:
            return False, "В этом режиме только экипаж управляет кораблями"
        if ship_id not in player.assigned_ships:
            return False, "Это не ваш корабль"
        try:
            at = ActionType(action_type)
        except ValueError:
            return False, f"Неизвестное действие: {action_type}"
        action = Action(ship_id=ship_id, action_type=at,
                        target_x=tx, target_y=ty, target_z=tz)
        self.teams[player.team].planned_actions[ship_id] = action
        return True, "ok"

    def clear_action(self, pid: str, ship_id: str) -> Tuple[bool, str]:
        player = self.players.get(pid)
        if not player or player.team is None:
            return False, "Нет команды"
        if ship_id not in player.assigned_ships:
            return False, "Это не ваш корабль"
        self.teams[player.team].planned_actions.pop(ship_id, None)
        return True, "ok"

    def submit_order(self, captain_pid: str, ship_id: str,
                     action_type: str,
                     tx: Optional[int] = None,
                     ty: Optional[int] = None,
                     tz: Optional[int] = None,
                     note: str = "") -> Tuple[bool, str]:
        captain = self.players.get(captain_pid)
        if not captain or not captain.is_captain:
            return False, "Только капитан отдаёт приказы"
        try:
            ActionType(action_type)
        except ValueError:
            return False, f"Неизвестное действие: {action_type}"
        self.teams[captain.team].orders[ship_id] = {  # type: ignore[index]
            "action_type": action_type,
            "target": [tx, ty, tz] if tx is not None else None,
            "note": note.strip()[:200],
            "turn": self.turn,
            "from": "captain",
        }
        return True, "ok"

    def clear_order(self, captain_pid: str, ship_id: str) -> Tuple[bool, str]:
        captain = self.players.get(captain_pid)
        if not captain or not captain.is_captain:
            return False, "Только капитан"
        self.teams[captain.team].orders.pop(ship_id, None)  # type: ignore[index]
        return True, "ok"

    def submit_suggestion(self, radist_pid: str, ship_id: str,
                          action_type: str,
                          tx: Optional[int] = None,
                          ty: Optional[int] = None,
                          tz: Optional[int] = None,
                          note: str = "") -> Tuple[bool, str]:
        radist = self.players.get(radist_pid)
        if not radist or not radist.is_radist:
            return False, "Только радист предлагает подсказки"
        try:
            ActionType(action_type)
        except ValueError:
            return False, f"Неизвестное действие: {action_type}"
        self.teams[radist.team].radist_suggestions[ship_id] = {  # type: ignore[index]
            "action_type": action_type,
            "target": [tx, ty, tz] if tx is not None else None,
            "note": note.strip()[:200],
            "turn": self.turn,
            "from": "radist",
        }
        return True, "ok"

    def promote_suggestion(self, captain_pid: str,
                           ship_id: str) -> Tuple[bool, str]:
        """Captain turns the radist's suggestion into a real order in 1 click."""
        captain = self.players.get(captain_pid)
        if not captain or not captain.is_captain:
            return False, "Только капитан"
        tstate = self.teams[captain.team]  # type: ignore[index]
        sug = tstate.radist_suggestions.get(ship_id)
        if not sug:
            return False, "Нет подсказки для этого корабля"
        tstate.orders[ship_id] = dict(sug, promoted_from="radist")
        return True, "ok"

    def share_intel(self, radist_pid: str, enemy_ship_id: str) -> Tuple[bool, str]:
        """Radist explicitly relays a sighting to the captain."""
        radist = self.players.get(radist_pid)
        if not radist or not radist.is_radist or radist.team is None:
            return False, "Только радист передаёт капитану"
        tstate = self.teams[radist.team]
        sighting = tstate.radist_intel.get(enemy_ship_id)
        if not sighting:
            return False, "Этой цели нет в данных радара"
        tstate.captain_intel[enemy_ship_id] = dict(sighting, relayed_turn=self.turn)
        return True, "ok"

    def process_turn_if_ready(self) -> Optional[dict]:
        if self.phase != PHASE_PLANNING or self.engine is None:
            return None
        now = time.time()
        deadline_passed = (self.planning_deadline is not None
                           and now >= self.planning_deadline)
        all_queued = True
        for tstate in self.teams.values():
            own_ships = [sid for sid, s in self.engine.game_state["ships"].items()
                         if s.team == tstate.team and s.alive]
            if any(sid not in tstate.planned_actions for sid in own_ships):
                all_queued = False
                break
        if not (all_queued or deadline_passed):
            return None
        for tstate in self.teams.values():
            self.engine.submit_actions(tstate.team,
                                       list(tstate.planned_actions.values()))
        summary = self.engine.step_turn()
        self.turn = self.engine.game_state["turn"]
        self.turn_results.append(summary)
        # Reset plans + per-turn captain orders; keep intel (ages via turn_seen).
        for tstate in self.teams.values():
            tstate.planned_actions = {}
            tstate.orders = {}
            tstate.radist_suggestions = {}
        self._refresh_radist_intel()
        if summary.get("game_over"):
            self.phase = PHASE_FINISHED
            self.planning_deadline = None
        else:
            self.planning_deadline = time.time() + self.planning_timeout
        return summary

    # ---- game master controls ------------------------------------------

    def gm_set_timeout(self, seconds: int) -> Tuple[bool, str]:
        if seconds < 5 or seconds > 600:
            return False, "Таймаут должен быть в диапазоне 5..600 секунд"
        self.planning_timeout = seconds
        if self.phase == PHASE_PLANNING:
            self.planning_deadline = time.time() + self.planning_timeout
        return True, "ok"

    def gm_override_ship(self, ship_id: str,
                         x: Optional[int] = None,
                         y: Optional[int] = None,
                         z: Optional[int] = None,
                         alive: Optional[bool] = None,
                         hits: Optional[int] = None) -> Tuple[bool, str]:
        if self.engine is None:
            return False, "Игра ещё не запущена"
        ship = self.engine.game_state["ships"].get(ship_id)
        if ship is None:
            return False, f"Неизвестный корабль: {ship_id}"

        nx = ship.x if x is None else int(x)
        ny = ship.y if y is None else int(y)
        nz = ship.z if z is None else int(z)
        if not (0 <= nx <= 9 and 0 <= ny <= 9 and 0 <= nz <= 9):
            return False, "Координаты вне диапазона 0..9"
        ship.x, ship.y, ship.z = nx, ny, nz

        if alive is not None:
            ship.alive = bool(alive)
        if hits is not None:
            try:
                nh = int(hits)
            except (TypeError, ValueError):
                return False, "hits должен быть числом"
            ship.hits = max(0, min(ship.max_hits, nh))
        return True, "ok"

    def gm_stop(self) -> Tuple[bool, str]:
        if self.engine is not None:
            self.engine.game_state["game_over"] = True
            self.engine.game_state["winner"] = "Остановлено гейммастером"
        self.phase = PHASE_FINISHED
        self.planning_deadline = None
        return True, "ok"


# --- manager singleton -----------------------------------------------------


class GameManager:
    def __init__(self) -> None:
        self._games: Dict[str, Game] = {}
        self._lock = threading.RLock()

    def create(self, mode: str = MODE_ADVANCED) -> Game:
        with self._lock:
            if mode not in (MODE_ADVANCED, MODE_NORMAL):
                mode = MODE_ADVANCED
            gid = new_token("g_")
            used_public_ids = {g.public_id for g in self._games.values()}
            public_id = new_public_id()
            while public_id in used_public_ids:
                public_id = new_public_id()
            game = Game(
                gid=gid,
                public_id=public_id,
                join_key=new_join_key(),
                created_at=time.time(),
                mode=mode,
            )
            self._games[gid] = game
            return game

    def get(self, gid: str) -> Optional[Game]:
        return self._games.get(gid)

    def resolve_by_public(self, public_id: str, join_key: str) -> Optional[Game]:
        code = (public_id or "").strip().upper()
        key = (join_key or "").strip()
        for g in self._games.values():
            if g.public_id == code and g.join_key == key:
                return g
        return None

    def list_summary(self) -> List[dict]:
        with self._lock:
            out = [{
                "gid": g.gid,
                "public_id": g.public_id,
                "join_key": g.join_key,
                "phase": g.phase,
                "mode": g.mode,
                "players": len(g.players),
                "turn": g.turn,
                "created_at": g.created_at,
            } for g in self._games.values()]
            out.sort(key=lambda d: d["created_at"], reverse=True)
            return out

    def delete(self, gid: str) -> bool:
        with self._lock:
            return self._games.pop(gid, None) is not None


MANAGER = GameManager()
