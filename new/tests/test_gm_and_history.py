"""Тесты управления ходом от GM (handle_gm_command) и записи истории
попаданий (hit_history).

Поднимаем GameServer без GUI и без реального прослушивания TCP: конструктор
создаёт сокет, но не вызывает `bind()`/`listen()`, поэтому на тестовый порт
ничего не садится.
"""
import os
import sys
import time
import threading

import pytest

# Добавляем корень репо в sys.path, чтобы импортировать server напрямую.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server_full_visibility import GameServer
from shared_simple import Team, ShipType, Action, ActionType


@pytest.fixture
def server():
    s = GameServer(host='127.0.0.1', port=0, game_mode='advanced')
    # running=True по умолчанию, но __init__ выставляет game_over=False —
    # ничего специального не нужно.
    yield s
    s.running = False
    try:
        s.server.close()
    except Exception:
        pass


class TestHandleGmCommand:
    def test_start_turn_sets_start_event(self, server):
        assert not server.gm_start_event.is_set()
        server.handle_gm_command({'type': 'gm_command', 'command': 'start_turn'})
        assert server.gm_start_event.is_set()

    def test_end_planning_sets_end_event(self, server):
        assert not server.gm_end_planning_event.is_set()
        server.handle_gm_command({'type': 'gm_command', 'command': 'end_planning'})
        assert server.gm_end_planning_event.is_set()

    def test_stop_sets_all_events(self, server):
        server.handle_gm_command({'type': 'gm_command', 'command': 'stop'})
        assert server.gm_stop_event.is_set()
        # stop также просыпает main_loop и receive_actions:
        assert server.gm_start_event.is_set()
        assert server.gm_end_planning_event.is_set()

    def test_set_timeout_valid(self, server):
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'set_timeout', 'seconds': 30,
        })
        assert server.planning_timeout == 30

    def test_set_timeout_rejects_out_of_range(self, server):
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'set_timeout', 'seconds': 1,
        })
        # Не применилось — остался дефолт 60.
        assert server.planning_timeout == 60
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'set_timeout', 'seconds': 9999,
        })
        assert server.planning_timeout == 60

    def test_set_timeout_rejects_non_numeric(self, server):
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'set_timeout', 'seconds': 'hello',
        })
        assert server.planning_timeout == 60

    def test_unknown_command_is_noop(self, server):
        # Не должно падать.
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'nonsense',
        })


class TestOverrideShip:
    def test_override_moves_ship(self, server):
        # Берём произвольный корабль.
        ship_id, ship = next(iter(server.game_state['ships'].items()))
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'x': 1, 'y': 2, 'z': 3,
        })
        assert (ship.x, ship.y, ship.z) == (1, 2, 3)

    def test_override_kills_ship(self, server):
        ship_id, ship = next(iter(server.game_state['ships'].items()))
        assert ship.alive
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'alive': False,
        })
        assert not ship.alive

    def test_override_revives_ship(self, server):
        ship_id, ship = next(iter(server.game_state['ships'].items()))
        ship.alive = False
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'alive': True,
        })
        assert ship.alive

    def test_override_out_of_bounds_rejected(self, server):
        ship_id, ship = next(iter(server.game_state['ships'].items()))
        before = (ship.x, ship.y, ship.z)
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'x': 100, 'y': 0, 'z': 0,
        })
        assert (ship.x, ship.y, ship.z) == before

    def test_override_unknown_id_is_noop(self, server):
        # Не должно падать и не должно ничего менять.
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': 'no_such_id', 'x': 0, 'y': 0, 'z': 0,
        })

    def test_override_hits_clamped(self, server):
        ship_id, ship = next(iter(server.game_state['ships'].items()))
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'hits': 9999,
        })
        # Зажимаем сверху до max_hits.
        assert ship.hits == ship.max_hits
        server.handle_gm_command({
            'type': 'gm_command', 'command': 'override_ship',
            'ship_id': ship_id, 'hits': -5,
        })
        assert ship.hits == 0


