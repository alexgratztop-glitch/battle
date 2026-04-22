"""Быстрый демо-рендер UI-компонентов клиента без сервера.

Запуск:  ``python demo_ui.py map``
Пока умеет показывать MapWindow с реалистичным набором кораблей на поле.
Нужно для валидации визуала (скриншоты) без полной сетевой сессии.
"""
from __future__ import annotations

import sys
from tkinter import Tk, Toplevel, LabelFrame, BOTH, X

from client_player_fixed import MapWindow, GameClientGUI
from ui_theme import TEAM_COLORS, apply_theme, Palette


def _demo_ships():
    """Возвращает (ships_of_player, enemies_visible) с разнообразным составом."""
    # Свои — Team A (синий), разные типы, разный HP, фаза у Тишины.
    ships = {}
    for sid, (name, typ, x, y, z, hits, maxh, extra) in enumerate([
        ("Артиллерия A1", "Артиллерия", 2, 3, 4, 0, 1, {"can_shoot": True, "shoot_range": 10, "damage": 1, "shoot_anywhere": True, "move_range": 0}),
        ("Прыгун A2", "Прыгун", 3, 3, 4, 0, 2, {"jump_range": 2, "move_range": 2, "can_shoot": True, "shoot_range": 1, "damage": 1}),
        ("Факел A3", "Факел", 4, 3, 4, 1, 6, {"heal_range": 2, "move_range": 2, "can_shoot": True, "shoot_range": 1, "damage": 1}),
        ("Тишина A4", "Тишина", 5, 3, 4, 0, 2, {"is_phased": True, "phase_cooldown": 3, "can_phase": True, "move_range": 2}),
        ("Бурав A5", "Бурав", 6, 3, 4, 0, 2, {"drill_range": 3, "move_range": 3}),
        ("Провокатор A6", "Провокатор", 7, 3, 4, 0, 2, {"can_create_hologram": True, "can_shoot": True, "shoot_range": 1, "damage": 1, "move_range": 2}),
        ("Паук A7", "Паук", 8, 3, 4, 2, 3, {"can_place_mine": True, "mine_damage": 2, "move_range": 2}),
        ("Радиовышка A8", "Радиовышка", 3, 4, 4, 0, 2, {"scan_whole_z": True, "move_range": 2}),
    ]):
        ships[str(sid)] = {
            "id": str(sid), "name": name, "type": typ, "team": "Team A",
            "x": x, "y": y, "z": z, "alive": True,
            "hits": hits, "max_hits": maxh,
            **extra,
        }
    # Враги — по одному из Team B (красный) и Team C (мятный) в поле видимости.
    enemies = {}
    for sid, (name, typ, x, y, z, team, hits, maxh, extra) in enumerate([
        ("Прыгун B1", "Прыгун", 4, 6, 4, "Team B", 0, 2, {"jump_range": 2}),
        ("Артиллерия B2", "Артиллерия", 3, 7, 4, "Team B", 0, 1, {"can_shoot": True}),
        ("Тишина B3", "Тишина", 6, 6, 4, "Team B", 0, 2, {"is_phased": True}),
        ("Бурав C1", "Бурав", 7, 5, 4, "Team C", 1, 2, {"drill_range": 3}),
        ("Провокатор C2", "Провокатор", 8, 6, 4, "Team C", 0, 2, {}),
    ], start=100):
        enemies[str(sid)] = {
            "id": str(sid), "name": name, "type": typ, "team": team,
            "x": x, "y": y, "z": z, "alive": True,
            "hits": hits, "max_hits": maxh,
            **extra,
        }
    return ships, enemies


