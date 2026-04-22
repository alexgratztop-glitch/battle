"""Интеграционные тесты игровой логики сервера (process_turn).

Мы НЕ поднимаем TCP-сокеты: просто создаём GameServer без GUI, подменяем
ships/actions_received и вызываем process_turn напрямую.
"""
import pytest

from shared_simple import Ship, ShipType, Team, Action, ActionType
from server_full_visibility import GameServer


@pytest.fixture
def server():
    s = GameServer(host="127.0.0.1", port=0, game_mode="advanced", gui=None)
    # Сокет создан в __init__, но не биндится — просто закроем его в teardown,
    # чтобы не держать файловый дескриптор.
    yield s
    try:
        s.server.close()
    except Exception:
        pass


def _set_ships(server, ships_list):
    server.game_state['ships'] = {s.id: s for s in ships_list}


def _ship(sid, team, x, y, z, ship_type=ShipType.BASE):
    return Ship(sid, f"{ship_type.value} {sid}", team, x, y, z, ship_type)


class TestSimultaneousCombat:
    """Главный багфикс: обе стороны должны успеть выстрелить в один ход,
    даже если одна из них погибает от пули противника в этом же ходу."""

    def test_mutual_kill(self, server):
        # Два крейсера (1 HP) стоят в одной линии и одновременно стреляют
        # друг в друга. Оба должны умереть в один ход.
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.CRUISER)
        b = _ship("B1", Team.TEAM_B, 3, 0, 0, ShipType.CRUISER)
        _set_ships(server, [a, b])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 3, 0, 0)],
            Team.TEAM_B: [Action("B1", ActionType.SHOOT, 0, 0, 0)],
        }
        server.process_turn()
        assert not a.alive, "A1 должен погибнуть от выстрела B1"
        assert not b.alive, "B1 должен погибнуть от выстрела A1 (симультанно)"
        # В логе хитов должны быть обе записи
        assert len(server.game_state['last_hits']) == 2


class TestFriendlyLineBlock:
    """Багфикс: пуля блокируется первым кораблём на линии — своим или чужим."""

    def test_ally_blocks_shot(self, server):
        # Атакующий в (0,0,0) стреляет по оси X. Союзник в (1,0,0) стоит на пути.
        # Враг в (3,0,0) должен остаться невредим.
        attacker = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        ally = _ship("A2", Team.TEAM_A, 1, 0, 0, ShipType.BASE)
        enemy = _ship("B1", Team.TEAM_B, 3, 0, 0, ShipType.BASE)
        _set_ships(server, [attacker, ally, enemy])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 3, 0, 0)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        assert enemy.hits == 0
        assert ally.hits == 0, "Свой корабль не должен получать урон"

    def test_enemy_blocks_shot(self, server):
        # Вражеский корабль стоит ближе цели — попасть должны в него, не в заднего.
        attacker = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        near_enemy = _ship("B1", Team.TEAM_B, 2, 0, 0, ShipType.BASE)
        far_enemy = _ship("B2", Team.TEAM_B, 4, 0, 0, ShipType.BASE)
        _set_ships(server, [attacker, near_enemy, far_enemy])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 4, 0, 0)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        assert near_enemy.hits == 1
        assert far_enemy.hits == 0


class TestArtilleryPointStrike:
    def test_hits_any_team_in_cell(self, server):
        attacker = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.ARTILLERY)
        target = _ship("B1", Team.TEAM_B, 5, 5, 5, ShipType.BASE)
        _set_ships(server, [attacker, target])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 5, 5, 5)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        # Артиллерия по ТЗ наносит 2 ед. урона.
        assert target.hits == 2

    def test_artillery_not_blocked_by_ally_between(self, server):
        # Артиллерия бьёт по координате — союзник между атакующим и целью не блокирует.
        attacker = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.ARTILLERY)
        ally_middle = _ship("A2", Team.TEAM_A, 3, 3, 3, ShipType.BASE)
        target = _ship("B1", Team.TEAM_B, 7, 7, 7, ShipType.BASE)
        _set_ships(server, [attacker, ally_middle, target])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 7, 7, 7)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        assert target.hits == 2
        assert ally_middle.hits == 0


