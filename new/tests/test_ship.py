"""Проверка модели корабля и сериализации действий."""
import pytest

from shared_simple import Ship, ShipType, Team, Action, ActionType


def make(team=Team.TEAM_A, x=0, y=0, z=0, ship_type=ShipType.BASE, sid="S1"):
    return Ship(sid, f"{ship_type.value} {sid}", team, x, y, z, ship_type)


class TestShipDefaults:
    def test_base_defaults(self):
        s = make(ship_type=ShipType.BASE)
        assert s.max_hits == 2
        assert s.move_range == 2
        assert s.shoot_range == 5
        assert s.can_shoot is True
        assert s.shoot_anywhere is False
        assert s.scan_whole_z is False
        assert s.hits == 0
        assert s.alive is True

    def test_cruiser(self):
        s = make(ship_type=ShipType.CRUISER)
        # Баланс v2: hp 1→2.
        assert s.max_hits == 2
        assert s.move_range == 2
        assert s.shoot_range == 5
        assert s.can_shoot is True
        assert s.shoot_anywhere is False

    def test_artillery(self):
        s = make(ship_type=ShipType.ARTILLERY)
        # Баланс v2: hp 3→1 (glass cannon).
        assert s.max_hits == 1
        assert s.move_range == 0
        assert s.shoot_range == 10
        assert s.shoot_anywhere is True

    def test_radio(self):
        s = make(ship_type=ShipType.RADIO)
        # По ТЗ радиовышка получила hp=3.
        assert s.max_hits == 3
        assert s.can_shoot is False
        assert s.shoot_range == 0
        assert s.scan_whole_z is True
        # Не должно быть AttributeError при доступе к shoot_anywhere
        assert s.shoot_anywhere is False


class TestNewShipTypes:
    """Статы новых типов по ТЗ (способности пока без логики)."""

    def test_artillery_shoot_anywhere(self):
        s = make(ship_type=ShipType.ARTILLERY)
        assert s.shoot_anywhere is True
        assert s.can_shoot is True

    def test_jumper(self):
        s = make(ship_type=ShipType.JUMPER)
        assert s.max_hits == 2
        # Баланс v7 / Devin Review #4: move_range синхронизирован с
        # jump_range (оба = 2), чтобы не было мёртвого кода.
        assert s.move_range == 2
        assert s.jump_range == 2
        assert s.damage == 1
        assert s.can_shoot is True

    def test_torch(self):
        s = make(ship_type=ShipType.TORCH)
        # Баланс v2: hp 5→6.
        assert s.max_hits == 6
        assert s.move_range == 2
        assert s.heal_range == 2
        assert s.damage == 1

    def test_silence(self):
        s = make(ship_type=ShipType.SILENCE)
        assert s.max_hits == 2
        assert s.move_range == 2
        assert s.can_phase is True
        assert s.can_shoot is False
        assert s.damage == 0

    def test_drill(self):
        s = make(ship_type=ShipType.DRILL)
        assert s.max_hits == 4
        assert s.move_range == 3
        assert s.drill_range == 3
        assert s.can_shoot is False
        assert s.damage == 0

    def test_provocateur(self):
        s = make(ship_type=ShipType.PROVOCATEUR)
        assert s.max_hits == 2
        assert s.move_range == 2
        assert s.can_create_hologram is True
        assert s.damage == 1

    def test_spider(self):
        s = make(ship_type=ShipType.SPIDER)
        assert s.max_hits == 1
        assert s.move_range == 2
        assert s.can_place_mine is True
        assert s.mine_damage == 2
        assert s.can_shoot is False


class TestToDictNewFields:
    def test_to_dict_exposes_new_stats(self):
        s = make(ship_type=ShipType.JUMPER)
        d = s.to_dict()
        for key in (
            'damage', 'jump_range', 'heal_range', 'can_phase',
            'drill_range', 'can_create_hologram', 'can_place_mine',
            'mine_damage',
        ):
            assert key in d, f"{key} должен быть в to_dict()"


