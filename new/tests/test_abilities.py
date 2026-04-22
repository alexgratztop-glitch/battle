"""Тесты способностей новых типов кораблей (advanced-режим).

Прыгун, Бурав, Факел, Тишина, Провокатор, Паук — каждый проверяется
отдельным классом: легальное использование + ключевые ограничения.
"""
import pytest

from shared_simple import Ship, ShipType, Team, Action, ActionType
from server_full_visibility import GameServer


@pytest.fixture
def server():
    s = GameServer(host="127.0.0.1", port=0, game_mode="advanced", gui=None)
    yield s
    try:
        s.server.close()
    except Exception:
        pass


def _set_ships(server, ships_list):
    server.game_state['ships'] = {s.id: s for s in ships_list}


def _ship(sid, team, x, y, z, ship_type=ShipType.BASE):
    return Ship(sid, f"{ship_type.value} {sid}", team, x, y, z, ship_type)


# ==========================================================================
# Прыгун
# ==========================================================================

class TestJumper:
    def test_jumps_2_cells_through_ships(self, server):
        """Баланс v3: Прыгун прыгает на 2 клетки сквозь корабли на пути."""
        jumper = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.JUMPER)
        blocker = _ship("A2", Team.TEAM_A, 1, 0, 0, ShipType.BASE)  # свой на пути
        _set_ships(server, [jumper, blocker])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (jumper.x, jumper.y, jumper.z) == (2, 0, 0)
        assert blocker.alive, "Свой корабль на пути не должен пострадать"

    def test_jumper_rams_enemy_at_destination(self, server):
        """Прыгун приземляется на вражескую клетку — враг мгновенно погибает."""
        jumper = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.JUMPER)
        enemy = _ship("B1", Team.TEAM_B, 2, 0, 0, ShipType.ARTILLERY)
        _set_ships(server, [jumper, enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert not enemy.alive, "Враг на посадочной клетке должен быть уничтожен тараном"
        assert (jumper.x, jumper.y, jumper.z) == (2, 0, 0)
        # Таран должен попасть в hit_history
        ram_events = [h for h in server.game_state['hit_history'] if h.get('ram')]
        assert ram_events, "Таран Прыгуна должен записаться в hit_history"

    def test_jumper_cannot_land_on_ally(self, server):
        """Прыгун не может приземлиться на своего — движение отклонено."""
        jumper = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.JUMPER)
        ally = _ship("A2", Team.TEAM_A, 2, 0, 0, ShipType.BASE)
        _set_ships(server, [jumper, ally])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (jumper.x, jumper.y, jumper.z) == (0, 0, 0), "Ход должен быть отклонён"
        assert ally.alive

    def test_jumper_range_3_rejected_if_blocker(self, server):
        """Баланс v3: jump_range=2 — прыжок сквозь корабли ограничен 2 клетками.
        Обычный move=3 (без препятствий) остаётся разрешён: jump_range режет
        только прыжок сквозь preграды."""
        jumper = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.JUMPER)
        blocker = _ship("A2", Team.TEAM_A, 1, 0, 0, ShipType.BASE)
        _set_ships(server, [jumper, blocker])
        # Прыжок через союзника на дистанцию 3 — превышает jump_range=2.
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 3, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (jumper.x, jumper.y, jumper.z) == (0, 0, 0)


# ==========================================================================
# Бурав
# ==========================================================================