class TestMoveCollision:
    """Багфикс: нельзя переместиться в занятую клетку."""

    def test_move_into_alive_ship_rejected(self, server):
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        b = _ship("B1", Team.TEAM_B, 1, 0, 0, ShipType.BASE)
        _set_ships(server, [a, b])

        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.MOVE, 1, 0, 0)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        assert (a.x, a.y, a.z) == (0, 0, 0), "A1 не должен занять клетку B1"

    def test_move_into_empty_cell_ok(self, server):
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        _set_ships(server, [a])
        server.actions_received = {Team.TEAM_A: [Action("A1", ActionType.MOVE, 1, 0, 0)]}
        server.process_turn()
        assert (a.x, a.y, a.z) == (1, 0, 0)

    def test_move_into_dead_ship_ok(self, server):
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        dead = _ship("B1", Team.TEAM_B, 1, 0, 0, ShipType.BASE)
        dead.alive = False
        _set_ships(server, [a, dead])
        server.actions_received = {Team.TEAM_A: [Action("A1", ActionType.MOVE, 1, 0, 0)]}
        server.process_turn()
        assert (a.x, a.y, a.z) == (1, 0, 0)


class TestWinCondition:
    def test_last_team_standing_wins(self, server):
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.CRUISER)
        b = _ship("B1", Team.TEAM_B, 3, 0, 0, ShipType.CRUISER)
        c = _ship("C1", Team.TEAM_C, 0, 3, 0, ShipType.CRUISER)
        # B и C уже мертвы
        b.alive = False
        c.alive = False
        _set_ships(server, [a, b, c])

        server.actions_received = {Team.TEAM_A: []}
        server.process_turn()
        assert server.game_state['game_over'] is True
        assert server.game_state['winner'] == Team.TEAM_A.value


class TestShipTypeRuleEnforcement:
    def test_radio_shoot_rejected(self, server):
        # Радиовышка не стреляет — даже если клиент прислал SHOOT, сервер отклоняет.
        radio = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.RADIO)
        target = _ship("B1", Team.TEAM_B, 1, 0, 0, ShipType.BASE)
        _set_ships(server, [radio, target])
        server.actions_received = {
            Team.TEAM_A: [Action("A1", ActionType.SHOOT, 1, 0, 0)],
            Team.TEAM_B: [],
        }
        server.process_turn()
        assert target.hits == 0

    def test_artillery_move_rejected(self, server):
        art = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.ARTILLERY)
        _set_ships(server, [art])
        server.actions_received = {Team.TEAM_A: [Action("A1", ActionType.MOVE, 6, 5, 5)]}
        server.process_turn()
        assert (art.x, art.y, art.z) == (5, 5, 5)

    def test_cannot_shoot_other_team_ship(self, server):
        # Игрок не может отдавать приказ чужому кораблю.
        a = _ship("A1", Team.TEAM_A, 0, 0, 0, ShipType.BASE)
        b = _ship("B1", Team.TEAM_B, 1, 0, 0, ShipType.BASE)
        _set_ships(server, [a, b])
        # Team A шлёт команду на корабль B1
        server.actions_received = {
            Team.TEAM_A: [Action("B1", ActionType.SHOOT, 0, 0, 0)],
        }
        server.process_turn()
        assert a.hits == 0


class TestVisibility:
    def test_radio_sees_whole_z_plane(self, server):
        # Радиовышка A на Z=4 должна видеть противника на Z=4 вне радиуса 3.
        radio = _ship("A1", Team.TEAM_A, 0, 0, 4, ShipType.RADIO)
        enemy_far_same_z = _ship("B1", Team.TEAM_B, 9, 9, 4, ShipType.BASE)
        enemy_other_z = _ship("B2", Team.TEAM_B, 9, 9, 5, ShipType.BASE)
        _set_ships(server, [radio, enemy_far_same_z, enemy_other_z])

        visible = server.get_visible_enemies(Team.TEAM_A)
        assert "B1" in visible
        assert "B2" not in visible

    def test_regular_visibility_radius_4(self, server):
        """Баланс v4: радиус видимости 3→4."""
        ally = _ship("A1", Team.TEAM_A, 5, 5, 5, ShipType.BASE)
        near = _ship("B1", Team.TEAM_B, 9, 5, 5, ShipType.BASE)  # дистанция 4 — видно
        far = _ship("B2", Team.TEAM_B, 0, 5, 5, ShipType.BASE)  # дистанция 5 — не видно
        _set_ships(server, [ally, near, far])
        visible = server.get_visible_enemies(Team.TEAM_A)
        assert "B1" in visible
        assert "B2" not in visible