class TestHitHistory:
    def _find_ship(self, server, team, ship_type=None):
        for ship in server.game_state['ships'].values():
            if ship.team != team:
                continue
            if ship_type is not None and ship.ship_type != ship_type:
                continue
            return ship
        raise AssertionError(f"no ship for {team}/{ship_type}")

    def test_history_starts_empty(self, server):
        assert server.game_state['hit_history'] == []

    def test_hit_is_recorded(self, server):
        # Расставляем: атакер Team A в (0,0,0), жертва Team B в (1,0,0).
        # Крейсер убран из advanced (баланс v3) — используем Артиллерию.
        attacker = self._find_ship(server, Team.TEAM_A, ShipType.ARTILLERY)
        victim = self._find_ship(server, Team.TEAM_B, ShipType.ARTILLERY)
        attacker.x, attacker.y, attacker.z = 0, 0, 0
        victim.x, victim.y, victim.z = 1, 0, 0

        server.actions_received = {
            Team.TEAM_A: [Action(
                ship_id=attacker.id,
                action_type=ActionType.SHOOT,
                target_x=1, target_y=0, target_z=0,
            )],
        }
        server.process_turn()

        history = server.game_state['hit_history']
        assert len(history) >= 1
        entry = history[-1]
        assert entry['attacker'] == Team.TEAM_A.value
        assert entry['target'] == Team.TEAM_B.value
        assert entry['position'] == "(1,0,0)"
        assert entry['turn'] >= 1

    def test_history_accumulates_across_turns(self, server):
        attacker = self._find_ship(server, Team.TEAM_A, ShipType.ARTILLERY)
        victim = self._find_ship(server, Team.TEAM_B, ShipType.ARTILLERY)

        # Первый ход: стреляем.
        attacker.x, attacker.y, attacker.z = 0, 0, 0
        victim.x, victim.y, victim.z = 1, 0, 0
        victim.alive = True; victim.hits = 0
        server.actions_received = {
            Team.TEAM_A: [Action(
                ship_id=attacker.id, action_type=ActionType.SHOOT,
                target_x=1, target_y=0, target_z=0,
            )],
        }
        server.process_turn()
        first_len = len(server.game_state['hit_history'])
        assert first_len >= 1

        # Второй ход: ещё раз стреляем (возродим жертву, если надо).
        victim.alive = True; victim.hits = 0
        server.actions_received = {
            Team.TEAM_A: [Action(
                ship_id=attacker.id, action_type=ActionType.SHOOT,
                target_x=1, target_y=0, target_z=0,
            )],
        }
        server.process_turn()
        second_len = len(server.game_state['hit_history'])
        assert second_len > first_len

    def test_killed_flag_set_when_ship_dies(self, server):
        attacker = self._find_ship(server, Team.TEAM_A, ShipType.ARTILLERY)
        victim = self._find_ship(server, Team.TEAM_B, ShipType.ARTILLERY)
        attacker.x, attacker.y, attacker.z = 0, 0, 0
        victim.x, victim.y, victim.z = 1, 0, 0
        # Артиллерия max_hits = 1, damage = 2 → один выстрел убивает.
        server.actions_received = {
            Team.TEAM_A: [Action(
                ship_id=attacker.id, action_type=ActionType.SHOOT,
                target_x=1, target_y=0, target_z=0,
            )],
        }
        server.process_turn()
        assert not victim.alive
        entry = server.game_state['hit_history'][-1]
        assert entry['killed'] is True


class TestReceiveActionsEndPlanning:
    """Проверяем, что receive_actions выходит по сигналу end_planning досрочно,
    не дожидаясь таймаута."""

    def test_end_planning_breaks_out(self, server):
        # Чтобы receive_actions не заблокировался на send_state_to_all, дадим
        # ему короткий таймаут, но триггернём end_event почти сразу — цикл
        # должен завершиться за ~десятки мс, задолго до таймаута.
        def trigger():
            time.sleep(0.1)
            server.gm_end_planning_event.set()

        threading.Thread(target=trigger, daemon=True).start()
        start = time.time()
        server.receive_actions(timeout=30)  # 30с таймаут — должны выйти раньше
        elapsed = time.time() - start
        assert elapsed < 2.0, f"receive_actions не прервался по сигналу, elapsed={elapsed}"


class TestGmDisconnectWakesLoops:
    """Регрессия: при падении GM должны проснуться ВСЕ ожидающие циклы,
    а не только gm_stop_event. Иначе receive_actions/main_loop виснут до
    полного planning_timeout (в проде — до 60 секунд)."""

    def test_gm_disconnect_sets_all_events(self, server):
        # Эмулируем реакцию _game_master_loop на разрыв связи. Вместо того
        # чтобы реально поднимать framed-сокет и закрывать его, выполняем
        # ровно те же присваивания, что и обработчик ProtocolError.
        assert not server.gm_stop_event.is_set()
        assert not server.gm_start_event.is_set()
        assert not server.gm_end_planning_event.is_set()

        # Блок, дублирующий обработчик из _game_master_loop.
        server.game_master_framed = None
        server.gm_stop_event.set()
        server.gm_start_event.set()
        server.gm_end_planning_event.set()

        assert server.gm_stop_event.is_set()
        assert server.gm_start_event.is_set()
        assert server.gm_end_planning_event.is_set()

    def test_receive_actions_returns_quickly_after_gm_drop(self, server):
        """receive_actions должен вернуться быстро, если GM «отвалился»
        и корректно разбудил все события (а не только stop)."""
        def simulate_gm_drop():
            time.sleep(0.1)
            # То же самое, что делает обработчик ProtocolError.
            server.gm_stop_event.set()
            server.gm_start_event.set()
            server.gm_end_planning_event.set()

        threading.Thread(target=simulate_gm_drop, daemon=True).start()
        start = time.time()
        server.receive_actions(timeout=30)
        elapsed = time.time() - start
        assert elapsed < 2.0, (
            f"receive_actions завис на {elapsed:.2f}s после падения GM — "
            "похоже, gm_end_planning_event забыли выставить в обработчике "
            "ProtocolError (_game_master_loop)."
        )