class TestMove:
    def test_move_one_cell_ok(self):
        s = make(x=5, y=5, z=5)
        assert s.move(6, 5, 5) is True
        assert (s.x, s.y, s.z) == (6, 5, 5)

    def test_move_over_range_rejected(self):
        # Баланс v5: Базовый имеет move_range=2, поэтому >2 отклоняется.
        s = make(x=5, y=5, z=5)
        assert s.move(8, 5, 5) is False
        assert (s.x, s.y, s.z) == (5, 5, 5)

    def test_move_out_of_bounds(self):
        s = make(x=0, y=0, z=0)
        assert s.move(-1, 0, 0) is False
        assert s.move(0, 10, 0) is False
        assert (s.x, s.y, s.z) == (0, 0, 0)

    def test_artillery_cannot_move(self):
        s = make(ship_type=ShipType.ARTILLERY)
        assert s.move(1, 0, 0) is False


class TestCanShootAt:
    def test_straight_line_in_range(self):
        s = make(x=5, y=5, z=5)
        assert s.can_shoot_at(7, 5, 5) is True  # по оси X, дистанция 2
        assert s.can_shoot_at(5, 5, 0) is True  # по оси Z, дистанция 5

    def test_straight_line_out_of_range(self):
        s = make(x=0, y=0, z=0)
        assert s.can_shoot_at(6, 0, 0) is False  # дальность 5

    def test_not_straight_line(self):
        s = make(x=5, y=5, z=5)
        assert s.can_shoot_at(7, 7, 5) is False  # сразу 2 оси

    def test_artillery_anywhere(self):
        s = make(ship_type=ShipType.ARTILLERY, x=0, y=0, z=0)
        assert s.can_shoot_at(9, 9, 9) is True
        assert s.can_shoot_at(5, 5, 5) is True

    def test_radio_cannot_shoot(self):
        s = make(ship_type=ShipType.RADIO, x=0, y=0, z=0)
        assert s.can_shoot_at(0, 0, 1) is False


class TestHits:
    def test_take_hit_reduces_alive(self):
        # Крейсер теперь hp=2 (баланс v2): одного попадания недостаточно.
        s = make(ship_type=ShipType.CRUISER)
        assert s.alive
        s.take_hit()
        assert s.alive
        s.take_hit()
        assert not s.alive

    def test_base_takes_two_hits(self):
        s = make(ship_type=ShipType.BASE)
        s.take_hit()
        assert s.alive
        s.take_hit()
        assert not s.alive

    def test_artillery_takes_one_hit(self):
        # Баланс v2: артиллерия hp=1, умирает с одного попадания.
        s = make(ship_type=ShipType.ARTILLERY)
        assert s.alive
        s.take_hit()
        assert not s.alive

    def test_dead_ship_cannot_shoot(self):
        s = make()
        s.take_hit()
        s.take_hit()
        assert not s.alive
        assert s.can_shoot_at(s.x + 1, s.y, s.z) is False


class TestAction:
    def test_roundtrip(self):
        a = Action("A_1", ActionType.MOVE, 1, 2, 3)
        d = a.to_dict()
        b = Action.from_dict(d)
        assert b.ship_id == "A_1"
        assert b.action_type == ActionType.MOVE
        assert (b.target_x, b.target_y, b.target_z) == (1, 2, 3)

    def test_shoot_roundtrip(self):
        a = Action("X", ActionType.SHOOT, 0, 0, 0)
        assert Action.from_dict(a.to_dict()).action_type == ActionType.SHOOT

    def test_bad_action_type_raises(self):
        with pytest.raises(ValueError):
            Action.from_dict({
                'ship_id': 'X',
                'action_type': 'laser',
                'target_x': 0, 'target_y': 0, 'target_z': 0,
            })
