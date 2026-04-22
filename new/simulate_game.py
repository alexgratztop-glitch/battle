"""Headless-симуляция одного матча Space Battle 10×10×10.

Запускает настоящую игровую логику (`GameServer.process_turn` и
`handle_gm_command`), минуя TCP и Tk: это гарантирует, что симуляция тестирует
ТОТ ЖЕ код, который работает в реальной игре, но без зависимости от сети и GUI.

На каждом ходу:
  * GM-бот посылает ``start_turn`` → сервер «разрешает» фазу планирования.
  * Каждая из 3-х команд-ботов принимает решения для своих кораблей (move/shoot/
    skip) на основе видимых врагов (сервер рассчитывает видимость через
    `get_visible_enemies`).
  * Собранные действия передаются в `process_turn`.
  * GM-бот при необходимости может сделать ``override_ship`` (например, чтобы
    воскресить корабль, который «нечестно» погиб, — тут не используется, просто
    показан механизм).

Полный лог (все решения ботов, попадания, состояние кораблей после каждого
хода) пишется в ``game_logs/game_<timestamp>.log``.

Запуск::

    python simulate_game.py

"""
from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server_full_visibility import GameServer  # noqa: E402
from shared_simple import Action, ActionType, ShipType, Team  # noqa: E402


# ---------------------------------------------------------------------------
# Утилита: буфер лога
# ---------------------------------------------------------------------------
class TranscriptLogger:
    """Накапливает строки лога и умеет их сбрасывать в файл.

    Также служит "GUI-заглушкой" для GameServer: у него ожидается метод
    ``log(message, tag)``.
    """

    def __init__(self):
        self.lines: list[str] = []

    def log(self, message: str, tag: str = "info"):
        # Приписываем тег компактно в префикс — полезно для отладки.
        prefix = {
            'info': '',
            'success': '✓ ',
            'warning': '! ',
            'error': 'x ',
            'system': '# ',
        }.get(tag, '')
        self.lines.append(f"{prefix}{message}")

    def section(self, title: str):
        self.lines.append("")
        self.lines.append("=" * 70)
        self.lines.append(title)
        self.lines.append("=" * 70)

    def h(self, title: str):
        self.lines.append("")
        self.lines.append(f"--- {title} ---")

    def p(self, line: str = ""):
        self.lines.append(line)

    def dump(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
            f.write("\n")


# ---------------------------------------------------------------------------
# Стратегия команды-бота
# ---------------------------------------------------------------------------
class TeamBot:
    """Простейшая детерминированная стратегия.

    Порядок принятия решения для каждого корабля:
      1. Если корабль видит хоть одного врага и может стрелять — стреляем в
         того, по кому реально проходит `can_shoot_at` (с учётом типа).
      2. Иначе, если корабль умеет двигаться — делаем шаг в сторону ближайшего
         видимого врага (или к центру куба, если никого не видно).
      3. Иначе (радиовышка, либо некуда идти) — пропуск хода.
    """

    def __init__(self, team: Team, rng: random.Random):
        self.team = team
        self.rng = rng
        # Память о последних видимых врагах: id -> (x, y, z, turns_since_seen).
        # Используется, в частности, артиллерией для «слепого» выстрела
        # по предполагаемым клеткам, когда никого не видно в этот ход.
        self.last_known_enemies: dict[str, tuple[int, int, int, int]] = {}

    def decide(self, server: GameServer) -> list[Action]:
        ships = server.game_state['ships']
        my_ships = [s for s in ships.values() if s.team == self.team and s.alive]
        # Видимые враги на уровне команды (радиовышка видит всю плоскость Z).
        visible = server.get_visible_enemies(self.team)
        # Преобразуем обратно из dict в объекты, чтобы оперировать позициями.
        visible_ships = [ships[sid] for sid in visible.keys() if sid in ships]

        # Обновляем память о позициях врагов: видимых — на «0 ходов назад»,
        # всем остальным запомненным — прибавляем +1 к «свежести».
        for eid in list(self.last_known_enemies.keys()):
            x, y, z, age = self.last_known_enemies[eid]
            self.last_known_enemies[eid] = (x, y, z, age + 1)
        for e in visible_ships:
            self.last_known_enemies[e.id] = (e.x, e.y, e.z, 0)
        # Удаляем «мёртвые» и слишком старые записи.
        for eid in list(self.last_known_enemies.keys()):
            s = ships.get(eid)
            if s is None or not s.alive:
                self.last_known_enemies.pop(eid, None)
                continue
            if self.last_known_enemies[eid][3] > 6:
                self.last_known_enemies.pop(eid, None)

        actions: list[Action] = []
        # Зарезервированные целевые клетки для move на этом ходу — чтобы два
        # наших корабля не пытались влезть в одну и ту же клетку (коллизии
        # всё равно отсечёт сервер, но и так красивее).
        reserved_cells: set[tuple[int, int, int]] = {(s.x, s.y, s.z) for s in my_ships}

        # Предсказанные клетки врагов на следующий ход: каждый видимый враг,
        # скорее всего, сделает 1 шаг в сторону ближайшего НАШЕГО корабля.
        predicted_enemy_cells = self._predict_enemy_next_cells(visible_ships, my_ships)

        for ship in my_ships:
            action = self._decide_ship(
                ship, visible_ships, my_ships, reserved_cells,
                predicted_enemy_cells, ships,
            )
            if action is not None:
                actions.append(action)
                if action.action_type == ActionType.MOVE:
                    reserved_cells.add(
                        (action.target_x, action.target_y, action.target_z)
                    )
        return actions

    def _predict_enemy_next_cells(
        self, enemies: list, my_ships: list
    ) -> dict[tuple[int, int, int], int]:
        """Грубый прогноз: каждый видимый враг сделает 1 шаг в сторону
        Chebyshev-ближайшего нашего корабля. Возвращает dict (x,y,z)→вес
        (сколько врагов метят в эту клетку)."""
        cells: dict[tuple[int, int, int], int] = {}
        if not enemies or not my_ships:
            return cells
        for e in enemies:
            # Радиус хода у врага — берём эффективный (jump/drill включены).
            move_r = max(
                getattr(e, 'move_range', 0),
                getattr(e, 'jump_range', 0),
                getattr(e, 'drill_range', 0),
            )
            if move_r <= 0:
                continue
            ally = min(
                my_ships,
                key=lambda a: max(
                    abs(a.x - e.x), abs(a.y - e.y), abs(a.z - e.z)
                ),
            )

            def _sgn(a, b):
                return (1 if a < b else (-1 if a > b else 0))

            dx = _sgn(e.x, ally.x)
            dy = _sgn(e.y, ally.y)
            dz = _sgn(e.z, ally.z)
            nx = max(0, min(9, e.x + dx))
            ny = max(0, min(9, e.y + dy))
            nz = max(0, min(9, e.z + dz))
            if (nx, ny, nz) == (e.x, e.y, e.z):
                continue
            cells[(nx, ny, nz)] = cells.get((nx, ny, nz), 0) + 1
        return cells

    # --- shoot helpers -------------------------------------------------------

    def _target_score(self, ship, enemy):
        """Оценка полезности врага как цели стрельбы. Выше — лучше.

        Баланс v3: «максимально эффективно» — сначала добиваем, потом бьём
        наиболее опасных, потом ближних.
          + 1000  — наш выстрел убивает врага (damage >= hp_left).
          + damage_output × 10 — более опасные враги (с высоким damage)
            в приоритете над безопасными (Радиовышка, Тишина, Провокатор,
            Паук — damage=0 → сильно проседают).
          +  (max_hits − hp_left) × 5 — уже подранены, шаг до убийства.
          −  Chebyshev-дистанция — ближних чуть предпочитаем.
        """
        hp_left = max(0, enemy.max_hits - enemy.hits)
        dmg = getattr(enemy, 'damage', 0) or 0
        dist = max(abs(enemy.x - ship.x), abs(enemy.y - ship.y), abs(enemy.z - ship.z))
        will_kill = ship.damage >= hp_left and hp_left > 0
        score = 0.0
        if will_kill:
            score += 1000
        score += dmg * 10
        score += (enemy.max_hits - hp_left) * 5
        score -= dist
        # Голограмма — всегда умирает с 1 хита, но не опасна. Добиваем
        # только если ничего лучше нет: даём низкий базовый score.
        if getattr(enemy, 'is_hologram', False):
            score -= 100
        return score

    def _pick_shoot_target(self, ship, enemies):
        """Выбирает лучшую цель по scoring-функции. Возвращает корабль или None."""
        if not ship.can_shoot or not enemies:
            return None
        reachable = [e for e in enemies if ship.can_shoot_at(e.x, e.y, e.z)]
        if not reachable:
            return None
        # Детерминированная ранжировка; ранее использовался rng.shuffle —
        # теперь приоритет задаёт scoring, а rng нужен только для tie-break.
        reachable.sort(
            key=lambda e: (self._target_score(ship, e), self.rng.random()),
            reverse=True,
        )
        return reachable[0]

    # --- move helpers --------------------------------------------------------

    def _step_toward(self, sx, sy, sz, tx, ty, tz, reserved):
        """Один шаг на 1 клетку по каждой из осей в сторону (tx, ty, tz).
        Избегает занятых клеток из `reserved`."""
        def _sgn(a, b):
            if a < b:
                return 1
            if a > b:
                return -1
            return 0

        # Попробуем «жадный» вариант + несколько запасных, чтобы обойти
        # случай «клетка прямо впереди занята союзником».
        dx0, dy0, dz0 = _sgn(sx, tx), _sgn(sy, ty), _sgn(sz, tz)
        fallback = [
            (dx0, dy0, 0), (dx0, 0, dz0), (0, dy0, dz0),
            (dx0, 0, 0), (0, dy0, 0), (0, 0, dz0),
        ]
        # Подмешиваем рандомизацию: если есть несколько равнозначных
        # шагов — сид rng выбирает, куда пойдём, чтобы разные партии ветвились.
        self.rng.shuffle(fallback)
        candidates = [(dx0, dy0, dz0), *fallback]
        for (dx, dy, dz) in candidates:
            if dx == 0 and dy == 0 and dz == 0:
                continue
            nx, ny, nz = sx + dx, sy + dy, sz + dz
            if not (0 <= nx < 10 and 0 <= ny < 10 and 0 <= nz < 10):
                continue
            if (nx, ny, nz) in reserved:
                continue
            return nx, ny, nz
        return None

    # --- blind fire helpers --------------------------------------------------
    #
    # Стартовые зоны каждой команды — опорные точки для слепого обстрела,
    # если память ещё не заполнена (первые ходы), но враг уже где-то там.
    _ENEMY_ZONES: dict[Team, tuple[tuple[int, int, int], ...]] = {
        Team.TEAM_A: ((0, 0, 0), (0, 5, 0), (0, 0, 1), (0, 5, 1)),
        Team.TEAM_B: ((9, 9, 9), (9, 4, 9), (9, 9, 8), (9, 4, 8)),
        Team.TEAM_C: ((4, 9, 4), (7, 9, 4), (4, 9, 5), (7, 9, 5)),
    }

    def _pick_blind_target(self, ship, my_ships, all_ships):
        """Выбирает клетку для «слепого» выстрела артиллерии.

        1) Берём из памяти последние замеченные координаты врагов, смещаем
           их на 1 шаг в сторону наших кораблей (враг за прошедшие ходы
           скорее всего сдвинулся). Сортируем по «свежести» — чем недавнее
           видели, тем выше приоритет. Выбираем лучший таргет, который
           ship.can_shoot_at подтверждает.
        2) Если память пустая — стреляем по стартовым зонам вражеских
           команд, выбирая ту клетку, до которой стреляющий дотягивается.
        Возвращает (x, y, z) или None, если ничего не подошло.
        """
        # Собираем центр масс наших кораблей для оценки направления движения врага.
        if my_ships:
            cx = sum(a.x for a in my_ships) / len(my_ships)
            cy = sum(a.y for a in my_ships) / len(my_ships)
            cz = sum(a.z for a in my_ships) / len(my_ships)
        else:
            cx = cy = cz = 4.5

        def _sgn_f(a, b):
            if a < b - 0.5:
                return 1
            if a > b + 0.5:
                return -1
            return 0

        candidates: list[tuple[int, int, int, int]] = []  # (age, x, y, z)
        for eid, (ex, ey, ez, age) in self.last_known_enemies.items():
            s = all_ships.get(eid) if all_ships else None
            if s is None or not s.alive:
                continue
            # Смещение на 1 шаг в сторону наших, зависит от age — чем старше
            # запись, тем больше возможный шаг.
            shift = min(age + 1, 3)
            dx = _sgn_f(ex, cx) * shift
            dy = _sgn_f(ey, cy) * shift
            dz = _sgn_f(ez, cz) * shift
            tx = max(0, min(9, ex + dx))
            ty = max(0, min(9, ey + dy))
            tz = max(0, min(9, ez + dz))
            candidates.append((age, tx, ty, tz))
            # Ещё три варианта вокруг базовой клетки — случайный разброс.
            for _ in range(2):
                jx = self.rng.randint(-1, 1)
                jy = self.rng.randint(-1, 1)
                jz = self.rng.randint(-1, 1)
                candidates.append((
                    age,
                    max(0, min(9, tx + jx)),
                    max(0, min(9, ty + jy)),
                    max(0, min(9, tz + jz)),
                ))

        # Сортируем по «свежести» (age asc).
        candidates.sort(key=lambda t: t[0])
        # Не стрелять в свою же клетку.
        my_cells = {(s.x, s.y, s.z) for s in my_ships}

        for _, tx, ty, tz in candidates:
            if (tx, ty, tz) in my_cells:
                continue
            if ship.can_shoot_at(tx, ty, tz):
                return (tx, ty, tz)

        # Fallback: стрельба по стартовым зонам других команд.
        zones: list[tuple[int, int, int]] = []
        for team, cells in self._ENEMY_ZONES.items():
            if team != self.team:
                zones.extend(cells)
        self.rng.shuffle(zones)
        for tx, ty, tz in zones:
            if (tx, ty, tz) in my_cells:
                continue
            if ship.can_shoot_at(tx, ty, tz):
                return (tx, ty, tz)
        return None

    def _decide_ship(
        self, ship, visible_ships, my_ships, reserved,
        predicted_enemy_cells=None, all_ships=None,
    ):
        predicted_enemy_cells = predicted_enemy_cells or {}
        all_ships = all_ships or {}
        # ---- Способности нестандартных кораблей (только в advanced) --------
        # Тишина: вход в фазу по реальной угрозе, выход — когда угрозы нет.
        #   • enter PHASE, если виден хотя бы один вражеский стрелок в пределах
        #     5 клеток (дальность выстрела), т.е. нас могут подстрелить в
        #     следующем ходу.
        #   • exit PHASE, если ни одного такого стрелка не видно и мы здоровы.
        # Baseline v2 фазировалась «если ранена» — это давало мало пользы:
        # корабль в фазе бесполезен (не стреляет ни во что — у Тишины damage=0,
        # но в coffee будущем если дадим — важно, чтобы её не плющили).
        if getattr(ship, 'can_phase', False):
            threatening = [
                e for e in visible_ships
                if getattr(e, 'can_shoot', False)
                and max(abs(e.x - ship.x), abs(e.y - ship.y), abs(e.z - ship.z))
                    <= getattr(e, 'shoot_range', 0)
            ]
            # Баланс v6: PHASE длится 1 ход и уходит в 3-ходовый кулдаун;
            # выходить из неё вручную нельзя (и не нужно). Активируем, когда
            # действительно угрожают, и только если кулдаун снят.
            phase_cd = getattr(ship, 'phase_cooldown', 0)
            if threatening and not ship.is_phased and phase_cd == 0:
                return Action(ship.id, ActionType.PHASE)
            # В фазе и ранены: продолжаем держаться, обычное move-поведение
            # всё равно доступно (в фазе можно ходить, нельзя только быть
            # целью выстрелов).

        # Факел: лечит, если рядом есть раненый союзник (включая себя).
        # Максимально эффективно: если в радиусе heal_range есть хотя бы один
        # раненый — лечим (AoE лечит всех сразу на 1 hp). Если раненых нет,
        # но есть раненые вне радиуса — ниже по коду Факел пойдёт к ним
        # (обрабатывается в блоке «движение к раненому»).
        if getattr(ship, 'heal_range', 0) > 0:
            wounded_in_range = [
                ally for ally in my_ships
                if ally.alive and ally.hits > 0
                and max(abs(ally.x - ship.x), abs(ally.y - ship.y), abs(ally.z - ship.z))
                    <= ship.heal_range
            ]
            if wounded_in_range:
                return Action(ship.id, ActionType.HEAL)

        # Провокатор: ставит голограмму в соседнюю клетку в сторону ближайшего
        # врага, если враг в пределах 4 клеток и такая клетка свободна.
        if getattr(ship, 'can_create_hologram', False) and visible_ships:
            nearest = min(
                visible_ships,
                key=lambda e: max(
                    abs(e.x - ship.x), abs(e.y - ship.y), abs(e.z - ship.z)
                ),
            )
            dist = max(
                abs(nearest.x - ship.x),
                abs(nearest.y - ship.y),
                abs(nearest.z - ship.z),
            )
            if 1 <= dist <= 5:
                step = self._step_toward(
                    ship.x, ship.y, ship.z, nearest.x, nearest.y, nearest.z, reserved
                )
                if step is not None:
                    tx, ty, tz = step
                    return Action(
                        ship.id, ActionType.HOLOGRAM, tx, ty, tz
                    )

        # Паук: ставит мину в клетку, куда с максимальной вероятностью
        # шагнёт вражеский корабль на следующий ход.
        # Алгоритм: перебираем 26 соседних клеток Паука (Chebyshev≤1), для
        # каждой берём «вес» из predicted_enemy_cells (сколько врагов
        # предположительно туда шагнут). Дополнительно фильтруем: клетка
        # должна быть в границах, пустой (нет нашего корабля, нет
        # существующей мины/голограммы). Выбираем клетку с максимальным
        # весом. Если ни одна не совпала с прогнозом — fallback на
        # step_toward к ближайшему видимому врагу (старая логика).
        if getattr(ship, 'can_place_mine', False):
            # Собираем множество клеток, куда ставить нельзя.
            blocked = {(s.x, s.y, s.z) for s in all_ships.values() if s.alive}
            # Свои корабли и так в reserved (из decide()) — но мины ставим
            # только на пустые клетки.
            best = None
            best_weight = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        if dx == dy == dz == 0:
                            continue
                        nx, ny, nz = ship.x + dx, ship.y + dy, ship.z + dz
                        if not (0 <= nx < 10 and 0 <= ny < 10 and 0 <= nz < 10):
                            continue
                        if (nx, ny, nz) in blocked:
                            continue
                        w = predicted_enemy_cells.get((nx, ny, nz), 0)
                        if w > best_weight:
                            best_weight = w
                            best = (nx, ny, nz)
            if best is not None:
                return Action(ship.id, ActionType.MINE, *best)
            # Fallback: ставим между нами и ближайшим видимым врагом.
            if visible_ships:
                nearest = min(
                    visible_ships,
                    key=lambda e: max(
                        abs(e.x - ship.x), abs(e.y - ship.y), abs(e.z - ship.z)
                    ),
                )
                dist = max(
                    abs(nearest.x - ship.x),
                    abs(nearest.y - ship.y),
                    abs(nearest.z - ship.z),
                )
                if 1 <= dist <= 4:
                    step = self._step_toward(
                        ship.x, ship.y, ship.z, nearest.x, nearest.y, nearest.z, reserved
                    )
                    if step is not None:
                        tx, ty, tz = step
                        return Action(
                            ship.id, ActionType.MINE, tx, ty, tz
                        )

        # Прыгун: прыжок-таран = мгновенное убийство любой цели в радиусе
        # jump_range. Максимально эффективно — выбираем самого опасного
        # (damage), затем самого живого (hp_left), чтобы не тратить таран
        # на полуживого Паука, если рядом есть полноценная Артиллерия.
        if getattr(ship, 'jump_range', 0) > 0 and visible_ships:
            jump_candidates = []
            for enemy in visible_ships:
                if getattr(enemy, 'is_phased', False):
                    continue
                if getattr(enemy, 'is_hologram', False):
                    continue
                d = max(
                    abs(enemy.x - ship.x), abs(enemy.y - ship.y), abs(enemy.z - ship.z)
                )
                if not (1 <= d <= ship.jump_range):
                    continue
                if (enemy.x, enemy.y, enemy.z) in reserved:
                    continue
                # Приоритет: damage ↓ (сильнее — важнее убрать),
                # hp_left ↓ (полноценный важнее ослабленного),
                # dist ↑ (при прочих равных — ближний, быстрее реагируем).
                score = (
                    (getattr(enemy, 'damage', 0) or 0) * 100
                    + (enemy.max_hits - enemy.hits) * 10
                    - d
                )
                jump_candidates.append((score, enemy))
            if jump_candidates:
                jump_candidates.sort(key=lambda t: t[0], reverse=True)
                best_enemy = jump_candidates[0][1]
                return Action(
                    ship.id, ActionType.MOVE, best_enemy.x, best_enemy.y, best_enemy.z
                )

        # Бурав: одноосевая прямая ИЛИ строгая диагональ по 2 осям
        # (баланс v4). Ранжируем цели по damage × hp_left, затем — ближе.
        if getattr(ship, 'drill_range', 0) > 0 and visible_ships:
            drill_candidates = []
            for enemy in visible_ships:
                if getattr(enemy, 'is_phased', False):
                    continue
                if getattr(enemy, 'is_hologram', False):
                    continue
                dx_s = abs(enemy.x - ship.x)
                dy_s = abs(enemy.y - ship.y)
                dz_s = abs(enemy.z - ship.z)
                axes = (1 if dx_s else 0) + (1 if dy_s else 0) + (1 if dz_s else 0)
                d = max(dx_s, dy_s, dz_s)
                legal = False
                if axes == 1 and 1 <= d <= ship.drill_range:
                    legal = True
                elif axes == 2:
                    nonzero = [v for v in (dx_s, dy_s, dz_s) if v]
                    if nonzero[0] == nonzero[1] and 1 <= nonzero[0] <= ship.drill_range:
                        legal = True
                if not legal:
                    continue
                if (enemy.x, enemy.y, enemy.z) in reserved:
                    continue
                score = (
                    (getattr(enemy, 'damage', 0) or 0) * 100
                    + (enemy.max_hits - enemy.hits) * 10
                    - d
                )
                drill_candidates.append((score, enemy))
            if drill_candidates:
                drill_candidates.sort(key=lambda t: t[0], reverse=True)
                best_enemy = drill_candidates[0][1]
                return Action(
                    ship.id, ActionType.MOVE, best_enemy.x, best_enemy.y, best_enemy.z
                )

        # ---- Классическая стратегия ---------------------------------------
        # 1) Стрельба
        target = self._pick_shoot_target(ship, visible_ships)
        if target is not None:
            return Action(
                ship_id=ship.id,
                action_type=ActionType.SHOOT,
                target_x=target.x, target_y=target.y, target_z=target.z,
            )

        # 1b) Артиллерия: если никого не видит — «слепой» выстрел по
        # предполагаемой клетке (shoot_anywhere=True позволяет).
        # Приоритет:
        #   a) недавно замеченные враги (last_known_enemies) + случайная
        #      компенсация их возможного шага (±1 по каждой оси, bias к
        #      нашим кораблям).
        #   b) если памяти нет — случайная клетка в стартовой зоне каждой
        #      из ДРУГИХ команд (x=9 / y=9 ...), чтобы не тратить ход.
        if (
            getattr(ship, 'shoot_anywhere', False)
            and ship.can_shoot
            and not visible_ships
        ):
            blind = self._pick_blind_target(ship, my_ships, all_ships)
            if blind is not None:
                bx, by, bz = blind
                return Action(
                    ship_id=ship.id,
                    action_type=ActionType.SHOOT,
                    target_x=bx, target_y=by, target_z=bz,
                )

        # 2) Движение (учтём расширенный радиус у Прыгуна/Бурава).
        # Для Прыгуна jump_range — это авторитетная дальность хода
        # (серверный nerf v3), поэтому берём не max, а именно её.
        if getattr(ship, 'jump_range', 0) > 0:
            effective_move = ship.jump_range
        else:
            effective_move = max(
                ship.move_range,
                getattr(ship, 'drill_range', 0),
            )
        if effective_move > 0:
            tx, ty, tz = self._pick_move_target(ship, visible_ships, my_ships, all_ships)
            step = self._step_toward(
                ship.x, ship.y, ship.z, tx, ty, tz, reserved
            )
            if step is None:
                return None
            nx, ny, nz = step
            return Action(
                ship_id=ship.id,
                action_type=ActionType.MOVE,
                target_x=nx, target_y=ny, target_z=nz,
            )

        return None

    def _pick_move_target(self, ship, visible_ships, my_ships, all_ships):
        """Выбирает точку-«якорь», к которой корабль будет двигаться.

        Максимально эффективное поведение:
          • Факел (heal_range>0): идёт к раненому союзнику вне радиуса.
            Если все здоровы — становится рядом с самым «дорогим» союзником
            (у которого max_hits больше среднего), чтобы быть готовым лечить.
          • Радиовышка (can_shoot=False, scan_whole_z): движется к центру
            «неотсканированного» пространства — Z, отличный от союзной
            Радиовышки и дальний от врагов.
          • Все остальные: идут к ближайшему видимому врагу; если никого не
            видно — к самому «свежему» last_known_enemies или в центр карты.
        """
        # --- Факел (Баланс v7): держится в ЦЕНТРЕ союзной группы ---
        # Цели:
        #   • максимизировать число союзников в heal_range=2 (AoE-хил);
        #   • не «героически» лезть вперёд к одинокому раненому на линии огня;
        #   • сохранить возможность догнать раненого, если рядом с ним ещё
        #     есть наши (т.е. группа в целом двигается, а не один изолирован).
        if getattr(ship, 'heal_range', 0) > 0:
            allies = [a for a in my_ships if a.alive and a.id != ship.id]
            if allies:
                wounded = [a for a in allies if a.hits > 0]
                if wounded:
                    # Приоритет наиболее раненого, но только если он НЕ один
                    # (рядом с ним есть хотя бы ещё 1 союзник в radius 2).
                    wounded.sort(
                        key=lambda a: -(a.hits / max(1, a.max_hits)),
                    )
                    for cand in wounded:
                        neighbors = [
                            a for a in allies if a.id != cand.id
                            and max(
                                abs(a.x - cand.x),
                                abs(a.y - cand.y),
                                abs(a.z - cand.z),
                            ) <= 2
                        ]
                        if neighbors:
                            return cand.x, cand.y, cand.z
                # Никого не лечить: центроид 3 ближайших союзников.
                allies.sort(
                    key=lambda a: max(
                        abs(a.x - ship.x),
                        abs(a.y - ship.y),
                        abs(a.z - ship.z),
                    )
                )
                cluster = allies[:3]
                cx = sum(a.x for a in cluster) // len(cluster)
                cy = sum(a.y for a in cluster) // len(cluster)
                cz = sum(a.z for a in cluster) // len(cluster)
                return cx, cy, cz

        # --- Обычное поведение: ближайший видимый враг ---
        if visible_ships:
            anchor = min(
                visible_ships,
                key=lambda e: max(
                    abs(e.x - ship.x), abs(e.y - ship.y), abs(e.z - ship.z)
                ),
            )
            return anchor.x, anchor.y, anchor.z

        # --- Никого не видим: идём к свежайшему last-known врагу ---
        if self.last_known_enemies:
            freshest = min(
                self.last_known_enemies.items(),
                key=lambda kv: kv[1][3],  # age
            )
            _, (ex, ey, ez, _) = freshest
            return ex, ey, ez

        return 5, 5, 5


# ---------------------------------------------------------------------------
# GM-бот
# ---------------------------------------------------------------------------
class GmBot:
    """Простейший GM-бот — просто просит сервер начинать каждый ход и даёт
    сигнал "end_planning" сразу после того, как боты сформировали действия.
    В будущем сюда можно добавить произвольные ``override_ship`` — сервер это
    умеет.
    """

    def start_turn(self, server: GameServer):
        server.handle_gm_command({'type': 'gm_command', 'command': 'start_turn'})

    def end_planning(self, server: GameServer):
        server.handle_gm_command({'type': 'gm_command', 'command': 'end_planning'})

    def stop(self, server: GameServer):
        server.handle_gm_command({'type': 'gm_command', 'command': 'stop'})


# ---------------------------------------------------------------------------
# Описание одного хода (для лога)
# ---------------------------------------------------------------------------
def describe_action(ship, action):
    base = f"[{ship.team.value}] {ship.name} ({ship.x},{ship.y},{ship.z})"
    at = action.action_type
    if at == ActionType.MOVE:
        return f"{base} → MOVE ({action.target_x},{action.target_y},{action.target_z})"
    if at == ActionType.SHOOT:
        return f"{base} → SHOOT at ({action.target_x},{action.target_y},{action.target_z})"
    if at == ActionType.HEAL:
        return f"{base} → HEAL (AoE)"
    if at == ActionType.PHASE:
        return f"{base} → PHASE toggle"
    if at == ActionType.HOLOGRAM:
        return f"{base} → HOLOGRAM ({action.target_x},{action.target_y},{action.target_z})"
    if at == ActionType.MINE:
        return f"{base} → MINE ({action.target_x},{action.target_y},{action.target_z})"
    return f"{base} → {at}"


def team_summary(server: GameServer) -> list[str]:
    lines = []
    ships = server.game_state['ships']
    for team in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C):
        alive = [s for s in ships.values() if s.team == team and s.alive]
        dead = [s for s in ships.values() if s.team == team and not s.alive]
        lines.append(
            f"  {team.value}: живых {len(alive)}/{len(alive)+len(dead)}"
        )
        for s in alive:
            lines.append(
                f"    ✓ {s.name:<18} @ ({s.x},{s.y},{s.z}) "
                f"hp={s.max_hits - s.hits}/{s.max_hits}"
            )
        for s in dead:
            lines.append(
                f"    ✗ {s.name:<18} @ ({s.x},{s.y},{s.z}) [уничтожен]"
            )
    return lines