class TestDrill:
    def test_drills_straight_line_through_ships(self, server):
        """Бурав проходит 3 клетки по прямой сквозь любые корабли."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        middle_enemy = _ship("B1", Team.TEAM_B, 1, 0, 0, ShipType.CRUISER)
        middle_ally = _ship("A2", Team.TEAM_A, 2, 0, 0, ShipType.BASE)
        _set_ships(server, [drill, middle_enemy, middle_ally])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 3, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (drill.x, drill.y, drill.z) == (3, 0, 0)
        # Промежуточные корабли не тронуты
        assert middle_enemy.alive
        assert middle_ally.alive

    def test_drill_kills_ship_at_end_cell(self, server):
        """В конечной клетке враг уничтожается."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        enemy = _ship("B1", Team.TEAM_B, 3, 0, 0, ShipType.ARTILLERY)
        _set_ships(server, [drill, enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 3, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert not enemy.alive, "Бурав уничтожает корабль в конечной клетке"
        assert (drill.x, drill.y, drill.z) == (3, 0, 0)

    def test_drill_diagonal_2d_allowed(self, server):
        """Баланс v4: Бурав умеет 2D-диагональ (равные смещения по 2 осям)."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        enemy = _ship("B1", Team.TEAM_B, 2, 2, 0, ShipType.ARTILLERY)
        _set_ships(server, [drill, enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 2, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (drill.x, drill.y, drill.z) == (2, 2, 0)
        assert not enemy.alive, "Бурав убивает врага в конечной диагональной клетке"

    def test_drill_rejects_unequal_2axis(self, server):
        """Неравная «диагональ» (2-0-1 или 3-0-1) — отклоняется."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        _set_ships(server, [drill])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 1, 0)],  # dx=2, dy=1
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (drill.x, drill.y, drill.z) == (0, 0, 0)

    def test_drill_rejects_3axis(self, server):
        """3D-диагональ всё ещё запрещена (слишком сильная атака)."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        _set_ships(server, [drill])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 2, 2)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (drill.x, drill.y, drill.z) == (0, 0, 0)


# ==========================================================================
# Факел
# ==========================================================================

class TestTorch:
    def test_heal_restores_hp_to_allies_in_range(self, server):
        """Факел лечит всех раненых союзников в радиусе 1."""
        torch = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.TORCH)
        wounded = _ship("A2", Team.TEAM_A, 5, 6, 5, ShipType.ARTILLERY)  # hp 3
        wounded.hits = 2
        far_wounded = _ship("A3", Team.TEAM_A, 9, 9, 9, ShipType.BASE)
        far_wounded.hits = 1
        _set_ships(server, [torch, wounded, far_wounded])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HEAL)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert wounded.hits == 1, "Союзник в радиусе должен восстановить 1 hp"
        assert far_wounded.hits == 1, "Союзник вне радиуса не должен лечиться"

    def test_heal_doesnt_revive_dead(self, server):
        """Мертвых воскрешать нельзя."""
        torch = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.TORCH)
        dead = _ship("A2", Team.TEAM_A, 5, 6, 5, ShipType.CRUISER)
        dead.alive = False
        dead.hits = dead.max_hits
        _set_ships(server, [torch, dead])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HEAL)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert not dead.alive
        assert dead.hits == dead.max_hits

    def test_heal_doesnt_touch_enemies(self, server):
        """Врагов не лечим."""
        torch = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.TORCH)
        enemy = _ship("B1", Team.TEAM_B, 5, 6, 5, ShipType.BASE)
        enemy.hits = 1
        _set_ships(server, [torch, enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HEAL)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert enemy.hits == 1


# ==========================================================================
# Тишина
# ==========================================================================

class TestSilence:
    def test_phase_makes_ship_invulnerable_to_shots(self, server):
        """В фазе корабль не получает урона."""
        silence = _ship("A1", Team.TEAM_A, 3, 0, 0, ShipType.SILENCE)
        shooter = _ship("B1", Team.TEAM_B, 0, 0, 0, ShipType.CRUISER)
        _set_ships(server, [silence, shooter])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.PHASE)],
            Team.TEAM_B: [Action("B1", ActionType.SHOOT, 3, 0, 0)],
            Team.TEAM_C: [],
        }
        server.process_turn()
        assert silence.is_phased is True
        assert silence.hits == 0, "В фазе урон не проходит"
        assert silence.alive

    def test_phase_ship_invisible_to_enemies(self, server):
        silence = _ship("A1", Team.TEAM_A, 3, 0, 0, ShipType.SILENCE)
        silence.is_phased = True
        scout = _ship("B1", Team.TEAM_B, 4, 0, 0, ShipType.CRUISER)  # вплотную
        _set_ships(server, [silence, scout])
        visible_for_b = server.get_visible_enemies(Team.TEAM_B)
        assert "A1" not in visible_for_b, "Фазированный не должен быть в visible_enemies"

    def test_phase_auto_expires_after_one_turn(self, server):
        """Баланс v6: фаза длится ровно 1 ход, потом авто-выход."""
        silence = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.SILENCE)
        _set_ships(server, [silence])
        # Ход 1: активируем фазу.
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.PHASE)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert silence.is_phased is True
        assert silence.phase_cooldown == 3
        # Ход 2: фаза должна спасть автоматически.
        server.actions_received = {
            Team.TEAM_A: [], Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert silence.is_phased is False
        assert silence.phase_cooldown == 2

    def test_phase_cooldown_blocks_reactivation(self, server):
        """Баланс v6: фазу нельзя активировать пока кулдаун > 0."""
        silence = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.SILENCE)
        _set_ships(server, [silence])
        # Ход 1: фаза.
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.PHASE)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        # Ходы 2–3: пытаемся снова войти в фазу до снятия кулдауна.
        for _ in range(2):
            server.actions_received = {
                Team.TEAM_A: [Action("A1", ActionType.PHASE)],
                Team.TEAM_B: [], Team.TEAM_C: [],
            }
            server.process_turn()
            assert silence.is_phased is False, "Кулдаун запрещает фазу"
        # Ход 4: кулдаун обнулился, можно снова фазиться.
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.PHASE)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert silence.is_phased is True

    def test_phased_ship_does_not_block_enemy_movement(self, server):
        """Фаза = прозрачность: вражеский корабль должен свободно входить
        в клетку, где стоит корабль в фазе (согласованно с _resolve_shot
        и get_visible_enemies)."""
        silence = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.SILENCE)
        enemy = _ship("B1", Team.TEAM_B, 5, 4, 5, ShipType.CRUISER)
        _set_ships(server, [silence, enemy])
        # Баланс v6: фаза активируется через PHASE-действие в этом же ходу.
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.PHASE)],
            Team.TEAM_B: [Action("B1", ActionType.MOVE, 5, 5, 5)],
            Team.TEAM_C: [],
        }
        server.process_turn()
        # Враг вошёл в клетку фазированного корабля.
        assert (enemy.x, enemy.y, enemy.z) == (5, 5, 5)
        # Оба живы (фазированный не пострадал, враг не остановился).
        assert silence.alive is True
        assert enemy.alive is True

    def test_jumper_ram_pierces_phase(self, server):
        """Баланс v6: таран Прыгуна пробивает фазу и убивает врага."""
        jumper = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.JUMPER)
        phased_enemy = _ship("B1", Team.TEAM_B, 2, 0, 0, ShipType.SILENCE)
        phased_enemy.is_phased = True
        _set_ships(server, [jumper, phased_enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 2, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (jumper.x, jumper.y, jumper.z) == (2, 0, 0)
        assert phased_enemy.alive is False, "Таран пробивает фазу"

    def test_drill_ram_pierces_phase(self, server):
        """Баланс v6: таран Бурава тоже пробивает фазу."""
        drill = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.DRILL)
        phased_enemy = _ship("B1", Team.TEAM_B, 3, 0, 0, ShipType.SILENCE)
        phased_enemy.is_phased = True
        _set_ships(server, [drill, phased_enemy])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 3, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert (drill.x, drill.y, drill.z) == (3, 0, 0)
        assert phased_enemy.alive is False


# ==========================================================================
# Провокатор
# ==========================================================================

class TestProvocateur:
    def test_spawns_hologram_in_adjacent_cell(self, server):
        prov = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.PROVOCATEUR)
        _set_ships(server, [prov])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HOLOGRAM, 5, 6, 5)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        holos = server.game_state['holograms']
        assert len(holos) == 1
        holo = list(holos.values())[0]
        assert (holo['x'], holo['y'], holo['z']) == (5, 6, 5)
        assert holo['owner_team'] == Team.TEAM_A.value

    def test_hologram_far_cell_rejected(self, server):
        prov = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.PROVOCATEUR)
        _set_ships(server, [prov])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HOLOGRAM, 3, 0, 0)],  # dist=3
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert len(server.game_state['holograms']) == 0

    def test_hologram_destroyed_by_single_shot(self, server):
        prov = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.PROVOCATEUR)
        enemy = _ship("B1", Team.TEAM_B, 4, 0, 0, ShipType.CRUISER)
        _set_ships(server, [prov, enemy])
        # Ход 1: создать голограмму
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.HOLOGRAM, 1, 0, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert len(server.game_state['holograms']) == 1
        holo_id = list(server.game_state['holograms'].keys())[0]
        # Ход 2: враг стреляет по голограмме
        server.actions_received = {
            Team.TEAM_A: [],
            Team.TEAM_B: [Action("B1", ActionType.SHOOT, 1, 0, 0)],
            Team.TEAM_C: [],
        }
        server.process_turn()
        assert server.game_state['holograms'][holo_id]['alive'] is False
        # Провокатор (настоящий корабль) не получил урона — голограмма
        # заблокировала выстрел.
        assert prov.alive and prov.hits == 0


# ==========================================================================
# Паук
# ==========================================================================

class TestSpider:
    def test_places_mine_in_adjacent_cell(self, server):
        spider = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.SPIDER)
        _set_ships(server, [spider])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MINE, 5, 6, 5)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        mines = server.game_state['mines']
        assert len(mines) == 1
        assert (mines[0]['x'], mines[0]['y'], mines[0]['z']) == (5, 6, 5)
        assert mines[0]['damage'] == 2

    def test_mine_triggers_on_enemy_move(self, server):
        spider = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.SPIDER)
        enemy = _ship("B1", Team.TEAM_B, 2, 1, 0, ShipType.ARTILLERY)  # hp 3
        _set_ships(server, [spider, enemy])
        # Ход 1: поставить мину в (1,1,0) — соседняя клетка Паука
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MINE, 1, 1, 0)],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert len(server.game_state['mines']) == 1
        # Ход 2: вражеская артиллерия переезжает... artillery не двигается.
        # Возьмём Base вместо artillery.
        enemy.move_range = 2  # форсируем
        server.actions_received = {
            Team.TEAM_A: [],
            Team.TEAM_B: [Action("B1", ActionType.MOVE, 1, 1, 0)],
            Team.TEAM_C: [],
        }
        server.process_turn()
        assert len(server.game_state['mines']) == 0, "Мина должна сдетонировать и исчезнуть"
        assert enemy.hits == 2

    def test_mine_does_not_trigger_for_own_team(self, server):
        spider = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.SPIDER)
        ally = _ship("A2", Team.TEAM_A, 2, 1, 0, ShipType.BASE)
        _set_ships(server, [spider, ally])
        server.actions_received = {
            Team.TEAM_A: [
                Action("A1", ActionType.MINE, 1, 1, 0),
                Action("A2", ActionType.MOVE, 1, 1, 0),
            ],
            Team.TEAM_B: [], Team.TEAM_C: [],
        }
        server.process_turn()
        assert len(server.game_state['mines']) == 1, "Своя мина не срабатывает"
        assert ally.hits == 0