def run_map():
    root = Tk()
    apply_theme(root)
    # Корневое окно скрываем — визуализируем только MapWindow (Toplevel).
    root.withdraw()
    mw = MapWindow(root, "Team A", TEAM_COLORS["Team A"])
    # Когда пользователь закрывает карту — завершаем процесс целиком.
    mw.window.protocol("WM_DELETE_WINDOW", root.destroy)
    ships, enemies = _demo_ships()
    mw.update_data(ships, enemies)
    # Симулируем ситуацию: выбрали Прыгун A2, планируем таран — легальные клетки.
    legal = set()
    sx, sy, sz = 3, 3, 4
    # Примерная логика для Прыгуна jump_range=2: все клетки в кубе 2 вокруг.
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            for dz in range(-2, 3):
                if dx == dy == dz == 0:
                    continue
                nx, ny, nz = sx + dx, sy + dy, sz + dz
                if 0 <= nx < 10 and 0 <= ny < 10 and 0 <= nz < 10:
                    legal.add((nx, ny, nz))
    mw.set_targeting(legal, selected=(4, 6, 4), mode="shoot")
    mw.current_layer.set(4)
    root.mainloop()


def run_cards():
    """Демо: рендер панелей карточек кораблей (свои + враги) без сервера."""
    root = Tk()
    apply_theme(root)
    root.title("demo: карточки кораблей")
    root.configure(bg=Palette().bg_root)
    root.geometry("720x1400")

    # Привязываем render-методы к простому mock-объекту вместо полного
    # GameClientGUI (без сетевой логики).  Мы используем сам GameClientGUI
    # ровно настолько, насколько нужны его _render_* методы — передаём
    # его же на created panel'ы.
    ships, enemies = _demo_ships()
    # Добавляем один «мёртвый» корабль, чтобы было видно, как он рендерится.
    ships["dead"] = {
        "id": "dead", "name": "Бурав A9", "type": "Бурав", "team": "Team A",
        "x": 5, "y": 5, "z": 4, "alive": False, "hits": 2, "max_hits": 2,
        "drill_range": 3, "move_range": 3,
    }

    class _MockGui:
        colors = {
            'panel': Palette().bg_panel,
            'accent1': Palette().accent_info,
            'accent2': Palette().accent_danger,
            'accent3': Palette().accent_success,
        }
        root = None  # set below
        _make_scrollable_cards = GameClientGUI._make_scrollable_cards
        _render_hp_bar = GameClientGUI._render_hp_bar
        _render_ship_card = GameClientGUI._render_ship_card
        create_ships_panel = GameClientGUI.create_ships_panel
        create_enemies_panel = GameClientGUI.create_enemies_panel
        update_ships_list = GameClientGUI.update_ships_list
        update_enemies_list = GameClientGUI.update_enemies_list

    gui = _MockGui()
    gui.root = root
    gui.create_ships_panel()
    gui.create_enemies_panel()
    gui.update_ships_list({"my_ships": ships})
    gui.update_enemies_list({"visible_enemies": enemies})
    root.mainloop()


def _demo_history():
    """Возвращает примерную история событий для журнала боя."""
    return [
        # ход 1 — обычные попадания
        {"turn": 1, "attacker": "Team A", "attacker_name": "Артиллерия A1",
         "target": "Team B", "target_name": "Прыгун B1",
         "position": "(4,6,4)", "damage": 1, "killed": False},
        {"turn": 1, "attacker": "Team B", "attacker_name": "Прыгун B1",
         "target": "Team C", "target_name": "Бурав C1",
         "position": "(7,5,4)", "damage": 1, "killed": False},
        # ход 2 — таран
        {"turn": 2, "attacker": "Team A", "attacker_name": "Прыгун A2",
         "target": "Team C", "target_name": "Провокатор C2",
         "position": "(8,6,4)", "damage": 2, "killed": True,
         "ram": True, "type": "ram_kill"},
        {"turn": 2, "attacker": "Team C", "attacker_name": "Бурав C1",
         "target": "Team A", "target_name": "Факел A3",
         "position": "(4,3,4)", "damage": 1, "killed": False},
        # ход 3 — мина и убийство
        {"turn": 3, "type": "mine_detonated", "owner": "Team A",
         "mine_id": "A7-mine", "target": "Team B", "target_name": "Прыгун B1",
         "position": "(5,4,4)", "damage": 2, "killed": True},
        {"turn": 3, "attacker": "Team B", "attacker_name": "Артиллерия B2",
         "target": "Team A", "target_name": "Факел A3",
         "position": "(4,3,4)", "damage": 1, "killed": True},
        # ход 4 — голограмма + добивание
        {"turn": 4, "type": "hologram_destroyed", "attacker": "Team B",
         "attacker_name": "Бурав B4", "owner": "Team A",
         "hologram_id": "A6-holo", "position": "(6,4,4)"},
        {"turn": 4, "attacker": "Team A", "attacker_name": "Артиллерия A1",
         "target": "Team B", "target_name": "Тишина B3",
         "position": "(6,6,4)", "damage": 1, "killed": True},
    ]