# ---------------------------------------------------------------------------
# Главный цикл
# ---------------------------------------------------------------------------
def _team_stats_zero() -> dict:
    return {
        'shoot_hits': 0,
        'damage_dealt': 0,
        'kills': 0,
        'heals': 0,
        'phases': 0,
        'holograms': 0,
        'mines_placed': 0,
        'mines_triggered': 0,
        'rams': 0,
        'shoot_actions': 0,
        'move_actions': 0,
        'heal_actions': 0,
        'phase_actions': 0,
        'hologram_actions': 0,
        'mine_actions': 0,
        'kills_by_ship_type': {},
        'deaths_by_ship_type': {},
    }


def simulate(
    max_turns: int = 30,
    seed: int = 42,
    game_mode: str = 'advanced',
    write_log: bool = True,
) -> dict:
    """Проводит один матч и возвращает dict со статистикой (в т.ч. путь
    к лог-файлу под ключом ``log_path``).
    """
    rng = random.Random(seed)
    transcript = TranscriptLogger()

    server = GameServer(
        host='127.0.0.1', port=0, game_mode=game_mode, gui=transcript,
        spawn_seed=seed,
    )
    # GameServer в __init__ уже сделал create_ships и залогировал в gui.

    bots = {
        Team.TEAM_A: TeamBot(Team.TEAM_A, rng),
        Team.TEAM_B: TeamBot(Team.TEAM_B, rng),
        Team.TEAM_C: TeamBot(Team.TEAM_C, rng),
    }
    gm = GmBot()

    # Шапка.
    transcript.lines.insert(0, f"Seed: {seed}")
    transcript.lines.insert(0, f"Game mode: {game_mode}")
    transcript.lines.insert(
        0, f"Space Battle 10×10×10 — simulation run at "
           f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    transcript.lines.insert(0, "=" * 70)
    transcript.lines.append("")  # пустая строка перед турнирными ходами

    transcript.h("СТАРТОВАЯ РАССТАНОВКА")
    for line in team_summary(server):
        transcript.p(line)

    # Статистика по командам (заполняется по ходу игры).
    stats: dict[str, dict] = {
        Team.TEAM_A.value: _team_stats_zero(),
        Team.TEAM_B.value: _team_stats_zero(),
        Team.TEAM_C.value: _team_stats_zero(),
    }

    # Статистика по типам кораблей для балансировки.
    ships_ref = server.game_state['ships']
    id_to_type: dict[str, str] = {
        sid: s.ship_type.value for sid, s in ships_ref.items()
    }
    type_stats: dict[str, dict] = {}

    def _ensure_type(tp: str) -> dict:
        if tp not in type_stats:
            type_stats[tp] = {
                'deployed': 0,
                'damage_dealt': 0,
                'damage_taken': 0,
                'shots_hit': 0,
                'rams_scored': 0,
                'rams_received': 0,
                'mines_dealt': 0,          # срабатываний своих мин
                'mines_received': 0,       # срабатываний чужих мин по этому типу
                'kills': 0,
                'deaths': 0,
                'heals_given': 0,          # сколько hp восстановлено факелом
                'heals_received': 0,       # сколько hp восстановлено этому типу
                'action_move': 0,
                'action_shoot': 0,
                'action_heal': 0,
                'action_phase': 0,
                'action_hologram': 0,
                'action_mine': 0,
                'skip_turns': 0,
                'survivor_hp_sum': 0,
                'survivor_hp_max_sum': 0,
            }
        return type_stats[tp]

    for s in ships_ref.values():
        _ensure_type(s.ship_type.value)['deployed'] += 1

    # Стат для пассивок: сколько раз радиовышка каждой команды
    # сканировала реальные вражеские корабли в своём Z-слое.
    radio_scan_stats: dict[str, dict] = {
        t.value: {'scans_performed': 0, 'enemies_scanned': 0, 'unique_enemies': set()}
        for t in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C)
    }

    turn = 0
    while turn < max_turns and not server.game_state['game_over']:
        turn += 1
        transcript.section(f"ХОД {turn}")

        # 1. GM сигналит "начать ход".
        gm.start_turn(server)
        transcript.p("GM: start_turn")

        # 1b. Пассивка Радиовышки: лог, какие враги находятся в её Z-слое
        # (это клетки, которые без неё не были бы видны команде).
        ships_now = server.game_state['ships']
        transcript.h("Радиовышки: сканирование слоя Z")
        any_scan_logged = False
        for team in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C):
            radios = [
                s for s in ships_now.values()
                if s.team == team and s.alive
                and s.ship_type == ShipType.RADIO
            ]
            if not radios:
                continue
            for r in radios:
                enemies_in_z = [
                    s for s in ships_now.values()
                    if s.team != team and s.alive and s.z == r.z
                    and not getattr(s, 'is_phased', False)
                ]
                # Определяем, какие из них НЕ были бы видны без радиовышки
                # (все союзники дальше 3 клеток от них).
                team_allies = [
                    a for a in ships_now.values()
                    if a.team == team and a.alive and a.ship_type != ShipType.RADIO
                ]
                exclusive = []
                for e in enemies_in_z:
                    in_normal_vision = any(
                        max(abs(e.x - a.x), abs(e.y - a.y), abs(e.z - a.z)) <= 4
                        for a in team_allies
                    )
                    if not in_normal_vision:
                        exclusive.append(e)
                radio_scan_stats[team.value]['scans_performed'] += 1
                radio_scan_stats[team.value]['enemies_scanned'] += len(enemies_in_z)
                for e in exclusive:
                    radio_scan_stats[team.value]['unique_enemies'].add(e.id)
                if enemies_in_z:
                    summary = ", ".join(
                        f"{e.name}({e.x},{e.y},{e.z})"
                        for e in sorted(
                            enemies_in_z,
                            key=lambda e: (e.team.value, e.x, e.y, e.z)
                        )
                    )
                    tag = f"[+{len(exclusive)} эксклюзивно]" if exclusive else ""
                    transcript.p(
                        f"  {r.name} (z={r.z}): видит {len(enemies_in_z)} врагов "
                        f"{tag} → {summary}"
                    )
                    any_scan_logged = True
                else:
                    transcript.p(
                        f"  {r.name} (z={r.z}): слой чист"
                    )
                    any_scan_logged = True
        if not any_scan_logged:
            transcript.p("  (у команд не осталось живых радиовышек)")

        # 2. Каждая команда собирает действия.
        actions_by_team: dict[Team, list[Action]] = {}
        for team, bot in bots.items():
            actions_by_team[team] = bot.decide(server)

        # 3. Логируем принятые решения + считаем действия по типам.
        transcript.h("Решения ботов")
        ships = server.game_state['ships']
        for team in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C):
            tstats = stats[team.value]
            team_actions = actions_by_team[team]
            if not team_actions:
                transcript.p(f"  [{team.value}] все корабли пропускают ход")
                continue
            for action in team_actions:
                ship = ships.get(action.ship_id)
                if ship is None:
                    continue
                transcript.p(f"  {describe_action(ship, action)}")
                at = action.action_type
                tp_stats = _ensure_type(ship.ship_type.value)
                if at == ActionType.SHOOT:
                    tstats['shoot_actions'] += 1
                    tp_stats['action_shoot'] += 1
                elif at == ActionType.MOVE:
                    tstats['move_actions'] += 1
                    tp_stats['action_move'] += 1
                elif at == ActionType.HEAL:
                    tstats['heal_actions'] += 1
                    tp_stats['action_heal'] += 1
                elif at == ActionType.PHASE:
                    tstats['phase_actions'] += 1
                    tp_stats['action_phase'] += 1
                elif at == ActionType.HOLOGRAM:
                    tstats['hologram_actions'] += 1
                    tp_stats['action_hologram'] += 1
                elif at == ActionType.MINE:
                    tstats['mine_actions'] += 1
                    tp_stats['action_mine'] += 1
            passed = {s.id for s in ships.values() if s.team == team and s.alive} \
                - {a.ship_id for a in team_actions}
            for sid in passed:
                s = ships[sid]
                transcript.p(f"  [{team.value}] {s.name} → SKIP")
                _ensure_type(s.ship_type.value)['skip_turns'] += 1

        # 4. GM сигналит "end_planning".
        gm.end_planning(server)
        transcript.p("GM: end_planning")

        # 5. Загружаем actions_received и процессим ход.
        server.actions_received = actions_by_team
        turn_before_hits = len(server.game_state['hit_history'])
        # Запомним раненых до хода, чтобы посчитать heals.
        hits_before = {s.id: s.hits for s in ships.values() if s.alive}
        server.process_turn()
        # После хода: heals = раненые, у которых hits уменьшились.
        heal_amount_by_team: dict[str, int] = {}
        for sid, before in hits_before.items():
            s = ships.get(sid)
            if s is None or not s.alive:
                continue
            if s.hits < before:
                delta = before - s.hits
                stats[s.team.value]['heals'] += delta
                heal_amount_by_team[s.team.value] = heal_amount_by_team.get(s.team.value, 0) + delta
                _ensure_type(id_to_type.get(sid, s.ship_type.value))['heals_received'] += delta
        # heals_given относим к Факелам, которые сходили HEAL в этот ход.
        for team, team_actions in actions_by_team.items():
            team_heal = heal_amount_by_team.get(team.value, 0)
            torches_with_heal = [
                a for a in team_actions
                if a.action_type == ActionType.HEAL
            ]
            if team_heal and torches_with_heal:
                per_torch = team_heal / len(torches_with_heal)
                for a in torches_with_heal:
                    s = ships.get(a.ship_id)
                    if s is not None:
                        _ensure_type(s.ship_type.value)['heals_given'] += per_torch

        # 6. Разбираем новые события hit_history и обновляем статы.
        new_events = server.game_state['hit_history'][turn_before_hits:]
        transcript.h("Результаты хода")
        if not new_events:
            transcript.p("  Попаданий нет")
        else:
            for h in new_events:
                marker = "УБИТ" if h.get('killed') else "ранен"
                transcript.p(
                    f"  {h.get('attacker','?')} / {h.get('attacker_name','?'):<18} → "
                    f"{h.get('target','?')} / {h.get('target_name','?'):<18} "
                    f"@ {h.get('position','?')}  ({marker})"
                )

        for h in new_events:
            attacker = h.get('attacker')
            target = h.get('target')
            damage = h.get('damage', 0) or 0
            is_ram = bool(h.get('ram'))
            is_mine = h.get('type') == 'mine_detonated'
            killed = bool(h.get('killed'))

            # Тип атакующего/цели — берём из id→type, fallback на split по имени.
            def _extract_type(sid: str | None, name: str | None) -> str | None:
                if sid and sid in id_to_type:
                    return id_to_type[sid]
                if name:
                    return name.split()[0]
                return None

            atk_type = _extract_type(attacker, h.get('attacker_name'))
            tgt_type = _extract_type(target, h.get('target_name'))

            if is_mine:
                owner = h.get('owner')
                if owner and owner in stats:
                    stats[owner]['mines_triggered'] += 1
                    stats[owner]['damage_dealt'] += damage
                    if killed:
                        stats[owner]['kills'] += 1
                        if tgt_type:
                            stats[owner]['kills_by_ship_type'][tgt_type] = \
                                stats[owner]['kills_by_ship_type'].get(tgt_type, 0) + 1
                if target and target in stats:
                    if killed and tgt_type:
                        stats[target]['deaths_by_ship_type'][tgt_type] = \
                            stats[target]['deaths_by_ship_type'].get(tgt_type, 0) + 1
                # Per-type stats: мина — damage_dealt у Паука (владелец), damage_taken у жертвы.
                spider_type = ShipType.SPIDER.value
                sp = _ensure_type(spider_type)
                sp['mines_dealt'] += 1
                sp['damage_dealt'] += damage
                if killed:
                    sp['kills'] += 1
                if tgt_type:
                    tpv = _ensure_type(tgt_type)
                    tpv['damage_taken'] += damage
                    tpv['mines_received'] += 1
                    if killed:
                        tpv['deaths'] += 1
                continue

            if attacker and attacker in stats:
                if is_ram:
                    stats[attacker]['rams'] += 1
                else:
                    stats[attacker]['shoot_hits'] += 1
                stats[attacker]['damage_dealt'] += damage
                if killed:
                    stats[attacker]['kills'] += 1
                    if tgt_type:
                        stats[attacker]['kills_by_ship_type'][tgt_type] = \
                            stats[attacker]['kills_by_ship_type'].get(tgt_type, 0) + 1
            if target and target in stats and killed:
                if tgt_type:
                    stats[target]['deaths_by_ship_type'][tgt_type] = \
                        stats[target]['deaths_by_ship_type'].get(tgt_type, 0) + 1

            # Per-type: damage_dealt / shots_hit / rams_scored / kills для атакующего типа.
            if atk_type:
                atk_tp = _ensure_type(atk_type)
                atk_tp['damage_dealt'] += damage
                if is_ram:
                    atk_tp['rams_scored'] += 1
                else:
                    atk_tp['shots_hit'] += 1
                if killed:
                    atk_tp['kills'] += 1
            # Per-type: damage_taken / rams_received / deaths для цели.
            if tgt_type:
                tgt_tp = _ensure_type(tgt_type)
                tgt_tp['damage_taken'] += damage
                if is_ram:
                    tgt_tp['rams_received'] += 1
                if killed:
                    tgt_tp['deaths'] += 1

        # Голограммы/мины в state: посчитаем placements.
        for team_value in stats:
            stats[team_value]['phases'] = sum(
                1 for s in ships.values()
                if s.team.value == team_value and getattr(s, 'is_phased', False)
            ) + stats[team_value].get('phases_latched', 0)
        # Проще: количество «поставленных» мин/голограмм — это просто действия этого типа.
        stats[Team.TEAM_A.value]['mines_placed'] = stats[Team.TEAM_A.value]['mine_actions']
        stats[Team.TEAM_B.value]['mines_placed'] = stats[Team.TEAM_B.value]['mine_actions']
        stats[Team.TEAM_C.value]['mines_placed'] = stats[Team.TEAM_C.value]['mine_actions']
        stats[Team.TEAM_A.value]['holograms'] = stats[Team.TEAM_A.value]['hologram_actions']
        stats[Team.TEAM_B.value]['holograms'] = stats[Team.TEAM_B.value]['hologram_actions']
        stats[Team.TEAM_C.value]['holograms'] = stats[Team.TEAM_C.value]['hologram_actions']
        stats[Team.TEAM_A.value]['phases'] = stats[Team.TEAM_A.value]['phase_actions']
        stats[Team.TEAM_B.value]['phases'] = stats[Team.TEAM_B.value]['phase_actions']
        stats[Team.TEAM_C.value]['phases'] = stats[Team.TEAM_C.value]['phase_actions']

        transcript.h("Состояние команд после хода")
        for line in team_summary(server):
            transcript.p(line)

    # --- конец матча -------------------------------------------------------
    transcript.section("ИТОГ")
    if server.game_state['game_over']:
        winner = server.game_state.get('winner') or '—'
        transcript.p(f"Игра окончена. Победитель: {winner}")
        transcript.p(f"Ходов сыграно: {turn}")
    else:
        # Баланс v6: при исчерпании лимита ходов победитель — команда с
        # максимальным нанесённым уроном. Если максимум одинаковый у
        # нескольких — настоящая ничья.
        damage_by_team = {t.value: stats[t.value]['damage_dealt']
                          for t in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C)}
        max_damage = max(damage_by_team.values()) if damage_by_team else 0
        leaders = [t for t, d in damage_by_team.items() if d == max_damage]
        if max_damage > 0 and len(leaders) == 1:
            winner = leaders[0]
            transcript.p(
                f"Лимит ходов ({max_turns}) исчерпан. Победа по урону: {winner} "
                f"({max_damage} dmg)."
            )
            transcript.p(
                f"   Урон по командам: " + ", ".join(
                    f"{t}={d}" for t, d in damage_by_team.items()
                )
            )
        else:
            winner = '—'
            transcript.p(
                f"Лимит ходов ({max_turns}) исчерпан, ничья "
                f"(урон: {damage_by_team})."
            )

    # Финальные выжившие и агрегаты.
    ships = server.game_state['ships']
    survivors = {t.value: 0 for t in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C)}
    totals = {t.value: 0 for t in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C)}
    remaining_hp = {t.value: 0 for t in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C)}
    for s in ships.values():
        totals[s.team.value] += 1
        hp_left = s.max_hits - s.hits if s.alive else 0
        if s.alive:
            survivors[s.team.value] += 1
            remaining_hp[s.team.value] += hp_left
        tp = _ensure_type(s.ship_type.value)
        if s.alive:
            tp['survivor_hp_sum'] += hp_left
            tp['survivor_hp_max_sum'] += s.max_hits

    transcript.h("Сводка по командам")
    for team in (Team.TEAM_A, Team.TEAM_B, Team.TEAM_C):
        transcript.p(
            f"  {team.value}: {survivors[team.value]}/{totals[team.value]} живых; "
            f"hp={remaining_hp[team.value]}"
        )

    transcript.h("Сводка по пассивкам радиовышек")
    for team_value, rs in radio_scan_stats.items():
        unique_count = len(rs['unique_enemies'])
        transcript.p(
            f"  {team_value}: сканирований={rs['scans_performed']}, "
            f"всего врагов-в-слоях={rs['enemies_scanned']}, "
            f"уникальных эксклюзивных (видны только радиовышкой)={unique_count}"
        )

    transcript.h("Сводка по попаданиям")
    history = server.game_state['hit_history']
    kills = sum(1 for h in history if h.get('killed'))
    transcript.p(f"  Всего событий (шоты+тараны+мины): {len(history)}")
    transcript.p(f"  Уничтожено кораблей всего: {kills}")
    for team_name in (Team.TEAM_A.value, Team.TEAM_B.value, Team.TEAM_C.value):
        ts = stats[team_name]
        transcript.p(
            f"  {team_name}: урон={ts['damage_dealt']} хиты={ts['shoot_hits']} "
            f"тараны={ts['rams']} мины(сработало)={ts['mines_triggered']} "
            f"убийств={ts['kills']}"
        )

    # GM-бот завершает игру.
    gm.stop(server)

    # --- дамп ----------------------------------------------------------------
    out_path = None
    if write_log:
        ts_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = os.path.join(
            ROOT, "game_logs", f"game_{ts_stamp}_{game_mode}_seed{seed}.log"
        )
        transcript.dump(out_path)

    # Закрыть сокет, чтобы не оставался открытый файловый дескриптор.
    try:
        server.server.close()
    except Exception:
        pass

    total_damage = sum(stats[t]['damage_dealt'] for t in stats)
    radio_stats_out = {
        tv: {
            'scans_performed': rs['scans_performed'],
            'enemies_scanned': rs['enemies_scanned'],
            'unique_exclusive': len(rs['unique_enemies']),
        }
        for tv, rs in radio_scan_stats.items()
    }
    return {
        'seed': seed,
        'mode': game_mode,
        'turns': turn,
        'winner': winner,
        'survivors': survivors,
        'totals': totals,
        'remaining_hp': remaining_hp,
        'stats': stats,
        'type_stats': type_stats,
        'radio_stats': radio_stats_out,
        'total_damage': total_damage,
        'avg_damage_per_turn': round(total_damage / turn, 2) if turn else 0,
        'log_path': out_path,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Headless Space Battle simulation")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed (для детерминированности)")
    parser.add_argument("--max-turns", type=int, default=30,
                        help="Максимум ходов до объявления ничьей")
    parser.add_argument("--mode", choices=("advanced", "basic"), default="advanced",
                        help="Режим игры: advanced (разные типы) или basic (только крейсеры)")
    args = parser.parse_args()

    path = simulate(
        max_turns=args.max_turns,
        seed=args.seed,
        game_mode=args.mode,
    )
    print(f"Simulation complete. Log written to:\n  {path}")