def run_legend():
    """Демо: модальная справка по типам кораблей."""
    root = Tk()
    apply_theme(root)
    root.title("demo: легенда")
    root.configure(bg=Palette().bg_root)
    root.geometry("200x60+1200+20")

    class _Mock:
        colors = {
            'panel': Palette().bg_panel, 'accent1': Palette().accent_info,
            'accent3': Palette().accent_success,
        }
        root = None
        open_legend = GameClientGUI.open_legend
        _render_legend_card = GameClientGUI._render_legend_card

    m = _Mock()
    m.root = root
    m.open_legend()
    root.mainloop()


def run_hud():
    """Демо: HUD-панель (ход/фаза/плашки команд)."""
    root = Tk()
    apply_theme(root)
    root.title("demo: HUD")
    root.configure(bg=Palette().bg_root)
    root.geometry("980x220")

    class _Mock:
        colors = {
            'panel': Palette().bg_panel,
            'accent1': Palette().accent_info,
            'accent3': Palette().accent_success,
            'accent4': Palette().accent_warning,
            'text': Palette().fg_primary,
        }
        root = None
        create_info_panel = GameClientGUI.create_info_panel
        _update_team_pills = GameClientGUI._update_team_pills

    m = _Mock()
    m.root = root
    m.create_info_panel()

    # Применяем реалистичное состояние: ход 8, фаза планирования,
    # Team A — наши, противники частично видны, есть история событий.
    m.turn_label.config(text="8")
    m.phase_label.config(text="📝 ПЛАНИРОВАНИЕ",
                         fg=Palette().accent_success)
    m.team_label.config(text="Team A", fg=TEAM_COLORS["Team A"])
    m.player_label.config(text="bpyh2706")

    ships, enemies = _demo_ships()
    # Убьём пару своих, чтобы 6/8.
    for dead_id in ("0", "7"):
        if dead_id in ships:
            ships[dead_id]["alive"] = False
    state = {
        "team": "Team A",
        "my_ships": ships,
        "visible_enemies": enemies,
        "hit_history": _demo_history(),
    }
    m._update_team_pills(state)
    root.mainloop()


def run_log():
    """Демо: рендер журнала боя."""
    root = Tk()
    apply_theme(root)
    root.title("demo: журнал боя")
    root.configure(bg=Palette().bg_root)
    root.geometry("760x520")

    class _Mock:
        colors = {
            'panel': Palette().bg_panel,
            'bg2': Palette().bg_card,
            'accent4': Palette().accent_info,
        }
        root = None
        create_history_panel = GameClientGUI.create_history_panel
        _history_event_icon = GameClientGUI._history_event_icon
        _short_team = GameClientGUI._short_team
        update_history = GameClientGUI.update_history

    m = _Mock()
    m.root = root
    m.create_history_panel()
    m.update_history(_demo_history())
    root.mainloop()


def run_gm():
    """Демо: панель гейммастера с мок-состоянием сервера."""
    import time
    from game_master_gui import GameMasterGUI

    gui = GameMasterGUI()
    # Закрываем окно подключения — работаем в офлайн-режиме.
    for w in gui.root.winfo_children():
        if isinstance(w, Toplevel):
            w.destroy()

    # Собираем максимально показательное состояние.
    ships = {}
    # Team A на z=4.
    a_line = [
        ("Артиллерия A1", "Артиллерия", 2, 3, 4, 0, 1),
        ("Прыгун A2", "Прыгун", 3, 3, 4, 0, 2),
        ("Факел A3", "Факел", 4, 3, 4, 1, 6),
        ("Тишина A4", "Тишина", 5, 3, 4, 0, 2),
        ("Бурав A5", "Бурав", 6, 3, 4, 1, 2),
        ("Провокатор A6", "Провокатор", 7, 3, 4, 0, 2),
        ("Паук A7", "Паук", 8, 3, 4, 2, 3),
        ("Радиовышка A8", "Радиовышка", 3, 4, 4, 0, 2),
    ]
    for i, (nm, tp, x, y, z, h, mh) in enumerate(a_line):
        ships[f"A{i+1}"] = {
            "id": f"A{i+1}", "name": nm, "type": tp, "team": "Team A",
            "x": x, "y": y, "z": z, "alive": True, "hits": h,
            "max_hits": mh, "is_phased": tp == "Тишина",
        }
    # Team B на z=4 (часть видима на карте).
    b_line = [
        ("Прыгун B1", "Прыгун", 4, 6, 4, 0, 2),
        ("Артиллерия B2", "Артиллерия", 3, 7, 4, 0, 1),
        ("Тишина B3", "Тишина", 6, 6, 4, 0, 2, True),
        ("Бурав B4", "Бурав", 5, 8, 4, 0, 2),
    ]
    for i, row in enumerate(b_line):
        nm, tp, x, y, z, h, mh = row[:7]
        ships[f"B{i+1}"] = {
            "id": f"B{i+1}", "name": nm, "type": tp, "team": "Team B",
            "x": x, "y": y, "z": z, "alive": True, "hits": h,
            "max_hits": mh, "is_phased": tp == "Тишина",
        }
    # Team C: пара — один убит.
    ships["C1"] = {
        "id": "C1", "name": "Бурав C1", "type": "Бурав", "team": "Team C",
        "x": 7, "y": 5, "z": 4, "alive": True, "hits": 1, "max_hits": 2,
    }
    ships["C2"] = {
        "id": "C2", "name": "Провокатор C2", "type": "Провокатор",
        "team": "Team C",
        "x": 8, "y": 6, "z": 4, "alive": False, "hits": 2, "max_hits": 2,
    }

    history = [
        {"turn": 1, "kind": "hit", "attacker": "Team A", "target": "Team B",
         "attacker_name": "Артиллерия A1", "target_name": "Прыгун B1",
         "position": "(4,6,4)", "damage": 1, "killed": False},
        {"turn": 2, "kind": "ram", "attacker": "Team A", "target": "Team C",
         "attacker_name": "Прыгун A2", "target_name": "Провокатор C2",
         "position": "(8,6,4)", "damage": 2, "killed": True},
        {"turn": 3, "kind": "mine", "attacker": "Team A", "target": "Team B",
         "attacker_name": "Паук A7", "target_name": "Прыгун B1",
         "position": "(5,7,4)", "damage": 2, "killed": False},
    ]

    state = {
        "turn": 7, "turn_limit": 30, "phase": "planning",
        "planning_deadline": time.time() + 22,
        "actions_received_teams": ["Team A", "Team B"],
        "connected_teams": ["Team A", "Team B", "Team C"],
        "all_ships": ships,
        "hit_history": history,
        "game_over": False,
        "message": "идёт планирование",
    }
    gui.current_state = state
    gui.status_label.config(text="🟢 подключён",
                            fg=gui.pal.accent_success)
    gui.update_interface(state)
    gui.current_layer.set(4)
    gui.update_map()
    # Выбираем корабль A2 для демонстрации Арбитража.
    gui.root.after(100, lambda: gui._select_ship("A2"))
    gui.root.after(200, lambda: gui._push_history("A2  +1 HP"))
    gui.root.after(220,
                    lambda: gui._push_history("B3  ✖ KILL"))
    gui.root.after(240, lambda: gui._push_history(
        "A5 → (6,4,4)  HP:0  жив"))
    gui.root.mainloop()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    if cmd == "map":
        run_map()
    elif cmd == "cards":
        run_cards()
    elif cmd == "log":
        run_log()
    elif cmd == "hud":
        run_hud()
    elif cmd == "legend":
        run_legend()
    elif cmd == "gm":
        run_gm()
    else:
        print(f"unknown demo '{cmd}'", file=sys.stderr)
        sys.exit(2)
