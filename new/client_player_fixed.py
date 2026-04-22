# client_player_fixed.py
import socket
import threading
import time
from tkinter import *
from tkinter import ttk, messagebox, font
from shared_simple import *
from protocol import Framed, ProtocolError
from ui_theme import (
    Palette, Fonts, TEAM_COLORS, SHIP_TYPE_INFO,
    ship_icon, ship_short, ship_role, ship_accent, hp_color, apply_theme,
)


# --------------------------------------------------------------------------- #
# Tooltip — всплывающая подсказка для любого виджета.
# --------------------------------------------------------------------------- #

class Tooltip:
    """Лёгкий tooltip на чистом Tkinter.

    Использование::

        Tooltip(widget, lambda: "Мой текст")

    Лейбл позиционируется справа-снизу курсора и прячется при уходе мыши.
    """

    def __init__(self, widget, text_provider, delay_ms: int = 250):
        self.widget = widget
        self.text_provider = text_provider
        self.delay = delay_ms
        self._after_id = None
        self._tw = None
        self._palette = Palette()
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        text = None
        try:
            text = self.text_provider() if callable(self.text_provider) else self.text_provider
        except Exception:
            text = None
        if not text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tw = Toplevel(self.widget)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry(f"+{x}+{y}")
        self._tw.configure(bg=self._palette.border_strong)
        Label(
            self._tw, text=text, justify=LEFT,
            bg=self._palette.bg_card, fg=self._palette.fg_primary,
            font=("DejaVu Sans", 9), padx=8, pady=4,
            relief=FLAT, bd=0,
        ).pack(padx=1, pady=1)

    def _hide(self, _event=None):
        self._cancel()
        if self._tw is not None:
            try:
                self._tw.destroy()
            except Exception:
                pass
            self._tw = None


class MapWindow:
    """Окно карты 10×10×10 (слой Z — активный).

    Версия v2 (UI overhaul):
    • Каждая клетка — отдельный Canvas ~CELL_SIZE px, что позволяет рисовать:
      иконку типа корабля, HP-бар (цвет зависит от hp/max_hp), бейдж хитов,
      маркер фазы/мины; без нагромождения Label-ов.
    • Единая тема из ui_theme.py.
    • Tooltip при hover показывает полный статус (тип, HP, дальности, фаза).
    • Подсветка легальных клеток (зелёная — ход, янтарная — стрельба/таран),
      выделение выбранной цели — белая рамка.
    • Отдельный слой Z-mini: 10 «столбиков» слева от карты показывают,
      на каких Z-слоях есть свои/вражеские корабли.

    Публичный контракт (не менять — используется GameClientGUI):
      self.window              — Toplevel
      self.current_layer       — IntVar (0..9)
      update_data(ships, enem) — обновить данные и перерисовать
      set_targeting(legal, selected, mode)
      clear_targeting()
    """

    CELL_SIZE = 54     # ширина/высота квадрата одной клетки карты, px.
    GRID_SIZE = 10

    def __init__(self, parent, team_name, team_color, gui=None):
        self.parent = parent
        self.gui = gui
        self.team_name = team_name
        self.team_color = team_color
        self.palette = Palette()
        self.fonts = Fonts()
        self.window = Toplevel(parent)
        self.window.title(f"🗺 Карта — {team_name}")
        cell = self.CELL_SIZE
        # Ширина: 10 клеток карты + левая Z-колонка + поля.
        width = cell * self.GRID_SIZE + 160
        # Высота: заголовок + карта + легенда + статус.
        height = cell * self.GRID_SIZE + 220
        self.window.geometry(f"{width}x{height}")
        apply_theme(self.window, self.palette, self.fonts)

        self.current_layer = IntVar(value=0)
        self.ships_data = {}
        self.enemies_data = {}
        self._legal_cells = set()
        self._selected_target = None
        self._targeting_mode = None
        # (row, col) -> Canvas.
        self._cells = {}
        self._cell_tooltip_text = {}     # (x, y) -> str
        self._cell_tooltip_objs = {}     # (x, y) -> Tooltip
        # Колонки Z-мини-миникарты (0..9) -> Canvas.
        self._z_columns = {}

        self._build()
        self.current_layer.trace("w", self._on_layer_change)

    # --------------------------------------------------------------- build ---

    def _build(self):
        p = self.palette
        f = self.fonts

        # Заголовок — крупная плашка с именем команды и подсказкой.
        header = Frame(self.window, bg=p.bg_root, height=60)
        header.pack(fill=X)
        header.pack_propagate(False)
        Label(
            header, text=f"🗺  КАРТА  •  {self.team_name}",
            bg=p.bg_root, fg=self.team_color, font=f.h1,
        ).pack(side=LEFT, padx=18, pady=10)
        Label(
            header,
            text="X →   Y ↓   Z-слой ниже",
            bg=p.bg_root, fg=p.fg_secondary, font=f.small,
        ).pack(side=RIGHT, padx=18)

        # Панель управления слоем (scale + label).
        control = Frame(self.window, bg=p.bg_panel, height=46)
        control.pack(fill=X, padx=10, pady=(0, 6))
        control.pack_propagate(False)
        Label(
            control, text="Слой Z:", bg=p.bg_panel, fg=p.fg_secondary,
            font=f.body,
        ).pack(side=LEFT, padx=(14, 6))
        Scale(
            control, from_=0, to=9, variable=self.current_layer,
            orient=HORIZONTAL, length=280, showvalue=False,
            bg=p.bg_panel, fg=p.accent_info, troughcolor=p.bg_root,
            activebackground=p.accent_info,
            highlightbackground=p.bg_panel, bd=0,
        ).pack(side=LEFT, padx=6)
        self.layer_label = Label(
            control, text="Z = 0", bg=p.bg_panel, fg=p.accent_info,
            font=f.h2, width=7, anchor=W,
        )
        self.layer_label.pack(side=LEFT, padx=10)
        Button(
            control, text="🔄 Обновить", bg=p.accent_info, fg="#06122a",
            font=f.body_bold, bd=0, relief=FLAT, padx=14, pady=4,
            activebackground="#3be6ff", activeforeground="#06122a",
            command=self.update_map,
        ).pack(side=RIGHT, padx=12)

        # Основное тело: слева Z-миникарта, справа — карта 10×10.
        body = Frame(self.window, bg=p.bg_root)
        body.pack(fill=BOTH, expand=True, padx=10, pady=2)

        # Z-миникарта: вертикальная колонка 10 ячеек (Z=9 сверху → Z=0 снизу
        # соответствует восприятию «высота») + стрелка «вы здесь».
        zmini = Frame(body, bg=p.bg_panel, bd=0)
        zmini.pack(side=LEFT, fill=Y, padx=(2, 10))
        Label(
            zmini, text="Z-слои", bg=p.bg_panel, fg=p.fg_secondary,
            font=f.small_bold,
        ).pack(anchor=N, pady=(6, 4))
        zmini_grid = Frame(zmini, bg=p.bg_panel)
        zmini_grid.pack(padx=4, pady=2)
        for z in range(9, -1, -1):
            row = Frame(zmini_grid, bg=p.bg_panel)
            row.pack(anchor=W, pady=1)
            marker = Canvas(
                row, width=14, height=14, bg=p.bg_panel,
                highlightthickness=0,
            )
            marker.pack(side=LEFT)
            Label(
                row, text=f"Z{z}", bg=p.bg_panel, fg=p.fg_secondary,
                font=f.small, width=3, anchor=W,
            ).pack(side=LEFT)
            bar = Canvas(
                row, width=90, height=14, bg=p.bg_panel,
                highlightthickness=0, cursor="hand2",
            )
            bar.pack(side=LEFT, padx=4)
            bar.bind(
                "<Button-1>",
                lambda _e, z=z: self.current_layer.set(z),
            )
            marker.bind(
                "<Button-1>",
                lambda _e, z=z: self.current_layer.set(z),
            )
            self._z_columns[z] = (marker, bar)

        # Карта.
        grid_wrap = Frame(body, bg=p.bg_panel, bd=0)
        grid_wrap.pack(side=LEFT, padx=0, pady=0)

        # Верхняя шкала X (0..9).
        for col in range(self.GRID_SIZE):
            Label(
                grid_wrap, text=str(col), bg=p.bg_panel, fg=p.fg_muted,
                font=self.fonts.small_bold, width=2,
            ).grid(row=0, column=col + 1, padx=0, pady=(2, 1))

        for row in range(self.GRID_SIZE):
            Label(
                grid_wrap, text=str(row), bg=p.bg_panel, fg=p.fg_muted,
                font=self.fonts.small_bold, width=2,
            ).grid(row=row + 1, column=0, padx=(2, 4), pady=0)
            for col in range(self.GRID_SIZE):
                c = Canvas(
                    grid_wrap, width=self.CELL_SIZE, height=self.CELL_SIZE,
                    bg=p.bg_cell_empty, highlightthickness=1,
                    highlightbackground=p.border, bd=0, cursor="hand2",
                )
                c.grid(row=row + 1, column=col + 1, padx=1, pady=1)
                c.bind(
                    "<Button-1>",
                    lambda _e, x=col, y=row: self._on_cell_click(x, y),
                )
                self._cells[(col, row)] = c
                # Tooltip — ленивое получение текста при показе.
                self._cell_tooltip_objs[(col, row)] = Tooltip(
                    c,
                    (lambda cx=col, cy=row:
                     self._cell_tooltip_text.get((cx, cy))),
                )

        # Легенда (все 10 типов, компактно, в 2 строки).
        legend = Frame(self.window, bg=p.bg_panel)
        legend.pack(fill=X, padx=10, pady=(6, 2))
        Label(
            legend, text="Легенда:", bg=p.bg_panel, fg=p.accent_warning,
            font=f.small_bold,
        ).pack(side=LEFT, padx=(10, 8))
        legend_grid = Frame(legend, bg=p.bg_panel)
        legend_grid.pack(side=LEFT, fill=X, expand=True, pady=4)
        items = list(SHIP_TYPE_INFO.items())
        # Базовый не используется в advanced-режиме: показываем все типы, но
        # сохраняем компактную сетку 5×2.
        cols_per_row = 5
        for idx, (tname, info) in enumerate(items):
            r, c = divmod(idx, cols_per_row)
            cell = Frame(legend_grid, bg=p.bg_panel)
            cell.grid(row=r, column=c, sticky=W, padx=(4, 14), pady=2)
            Label(
                cell, text=info["icon"], bg=p.bg_panel,
                fg=info["accent"], font=f.body_bold, width=2,
            ).pack(side=LEFT)
            Label(
                cell, text=tname, bg=p.bg_panel, fg=p.fg_primary,
                font=f.small,
            ).pack(side=LEFT)

        # Статусная строка.
        self.status_label = Label(
            self.window, text="Карта загружается…",
            bg=p.bg_panel, fg=p.accent_info, font=f.small, anchor=W,
        )
        self.status_label.pack(fill=X, padx=10, pady=(2, 8))

    # ------------------------------------------------------------- render ---

    def _on_layer_change(self, *_args):
        self.update_map()

    def update_data(self, ships_data, enemies_data):
        self.ships_data = ships_data or {}
        self.enemies_data = enemies_data or {}
        self.update_map()

    def set_targeting(self, legal_cells=None, selected=None, mode=None):
        self._legal_cells = set(legal_cells) if legal_cells else set()
        self._selected_target = selected
        self._targeting_mode = mode
        if selected is not None:
            _, _, sz = selected
            if int(self.current_layer.get()) != sz:
                self.current_layer.set(sz)
                return
        self.update_map()

    def clear_targeting(self):
        self.set_targeting(None, None, None)

    def _on_cell_click(self, x, y):
        if self.gui is None:
            return
        self.gui.on_map_click(x, y, self.current_layer.get())

    def _cell_for(self, x, y):
        return self._cells[(x, y)]

    def update_map(self, *_args):
        layer = int(self.current_layer.get())
        self.layer_label.config(text=f"Z = {layer}")
        p = self.palette
        # Сброс содержимого и фона всех клеток.
        for (x, y), c in self._cells.items():
            c.delete("all")
            c.configure(
                bg=p.bg_cell_empty,
                highlightthickness=1,
                highlightbackground=p.border,
            )
            self._cell_tooltip_text[(x, y)] = None

        # Рисуем корабли союзной команды.
        my_count, my_radio = 0, []
        for _sid, ship in (self.ships_data or {}).items():
            if not ship.get("alive", False):
                continue
            if ship.get("z") != layer:
                continue
            self._draw_ship(ship, friendly=True)
            my_count += 1
            if ship.get("type") == "Радиовышка":
                my_radio.append(ship)

        # Вражеские корабли.
        enemy_count = 0
        for _sid, ship in (self.enemies_data or {}).items():
            if not ship.get("alive", False):
                continue
            if ship.get("z") != layer:
                continue
            self._draw_ship(ship, friendly=False)
            enemy_count += 1

        # Легальные клетки и выбранная цель.
        if self._legal_cells:
            legal_bg = (
                p.bg_cell_legal_shoot if self._targeting_mode == "shoot"
                else p.bg_cell_legal_move
            )
            legal_border = (
                p.accent_warning if self._targeting_mode == "shoot"
                else p.accent_success
            )
            for (lx, ly, lz) in self._legal_cells:
                if lz != layer or not (0 <= lx < 10 and 0 <= ly < 10):
                    continue
                c = self._cells[(lx, ly)]
                # Не затираем отрисованный корабль; только рамку.
                has_ship = bool(self._cell_tooltip_text.get((lx, ly)))
                if not has_ship:
                    c.configure(bg=legal_bg)
                c.configure(
                    highlightthickness=2,
                    highlightbackground=legal_border,
                )
        if self._selected_target is not None:
            sx, sy, sz = self._selected_target
            if sz == layer and 0 <= sx < 10 and 0 <= sy < 10:
                self._cells[(sx, sy)].configure(
                    highlightthickness=3,
                    highlightbackground=p.fg_title,
                )

        # Z-миникарта: по каждому слою Z считаем своих/врагов и рисуем.
        self._update_zcolumn(layer)

        # Статус.
        status_bits = [
            f"Слой Z={layer}",
            f"Свои: {my_count}",
            f"Врагов: {enemy_count}",
        ]
        if my_radio:
            status_bits.append("📡 Радиовышка сканирует слой")
        if self._targeting_mode == "move":
            status_bits.append("🚀 Кликните клетку для хода")
        elif self._targeting_mode == "shoot":
            status_bits.append("🎯 Кликните клетку для атаки")
        self.status_label.config(text="   •   ".join(status_bits))

    def _update_zcolumn(self, active_layer):
        p = self.palette
        per_layer_mine = [0] * 10
        per_layer_enemy = [0] * 10
        for ship in (self.ships_data or {}).values():
            if ship.get("alive"):
                z = ship.get("z")
                if isinstance(z, int) and 0 <= z <= 9:
                    per_layer_mine[z] += 1
        for ship in (self.enemies_data or {}).values():
            if ship.get("alive"):
                z = ship.get("z")
                if isinstance(z, int) and 0 <= z <= 9:
                    per_layer_enemy[z] += 1
        for z, (marker, bar) in self._z_columns.items():
            marker.delete("all")
            if z == active_layer:
                marker.create_polygon(
                    (2, 2, 12, 7, 2, 12),
                    fill=p.accent_info, outline="",
                )
            bar.delete("all")
            # Рисуем две полоски: свои (слева), враги (справа).
            mine = per_layer_mine[z]
            enemy = per_layer_enemy[z]
            # Нормируем на 8 кораблей (максимум в команде).
            my_len = min(mine, 8) * 5
            en_len = min(enemy, 8) * 5
            if my_len > 0:
                bar.create_rectangle(
                    0, 2, my_len, 6, fill=self.team_color, outline="",
                )
            if en_len > 0:
                bar.create_rectangle(
                    0, 8, en_len, 12, fill=p.accent_danger, outline="",
                )
            if mine or enemy:
                bar.create_text(
                    60, 7, text=f"{mine}/{enemy}",
                    fill=p.fg_secondary, font=self.fonts.small, anchor=W,
                )

    def _draw_ship(self, ship, friendly: bool):
        """Рисует содержимое одной клетки для корабля.

        Композиция:
          1) Фоновая плашка (цвет команды-хозяина; для «своих» — self.team_color).
          2) Крупная иконка типа (эмодзи) в центре.
          3) HP-бар внизу (цвет от hp_color).
          4) Маркер фазы (шестиугольная рамка) или фокуса (круг) при состоянии.

        Дополнительно: сохраняет в _cell_tooltip_text строку для подсказки.
        """
        p = self.palette
        x, y = ship["x"], ship["y"]
        if (x, y) not in self._cells:
            return
        c = self._cells[(x, y)]
        size = self.CELL_SIZE

        ship_type = ship.get("type") or ship.get("ship_type") or "Базовый"
        team = ship.get("team", "")
        info = SHIP_TYPE_INFO.get(ship_type, {})
        icon = info.get("icon", "🛰")
        base_color = (
            self.team_color if friendly
            else TEAM_COLORS.get(team, p.accent_danger)
        )

        # Фон клетки: тонированный цвет команды + рамка.
        c.configure(bg=self._tint(base_color))
        # Основная «подложка» корабля — круг в центре с окантовкой цвета команды.
        margin = 4
        c.create_rectangle(
            margin, margin, size - margin, size - margin,
            fill=p.bg_card, outline=base_color, width=2,
        )
        # Иконка типа.
        c.create_text(
            size / 2, size / 2 - 3,
            text=icon, fill=base_color,
            font=self.fonts.cell_icon,
        )

        # Короткая буква в углу (для надёжной читаемости).
        c.create_text(
            8, 10, text=ship_short(ship_type),
            fill=p.fg_primary, font=self.fonts.small_bold, anchor=W,
        )

        # HP-бар внизу. hits = число попаданий, hp = max_hits - hits.
        max_hits = int(ship.get("max_hits") or 1)
        hits = int(ship.get("hits") or 0)
        hp = max(0, max_hits - hits)
        bar_y = size - 10
        bar_x0, bar_x1 = 6, size - 6
        c.create_rectangle(
            bar_x0, bar_y, bar_x1, bar_y + 4,
            fill=p.bg_root, outline="",
        )
        if max_hits > 0 and hp > 0:
            pct = hp / max_hits
            filled = bar_x0 + int((bar_x1 - bar_x0) * pct)
            c.create_rectangle(
                bar_x0, bar_y, filled, bar_y + 4,
                fill=hp_color(hp, max_hits, p), outline="",
            )

        # Фаза.
        if ship.get("is_phased"):
            c.create_oval(
                2, 2, size - 2, size - 2,
                outline=p.accent_phase, width=2, dash=(4, 2),
            )

        # Tooltip.
        lines = [
            f"{icon}  {ship_type}  ({ship.get('name', ship.get('id', '?'))})",
            f"Команда: {team}" if team else None,
            f"HP: {hp}/{max_hits}",
        ]
        extras = []
        if ship.get("jump_range"):
            extras.append(f"jump {ship['jump_range']}")
        if ship.get("drill_range"):
            extras.append(f"drill {ship['drill_range']}")
        if ship.get("heal_range"):
            extras.append(f"heal {ship['heal_range']}")
        if ship.get("shoot_range") and ship.get("can_shoot"):
            extras.append(f"shoot {ship['shoot_range']}")
        if ship.get("move_range"):
            extras.append(f"move {ship['move_range']}")
        if extras:
            lines.append("Дальности: " + ", ".join(extras))
        if ship.get("is_phased"):
            lines.append("⚡ Фаза активна (неуязвим 1 ход)")
        pc = ship.get("phase_cooldown") or 0
        if pc:
            lines.append(f"⏳ PHASE cooldown: {pc}")
        role = ship_role(ship_type)
        if role:
            lines.append(f"Роль: {role}")
        self._cell_tooltip_text[(x, y)] = "\n".join(
            line for line in lines if line
        )

    @staticmethod
    def _tint(hex_color: str, alpha: float = 0.25) -> str:
        """Смешивает цвет с тёмным фоном для «приглушённой» плашки клетки."""
        try:
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except Exception:
            return hex_color
        # Фон под тонировку — bg_cell_empty.
        bg = (0x16, 0x20, 0x4a)
        nr = int(r * alpha + bg[0] * (1 - alpha))
        ng = int(g * alpha + bg[1] * (1 - alpha))
        nb = int(b * alpha + bg[2] * (1 - alpha))
        return f"#{nr:02x}{ng:02x}{nb:02x}"

class GameClientGUI:
    def __init__(self):
        self.socket = None
        self.team = None
        self.connected = False
        self.player_name = ""
        self.current_state = None
        
        # Цветовая схема
        self.colors = {
            'bg': '#0a0e27',
            'bg2': '#1a1f3a',
            'fg': '#ffffff',
            'accent1': '#00d4ff',
            'accent2': '#ff6b6b',
            'accent3': '#6bff6b',
            'accent4': '#ffd700',
            'panel': '#151a33',
            'text': '#e0e0ff'
        }
        
        # Цвета команд
        self.team_colors = {
            'Team A': '#4169E1',
            'Team B': '#DC143C',
            'Team C': '#228B22'
        }
        
        # Создаем главное окно
        self.root = Tk()
        self.root.title("🚀 КОСМИЧЕСКИЙ БОЙ - ИГРОК")
        self.root.geometry("1000x800")
        self.root.configure(bg=self.colors['bg'])
        
        # Переменные
        self.actions = []
        self.map_window = None
        self.team_color = '#00d4ff'  # будет переопределено при подключении
        # Состояние «выбора цели на карте»: когда игрок в планировщике
        # нажимает «Выбрать на карте», сюда кладётся словарь с контекстом,
        # а кликом по карте координаты записываются в нужные StringVar'ы.
        #   {'ship': <dict>, 'kind': 'move'|'shoot',
        #    'vars': (x_var, y_var, z_var), 'legal': set((x,y,z),...)}
        self.target_capture = None

        # Создаем интерфейс
        self.create_widgets()
        
        # Запускаем окно подключения
        self.show_connection_window()
    
    def create_widgets(self):
        """Создает основные виджеты интерфейса"""
        # Верхняя панель с заголовком
        header_frame = Frame(self.root, bg='#000000', height=80)
        header_frame.pack(fill=X)
        header_frame.pack_propagate(False)
        
        # Заголовок
        title_label = Label(header_frame,
                           text="🚀 КОСМИЧЕСКИЙ БОЙ 10x10x10",
                           bg='#000000', fg=self.colors['accent1'],
                           font=('Arial', 20, 'bold'))
        title_label.pack(expand=True)
        
        subtitle_label = Label(header_frame,
                              text="КЛИЕНТ ИГРОКА",
                              bg='#000000', fg='white',
                              font=('Arial', 12))
        subtitle_label.pack()
        
        # Статусная строка под заголовком
        status_bar = Frame(self.root, bg=self.colors['panel'], height=30)
        status_bar.pack(fill=X, padx=10, pady=5)
        status_bar.pack_propagate(False)
        
        self.status_label = Label(status_bar, text="⚫ Не подключен",
                                  bg=self.colors['panel'], fg='red',
                                  font=('Arial', 10, 'bold'))
        self.status_label.pack(side=LEFT, padx=10)

        # Визуальный таймер фазы планирования (обновляется каждые 0.5с).
        self.timer_label = Label(
            status_bar, text="", bg=self.colors['panel'],
            fg=self.colors['accent4'], font=('Arial', 11, 'bold'),
        )
        self.timer_label.pack(side=RIGHT, padx=10)
        
        # Панель информации
        self.create_info_panel()
        
        # Панель кораблей
        self.create_ships_panel()
        
        # Панель врагов
        self.create_enemies_panel()

        # Журнал попаданий за всю партию.
        self.create_history_panel()
        
        # Нижняя панель с кнопками
        self.create_button_panel()
    
    def open_legend(self):
        """Модальное окно со справкой по всем типам кораблей."""
        pal = Palette()
        fnt = Fonts()

        if getattr(self, "_legend_window", None) is not None:
            try:
                if self._legend_window.winfo_exists():
                    self._legend_window.lift()
                    self._legend_window.focus_force()
                    return
            except Exception:
                pass

        win = Toplevel(self.root)
        win.title("Справка · Типы кораблей")
        win.configure(bg=pal.bg_root)
        win.geometry("760x620")
        win.transient(self.root)
        self._legend_window = win

        # Заголовок.
        head = Frame(win, bg=pal.bg_root)
        head.pack(fill=X, padx=20, pady=(16, 8))
        Label(head, text="📖 Справочник по типам кораблей",
              bg=pal.bg_root, fg=pal.fg_title, font=fnt.h1).pack(side=LEFT)
        Button(head, text="✕", width=3, bg=pal.bg_root, fg=pal.fg_secondary,
               activebackground=pal.bg_panel, activeforeground=pal.fg_title,
               bd=0, relief=FLAT, font=fnt.h3, cursor="hand2",
               command=win.destroy).pack(side=RIGHT)

        # Прокручиваемый контейнер для карточек типов.
        canvas = Canvas(win, bg=pal.bg_root, highlightthickness=0, bd=0)
        canvas.pack(side=LEFT, fill=BOTH, expand=True, padx=(20, 0), pady=4)
        sb = ttk.Scrollbar(win, orient=VERTICAL, command=canvas.yview)
        sb.pack(side=RIGHT, fill=Y, padx=(0, 20), pady=4)
        canvas.configure(yscrollcommand=sb.set)

        inner = Frame(canvas, bg=pal.bg_root)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e, c=canvas, i=inner_id: c.itemconfigure(i, width=e.width),
        )
        # Мышь-колёсико.
        for wheel_ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(
                wheel_ev,
                lambda e, c=canvas: c.yview_scroll(
                    -1 if getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4 else 1,
                    "units",
                ),
            )
        win.protocol(
            "WM_DELETE_WINDOW",
            lambda c=canvas, w=win: (
                [c.unbind_all(e) for e in ("<MouseWheel>", "<Button-4>", "<Button-5>")],
                w.destroy(),
            ),
        )

        # Порядок — как в SHIP_TYPE_INFO (кроме «Базовый»).
        order = ["Прыгун", "Артиллерия", "Бурав", "Факел", "Тишина",
                 "Провокатор", "Паук", "Радиовышка", "Крейсер"]
        for typ in order:
            info = SHIP_TYPE_INFO.get(typ)
            if not info:
                continue
            self._render_legend_card(inner, typ, info, pal, fnt)

        # Футер — краткие правила.
        footer = Frame(win, bg=pal.bg_root)
        footer.pack(fill=X, padx=20, pady=(6, 14))
        Label(
            footer,
            text=("🏁 Победа: уничтожить корабли всех других команд. "
                  "При таймауте (30 ходов) — побеждает команда с бо́льшим уроном."),
            bg=pal.bg_root, fg=pal.fg_secondary, font=fnt.small,
            justify=LEFT, wraplength=700,
        ).pack(side=LEFT, fill=X, expand=True)

        win.bind("<Escape>", lambda _e: win.destroy())
        win.focus_force()

    def _render_legend_card(self, parent, ship_type, info, pal, fnt):
        """Одна карточка описания типа корабля в модалке справки."""
        accent = info.get("accent", pal.fg_primary)
        card = Frame(parent, bg=pal.bg_card, bd=0, relief=FLAT,
                     highlightbackground=pal.border, highlightthickness=1)
        card.pack(fill=X, padx=4, pady=4)

        # Левая полоска-акцент.
        Frame(card, bg=accent, width=5).pack(side=LEFT, fill=Y)

        body = Frame(card, bg=pal.bg_card)
        body.pack(side=LEFT, fill=BOTH, expand=True, padx=10, pady=8)

        head = Frame(body, bg=pal.bg_card)
        head.pack(fill=X)
        Label(head, text=info.get("icon", "🛰"),
              bg=pal.bg_card, fg=accent, font=fnt.h1).pack(side=LEFT)
        Label(head, text=f"  {ship_type}",
              bg=pal.bg_card, fg=pal.fg_title, font=fnt.h2).pack(side=LEFT)
        role = info.get("role", "")
        if role:
            Label(head, text=f"  · {role}",
                  bg=pal.bg_card, fg=pal.fg_secondary, font=fnt.small
                  ).pack(side=LEFT)

        # Статы.
        stats = info.get("stats") or {}
        if stats:
            st = Frame(body, bg=pal.bg_card)
            st.pack(fill=X, pady=(4, 2))
            for k, v in stats.items():
                pill = Frame(st, bg=pal.bg_panel, bd=0)
                pill.pack(side=LEFT, padx=(0, 6))
                Label(pill, text=f"{k}", bg=pal.bg_panel,
                      fg=pal.fg_muted, font=fnt.small,
                      ).pack(side=LEFT, padx=(6, 2), pady=2)
                Label(pill, text=f"{v}", bg=pal.bg_panel,
                      fg=pal.fg_primary, font=fnt.small_bold,
                      ).pack(side=LEFT, padx=(0, 6), pady=2)

        # Способности — bullet-список.
        for line in (info.get("abilities") or []):
            Label(body, text=f"• {line}", bg=pal.bg_card,
                  fg=pal.fg_primary, font=fnt.small,
                  justify=LEFT, wraplength=640, anchor=W
                  ).pack(fill=X, anchor=W)

    def create_info_panel(self):
        """HUD-панель: ход, фаза, плашки команд (живые/урон/киллы)."""
        pal = Palette()
        fnt = Fonts()

        hud = Frame(self.root, bg=pal.bg_panel, bd=1, relief=FLAT)
        hud.pack(fill=X, padx=10, pady=(0, 6))

        # --- Левая секция: ход + фаза + команда/игрок ------------------- #
        left = Frame(hud, bg=pal.bg_panel)
        left.pack(side=LEFT, fill=Y, padx=10, pady=8)

        Label(left, text="ХОД", bg=pal.bg_panel, fg=pal.fg_secondary,
              font=fnt.small).grid(row=0, column=0, sticky=W)
        self.turn_label = Label(
            left, text="0", bg=pal.bg_panel, fg=pal.accent_info, font=fnt.h1,
        )
        self.turn_label.grid(row=1, column=0, sticky=W, padx=(0, 2))
        self.turn_limit_label = Label(
            left, text="/30", bg=pal.bg_panel, fg=pal.fg_muted, font=fnt.h3,
        )
        self.turn_limit_label.grid(row=1, column=1, sticky=SW, pady=(0, 4))

        Label(left, text="ФАЗА", bg=pal.bg_panel, fg=pal.fg_secondary,
              font=fnt.small).grid(row=0, column=2, sticky=W, padx=(16, 0))
        self.phase_label = Label(
            left, text="ожидание", bg=pal.bg_panel, fg=pal.accent_warning,
            font=fnt.body_bold,
        )
        self.phase_label.grid(row=1, column=2, sticky=W, padx=(16, 0))

        Label(left, text="ВЫ", bg=pal.bg_panel, fg=pal.fg_secondary,
              font=fnt.small).grid(row=0, column=3, sticky=W, padx=(16, 0))
        self.team_label = Label(
            left, text="—", bg=pal.bg_panel, fg=pal.fg_primary,
            font=fnt.body_bold,
        )
        self.team_label.grid(row=1, column=3, sticky=W, padx=(16, 0))
        self.player_label = Label(
            left, text="", bg=pal.bg_panel, fg=pal.fg_muted, font=fnt.small,
        )
        self.player_label.grid(row=2, column=3, sticky=W, padx=(16, 0))

        # --- Правая секция: плашки 3 команд ---------------------------- #
        right = Frame(hud, bg=pal.bg_panel)
        right.pack(side=RIGHT, fill=Y, padx=10, pady=6)

        # Кнопка «?»-справки — самая правая.
        help_btn = Button(
            right, text="?", width=3, bg=pal.bg_card, fg=pal.accent_info,
            activebackground=pal.border_strong, activeforeground=pal.fg_title,
            font=fnt.h3, bd=0, relief=FLAT, cursor="hand2",
            command=self.open_legend,
        )
        help_btn.grid(row=0, column=99, padx=(10, 0), sticky=NS)
        Tooltip(help_btn, lambda: "Справка по типам кораблей")

        self._team_pill_widgets = {}
        for i, team_name in enumerate(("Team A", "Team B", "Team C")):
            pill = Frame(right, bg=pal.bg_card, bd=1, relief=FLAT)
            pill.grid(row=0, column=i, padx=4, pady=2, sticky=NS)
            color = TEAM_COLORS.get(team_name, pal.fg_primary)
            # Цветная полоска слева от плашки.
            stripe = Frame(pill, bg=color, width=4)
            stripe.pack(side=LEFT, fill=Y)
            body = Frame(pill, bg=pal.bg_card)
            body.pack(side=LEFT, fill=BOTH, expand=True, padx=8, pady=4)

            name_lbl = Label(
                body, text=team_name, bg=pal.bg_card, fg=color,
                font=fnt.body_bold,
            )
            name_lbl.grid(row=0, column=0, columnspan=3, sticky=W)

            alive_lbl = Label(
                body, text="—/—", bg=pal.bg_card, fg=pal.fg_primary,
                font=fnt.body_bold,
            )
            alive_lbl.grid(row=1, column=0, sticky=W, padx=(0, 10))
            dmg_lbl = Label(
                body, text="⚡0", bg=pal.bg_card, fg=pal.accent_warning,
                font=fnt.small_bold,
            )
            dmg_lbl.grid(row=1, column=1, sticky=W, padx=(0, 8))
            kill_lbl = Label(
                body, text="✖0", bg=pal.bg_card, fg=pal.accent_danger,
                font=fnt.small_bold,
            )
            kill_lbl.grid(row=1, column=2, sticky=W)

            Label(body, text="живых", bg=pal.bg_card, fg=pal.fg_muted,
                  font=fnt.small).grid(row=2, column=0, sticky=W)
            Label(body, text="урон", bg=pal.bg_card, fg=pal.fg_muted,
                  font=fnt.small).grid(row=2, column=1, sticky=W)
            Label(body, text="киллы", bg=pal.bg_card, fg=pal.fg_muted,
                  font=fnt.small).grid(row=2, column=2, sticky=W)

            self._team_pill_widgets[team_name] = {
                "frame": pill, "name": name_lbl,
                "alive": alive_lbl, "dmg": dmg_lbl, "kill": kill_lbl,
            }

        # Подсказка по видимости (маленькой подписью).
        note = Label(
            hud,
            text="👁 Радиус обзора 4 · Радиовышка видит всю свою Z-плоскость",
            bg=pal.bg_panel, fg=pal.fg_muted, font=fnt.small,
        )
        note.pack(side=BOTTOM, fill=X, padx=10, pady=(0, 4))
    
    # ------------------------------------------------------------------ #
    #  Карточки кораблей (Task 2)
    # ------------------------------------------------------------------ #

    def _make_scrollable_cards(self, parent):
        """Создаёт Canvas + inner Frame + scrollbar для карточек."""
        pal = Palette()
        canvas = Canvas(parent, bg=pal.bg_panel, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(parent, orient=VERTICAL, command=canvas.yview)
        inner = Frame(canvas, bg=pal.bg_panel)
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True, padx=2, pady=2)
        sb.pack(side=RIGHT, fill=Y)

        # Прокрутка колесом мыши при наведении курсора.
        def _on_wheel(event):
            delta = -1 if (getattr(event, 'num', 0) == 4 or event.delta > 0) else 1
            canvas.yview_scroll(delta, "units")
        def _bind_wheel(_e):
            canvas.bind_all("<MouseWheel>", _on_wheel)
            canvas.bind_all("<Button-4>", _on_wheel)
            canvas.bind_all("<Button-5>", _on_wheel)
        def _unbind_wheel(_e):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        return canvas, inner

    def _render_hp_bar(self, parent, hp, max_hp, width=120, height=8):
        """Рисует горизонтальный HP-бар на Canvas-виджете."""
        pal = Palette()
        c = Canvas(parent, width=width, height=height,
                   bg=pal.bg_card, highlightthickness=0, bd=0)
        c.create_rectangle(0, 0, width, height, fill=pal.bg_panel, outline="")
        if max_hp > 0 and hp > 0:
            fill_w = max(2, int(width * hp / max_hp))
            color = hp_color(hp, max_hp)
            c.create_rectangle(0, 0, fill_w, height, fill=color, outline="")
        return c

    def _render_ship_card(self, parent, ship, ship_id, is_enemy=False):
        """Рисует одну карточку корабля (Frame)."""
        pal = Palette()
        fnt = Fonts()
        alive = ship.get('alive', True)
        stype = ship.get('type', 'Базовый')
        icon = ship_icon(stype)
        short = ship_short(stype)
        accent = ship_accent(stype)
        team = ship.get('team', '')
        team_clr = TEAM_COLORS.get(team, pal.fg_secondary)

        bg = pal.bg_card if alive else pal.bg_panel
        fg = pal.fg_primary if alive else pal.fg_muted

        card = Frame(parent, bg=bg, highlightbackground=pal.border,
                     highlightthickness=1, padx=6, pady=4)
        card.pack(fill=X, padx=4, pady=2)

        # --- Row 1: icon + name + position + status ---------
        row1 = Frame(card, bg=bg)
        row1.pack(fill=X)

        badge_text = f"{icon} {short}"
        badge_bg = team_clr if is_enemy else accent
        Label(row1, text=badge_text, bg=badge_bg, fg="#000",
              font=fnt.small_bold, padx=4, pady=1).pack(side=LEFT, padx=(0, 6))

        name_txt = ship.get('name', stype)
        Label(row1, text=name_txt, bg=bg, fg=fg,
              font=fnt.body_bold).pack(side=LEFT)

        pos = f"({ship.get('x', '?')},{ship.get('y', '?')},{ship.get('z', '?')})"
        Label(row1, text=pos, bg=bg, fg=pal.fg_secondary,
              font=fnt.small).pack(side=RIGHT, padx=(6, 0))

        status_txt = "💀" if not alive else ""
        if ship.get('is_phased'):
            status_txt = "👻 ФАЗА"
        if status_txt:
            st_fg = pal.accent_phase if ship.get('is_phased') else pal.fg_muted
            Label(row1, text=status_txt, bg=bg, fg=st_fg,
                  font=fnt.small_bold).pack(side=RIGHT, padx=4)

        # --- Row 2: HP bar -----------------------------------
        hp = max(0, ship.get('max_hits', 2) - ship.get('hits', 0))
        max_hp = ship.get('max_hits', 2)
        row2 = Frame(card, bg=bg)
        row2.pack(fill=X, pady=(2, 0))

        bar = self._render_hp_bar(row2, hp, max_hp, width=140, height=7)
        bar.pack(side=LEFT, padx=(0, 6))

        hp_txt = f"{hp}/{max_hp} HP"
        hp_clr = hp_color(hp, max_hp) if alive else pal.fg_muted
        Label(row2, text=hp_txt, bg=bg, fg=hp_clr,
              font=fnt.small_bold).pack(side=LEFT)

        if is_enemy:
            Label(row2, text=team, bg=bg, fg=team_clr,
                  font=fnt.small).pack(side=RIGHT)
            return card

        # --- Row 3: abilities (only for own ships) -----------
        row3 = Frame(card, bg=bg)
        row3.pack(fill=X, pady=(2, 0))

        pills = []
        if ship.get('can_shoot') and alive:
            sr = ship.get('shoot_range', 0)
            dmg = ship.get('damage', 1)
            lbl = f"🎯 dmg={dmg}"
            if sr > 0:
                lbl += f" r={sr}"
            if ship.get('shoot_anywhere'):
                lbl += " ∞"
            pills.append((lbl, pal.accent_warning))

        mr = ship.get('move_range', 0)
        if mr > 0 and alive:
            pills.append((f"🚶 move={mr}", pal.fg_secondary))

        jr = ship.get('jump_range', 0)
        if jr > 0 and alive:
            pills.append((f"🌀 jump={jr}", SHIP_TYPE_INFO.get("Прыгун", {}).get("accent", pal.accent_mine)))

        dr = ship.get('drill_range', 0)
        if dr > 0 and alive:
            pills.append((f"⚙ drill={dr}", SHIP_TYPE_INFO.get("Бурав", {}).get("accent", pal.accent_mine)))

        hr = ship.get('heal_range', 0)
        if hr > 0 and alive:
            pills.append((f"🔥 heal r={hr}", pal.accent_heal))

        if ship.get('can_phase') and alive:
            cd = ship.get('phase_cooldown', 0)
            if cd > 0:
                pills.append((f"👻 PHASE cd={cd}", pal.fg_muted))
            else:
                pills.append(("👻 PHASE", pal.accent_phase))

        if ship.get('can_place_mine') and alive:
            pills.append((f"🕷 мины (dmg={ship.get('mine_damage', 0)})", pal.accent_mine))

        if ship.get('can_create_hologram') and alive:
            pills.append(("🎭 голо", pal.accent_phase))

        if ship.get('scan_whole_z') and alive:
            pills.append(("📡 скан Z", pal.accent_info))

        for txt, clr in pills:
            Label(row3, text=txt, bg=bg, fg=clr,
                  font=fnt.small, padx=3).pack(side=LEFT)

        return card

    def create_ships_panel(self):
        """Панель со своими кораблями — карточки."""
        ships_frame = LabelFrame(self.root, text="🚀 ВАШИ КОРАБЛИ",
                                 bg=self.colors['panel'], fg=self.colors['accent3'],
                                 font=('Arial', 12, 'bold'))
        ships_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self._ships_canvas, self._ships_cards_inner = \
            self._make_scrollable_cards(ships_frame)
    
    def create_enemies_panel(self):
        """Панель с вражескими кораблями — карточки."""
        enemies_frame = LabelFrame(self.root, text="🎯 ОБНАРУЖЕННЫЕ ВРАГИ",
                                   bg=self.colors['panel'], fg=self.colors['accent2'],
                                   font=('Arial', 12, 'bold'))
        enemies_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self._enemies_canvas, self._enemies_cards_inner = \
            self._make_scrollable_cards(enemies_frame)
    
    def create_history_panel(self):
        """Журнал боя за всю партию (с иконками и цветными тегами)."""
        pal = Palette()
        fnt = Fonts()
        hist_frame = LabelFrame(
            self.root, text="📜 ЖУРНАЛ БОЯ",
            bg=pal.bg_panel, fg=pal.accent_info,
            font=fnt.h3,
        )
        hist_frame.pack(fill=BOTH, expand=False, padx=10, pady=5)

        inner = Frame(hist_frame, bg=pal.bg_panel)
        inner.pack(fill=BOTH, expand=True, padx=6, pady=4)

        self.history_text = Text(
            inner, height=8, bg=pal.bg_card, fg=pal.fg_primary,
            font=fnt.log, wrap=NONE, state=DISABLED,
            bd=0, highlightthickness=0, padx=6, pady=4,
        )
        self.history_text.pack(side=LEFT, fill=BOTH, expand=True)

        sb = ttk.Scrollbar(inner, orient=VERTICAL, command=self.history_text.yview)
        self.history_text.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y)

        # Настраиваем цветные теги.
        t = self.history_text
        t.tag_configure("turn_header", foreground=pal.accent_info,
                        font=fnt.small_bold, spacing1=6, spacing3=2)
        t.tag_configure("summary", foreground=pal.fg_secondary, font=fnt.small)
        t.tag_configure("A", foreground=TEAM_COLORS.get("Team A", pal.accent_info),
                        font=fnt.small_bold)
        t.tag_configure("B", foreground=TEAM_COLORS.get("Team B", pal.accent_danger),
                        font=fnt.small_bold)
        t.tag_configure("C", foreground=TEAM_COLORS.get("Team C", pal.accent_success),
                        font=fnt.small_bold)
        t.tag_configure("hit", foreground=pal.accent_warning)
        t.tag_configure("killed", foreground=pal.accent_danger, font=fnt.small_bold)
        t.tag_configure("ram", foreground=pal.accent_phase, font=fnt.small_bold)
        t.tag_configure("mine", foreground=pal.accent_mine, font=fnt.small_bold)
        t.tag_configure("holo", foreground=pal.accent_phase)
        t.tag_configure("muted", foreground=pal.fg_muted)
        t.tag_configure("empty", foreground=pal.fg_muted, justify="center")

    def _history_event_icon(self, ev):
        """Возвращает иконку-префикс по типу события."""
        if ev.get('type') == 'mine_detonated':
            return "🕷"
        if ev.get('type') == 'hologram_destroyed':
            return "🎭"
        if ev.get('ram') or ev.get('type') == 'ram_kill':
            return "💥"
        if ev.get('killed'):
            return "💀"
        return "🎯"

    def _short_team(self, team):
        """'Team A' -> 'A'. Возвращает одно-буквенную метку команды."""
        if not team:
            return "?"
        return team.replace("Team ", "").strip() or team[:1]

    def update_history(self, history):
        """Обновляет журнал боя с группировкой по ходам, цветами и итогами."""
        if not hasattr(self, 'history_text'):
            return
        t = self.history_text
        t.config(state=NORMAL)
        t.delete(1.0, END)

        if not history:
            t.insert(END, "\n   Событий пока нет — ждём первого хода.\n", "empty")
            t.config(state=DISABLED)
            return

        # Группируем по ходам.
        by_turn = {}
        for ev in history:
            by_turn.setdefault(ev.get('turn', 0), []).append(ev)

        for turn in sorted(by_turn.keys()):
            events = by_turn[turn]
            # Подсчёт урона/убийств по атакующей команде.
            dmg = {"A": 0, "B": 0, "C": 0}
            kills = {"A": 0, "B": 0, "C": 0}
            for ev in events:
                atk = self._short_team(ev.get('attacker') or ev.get('owner', ''))
                if atk in dmg:
                    dmg[atk] += ev.get('damage', 0) or 0
                    if ev.get('killed'):
                        kills[atk] += 1
            parts = []
            for k in ("A", "B", "C"):
                if dmg[k] or kills[k]:
                    piece = f"{k}:{dmg[k]}dmg"
                    if kills[k]:
                        piece += f"/{kills[k]}✖"
                    parts.append(piece)
            summary = "  •  ".join(parts) if parts else "нет попаданий"
            t.insert(END, f"── Ход {turn} ", "turn_header")
            t.insert(END, f"({summary})\n", "summary")

            for ev in events:
                icon = self._history_event_icon(ev)
                atk_team = self._short_team(ev.get('attacker') or ev.get('owner', ''))
                tgt_team = self._short_team(ev.get('target', ''))
                attacker_name = ev.get('attacker_name', '')
                target_name = ev.get('target_name', '')
                position = ev.get('position', '')
                damage = ev.get('damage', 0)
                killed = ev.get('killed', False)
                etype = ev.get('type') or ('ram_kill' if ev.get('ram') else 'hit')

                # Префикс-иконка + ход.
                t.insert(END, f"  {icon} ", "hit")
                # Атакующий (команда + имя), если есть.
                if ev.get('type') == 'mine_detonated':
                    t.insert(END, f"Мина({atk_team})", atk_team if atk_team in "ABC" else "muted")
                elif attacker_name:
                    if atk_team in "ABC":
                        t.insert(END, atk_team, atk_team)
                    t.insert(END, f" {attacker_name}", "hit" if not killed else "killed")
                # Разделитель.
                if etype == 'ram_kill' or ev.get('ram'):
                    t.insert(END, "  ⚡таран  ", "ram")
                elif ev.get('type') == 'hologram_destroyed':
                    t.insert(END, "  раскрыл  ", "holo")
                elif ev.get('type') == 'mine_detonated':
                    t.insert(END, " → ", "muted")
                else:
                    t.insert(END, " → ", "muted")
                # Цель.
                if ev.get('type') == 'hologram_destroyed':
                    t.insert(END, "голограмму ", "holo")
                    owner = self._short_team(ev.get('owner', ''))
                    if owner in "ABC":
                        t.insert(END, owner, owner)
                else:
                    if tgt_team in "ABC":
                        t.insert(END, tgt_team, tgt_team)
                    if target_name:
                        t.insert(END, f" {target_name}",
                                 "killed" if killed else "hit")
                # Позиция.
                if position:
                    t.insert(END, f"  @{position}", "muted")
                # Урон / отметка убийства.
                if damage:
                    t.insert(END, f"  −{damage}HP", "killed" if killed else "hit")
                if killed:
                    t.insert(END, "  ✖УБИТ", "killed")
                t.insert(END, "\n")

        t.see(END)
        t.config(state=DISABLED)

    def create_button_panel(self):
        """Панель с кнопками управления"""
        button_frame = Frame(self.root, bg=self.colors['panel'], height=60)
        button_frame.pack(fill=X, padx=10, pady=10)
        button_frame.pack_propagate(False)
        
        # Кнопки
        self.map_button = Button(button_frame, text="🗺️ КАРТА",
                                 bg=self.colors['accent1'], fg='black',
                                 font=('Arial', 11, 'bold'),
                                 command=self.show_map, state=DISABLED)
        self.map_button.pack(side=LEFT, padx=10)
        
        self.plan_button = Button(button_frame, text="📝 ПЛАНИРОВАТЬ",
                                  bg=self.colors['accent3'], fg='black',
                                  font=('Arial', 11, 'bold'),
                                  command=self.show_planning_window, state=DISABLED)
        self.plan_button.pack(side=LEFT, padx=10)
        
        self.send_button = Button(button_frame, text="🚀 ОТПРАВИТЬ",
                                   bg='orange', fg='black',
                                   font=('Arial', 11, 'bold'),
                                   command=self.send_actions, state=DISABLED)
        self.send_button.pack(side=LEFT, padx=10)
        
        Button(button_frame, text="🔄 ОБНОВИТЬ",
               bg='#9370DB', fg='white',
               font=('Arial', 11, 'bold'),
               command=self.request_update).pack(side=LEFT, padx=10)
        
        Button(button_frame, text="❌ ВЫХОД",
               bg='red', fg='white',
               font=('Arial', 11, 'bold'),
               command=self.root.quit).pack(side=RIGHT, padx=10)
        
        # Сообщения
        self.message_label = Label(button_frame, text="",
                                    bg=self.colors['panel'], fg=self.colors['accent1'],
                                    font=('Arial', 10))
        self.message_label.pack(side=RIGHT, padx=20)
    
    def show_connection_window(self):
        """Показывает окно подключения"""
        conn_window = Toplevel(self.root)
        conn_window.title("🚀 Подключение к игре")
        conn_window.geometry("500x500")
        conn_window.configure(bg=self.colors['bg'])
        conn_window.transient(self.root)
        conn_window.grab_set()
        
        # Заголовок
        title_font = font.Font(family='Arial', size=16, weight='bold')
        Label(conn_window, text="🚀 ПОДКЛЮЧЕНИЕ К СЕРВЕРУ",
              bg=self.colors['bg'], fg=self.colors['accent1'],
              font=title_font).pack(pady=20)
        
        # Рамка с полями
        input_frame = Frame(conn_window, bg=self.colors['panel'], bd=2, relief=RAISED)
        input_frame.pack(padx=30, pady=20, fill=BOTH, expand=True)
        
        # IP сервера
        Label(input_frame, text="🌐 IP сервера:",
              bg=self.colors['panel'], fg=self.colors['text'],
              font=('Arial', 11)).pack(anchor=W, padx=20, pady=(20,5))
        
        ip_entry = Entry(input_frame, width=30, font=('Arial', 11),
                         bg=self.colors['bg2'], fg='white',
                         insertbackground='white')
        ip_entry.insert(0, "localhost")
        ip_entry.pack(padx=20, pady=5, fill=X)
        
        # Имя игрока
        Label(input_frame, text="👤 Ваше имя:",
              bg=self.colors['panel'], fg=self.colors['text'],
              font=('Arial', 11)).pack(anchor=W, padx=20, pady=(20,5))
        
        name_entry = Entry(input_frame, width=30, font=('Arial', 11),
                           bg=self.colors['bg2'], fg='white',
                           insertbackground='white')
        default_name = f"Игрок_{int(time.time()) % 1000}"
        name_entry.insert(0, default_name)
        name_entry.pack(padx=20, pady=5, fill=X)
        
        # Выбор команды
        Label(input_frame, text="🎯 Выберите команду:",
              bg=self.colors['panel'], fg=self.colors['text'],
              font=('Arial', 11)).pack(anchor=W, padx=20, pady=(20,5))
        
        team_var = StringVar(value="1")
        
        # Команда A
        team_a_frame = Frame(input_frame, bg=self.colors['panel'])
        team_a_frame.pack(anchor=W, padx=30, pady=2)
        Radiobutton(team_a_frame, text="Team A (Синие)", variable=team_var, value="1",
                   bg=self.colors['panel'], fg='white',
                   selectcolor=self.colors['bg'],
                   activebackground=self.colors['panel']).pack(side=LEFT)
        Label(team_a_frame, text="🟦", bg=self.colors['panel'],
              fg='#4169E1', font=('Arial', 12)).pack(side=LEFT, padx=5)
        
        # Команда B
        team_b_frame = Frame(input_frame, bg=self.colors['panel'])
        team_b_frame.pack(anchor=W, padx=30, pady=2)
        Radiobutton(team_b_frame, text="Team B (Красные)", variable=team_var, value="2",
                   bg=self.colors['panel'], fg='white',
                   selectcolor=self.colors['bg'],
                   activebackground=self.colors['panel']).pack(side=LEFT)
        Label(team_b_frame, text="🟥", bg=self.colors['panel'],
              fg='#DC143C', font=('Arial', 12)).pack(side=LEFT, padx=5)
        
        # Команда C
        team_c_frame = Frame(input_frame, bg=self.colors['panel'])
        team_c_frame.pack(anchor=W, padx=30, pady=2)
        Radiobutton(team_c_frame, text="Team C (Зеленые)", variable=team_var, value="3",
                   bg=self.colors['panel'], fg='white',
                   selectcolor=self.colors['bg'],
                   activebackground=self.colors['panel']).pack(side=LEFT)
        Label(team_c_frame, text="🟩", bg=self.colors['panel'],
              fg='#228B22', font=('Arial', 12)).pack(side=LEFT, padx=5)
        
        # Кнопки
        button_frame = Frame(conn_window, bg=self.colors['bg'])
        button_frame.pack(pady=20)
        
        def connect():
            server_ip = ip_entry.get().strip()
            player_name = name_entry.get().strip()
            team_choice = team_var.get()
            
            if not server_ip:
                server_ip = "localhost"
            if not player_name:
                player_name = default_name
            
            if self.connect(server_ip, team_choice, player_name):
                conn_window.destroy()
                self.root.deiconify()
            else:
                messagebox.showerror("Ошибка", "Не удалось подключиться к серверу")
        
        Button(button_frame, text="🚀 ПОДКЛЮЧИТЬСЯ",
               bg=self.colors['accent3'], fg='black',
               font=('Arial', 12, 'bold'),
               command=connect).pack(side=LEFT, padx=10)
        
        Button(button_frame, text="❌ ОТМЕНА",
               bg='red', fg='white',
               font=('Arial', 12, 'bold'),
               command=self.root.quit).pack(side=LEFT, padx=10)
    
    def connect(self, server_ip, team_choice, player_name):
        try:
            self.player_name = player_name
            
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            self.socket.connect((server_ip, 5555))
            self.framed = Framed(self.socket)

            # Определяем команду
            if team_choice == "1":
                self.team = Team.TEAM_A
                team_display = "Team A (Синие)"
                team_color = self.team_colors['Team A']
            elif team_choice == "2":
                self.team = Team.TEAM_B
                team_display = "Team B (Красные)"
                team_color = self.team_colors['Team B']
            elif team_choice == "3":
                self.team = Team.TEAM_C
                team_display = "Team C (Зеленые)"
                team_color = self.team_colors['Team C']
            else:
                messagebox.showerror("Ошибка", "Неверный выбор команды")
                return False

            # Отправляем информацию о команде (явно указываем тип клиента).
            team_info = {
                'type': 'player',
                'team': self.team.value,
                'player_name': player_name,
            }
            self.framed.send(team_info)

            self.connected = True
            self.team_color = team_color
            
            # Обновляем интерфейс
            self.team_label.config(text=team_display, fg=team_color)
            self.player_label.config(text=player_name)
            self.status_label.config(text="✅ Подключен к серверу", fg=self.colors['accent3'])
            
            # Активируем кнопки
            self.map_button.config(state=NORMAL)
            self.plan_button.config(state=NORMAL)
            self.send_button.config(state=NORMAL)
            
            # Запускаем поток для получения данных
            self.receive_thread = threading.Thread(target=self.receive_loop, daemon=True)
            self.receive_thread.start()
            
            return True
            
        except Exception as e:
            messagebox.showerror("Ошибка подключения", f"Не удалось подключиться: {e}")
            return False
    
    def receive_loop(self):
        """Цикл получения данных от сервера. Использует framed-протокол,
        каждый вызов возвращает ровно одно сообщение или None по таймауту."""
        while self.connected:
            try:
                msg = self.framed.recv_once(timeout=1)
            except ProtocolError as e:
                if self.connected:
                    self.connected = False
                    self.root.after(
                        0,
                        lambda err=str(e): messagebox.showinfo(
                            "Соединение", f"Сервер закрыл соединение: {err}"
                        ),
                    )
                break
            except Exception as e:
                if self.connected:
                    # Не выводим в stdout — его пользователь не видит.
                    err_text = f"Ошибка получения данных: {e}"
                    self.root.after(
                        0,
                        lambda t=err_text: self.status_label.config(
                            text=t, fg=self.colors['accent2']
                        ),
                    )
                    time.sleep(1)
                continue

            if msg is None:
                continue

            # Сервер может прислать reject в handshake.
            if isinstance(msg, dict) and msg.get('type') == 'reject':
                reason = msg.get('reason', 'Сервер отклонил подключение')
                self.connected = False
                self.root.after(
                    0,
                    lambda r=reason: messagebox.showerror("Отказ сервера", r),
                )
                break

            self.current_state = msg
            self.root.after(0, self.update_interface, msg)
    
    def update_interface(self, state):
        """Обновляет интерфейс на основе полученного состояния"""
        turn = state.get('turn', 0) + 1
        phase = state.get('phase', 'unknown')
        message = state.get('message', '')
        game_over = state.get('game_over', False)
        
        # Обновляем заголовки
        self.turn_label.config(text=str(turn))
        
        # Цвет фазы
        if phase == 'planning':
            self.phase_label.config(text="📝 ПЛАНИРОВАНИЕ", fg=self.colors['accent3'])
        elif phase == 'results':
            self.phase_label.config(text="📊 РЕЗУЛЬТАТЫ", fg='orange')
        else:
            self.phase_label.config(text=phase.upper(), fg='gray')
        
        # Проверяем наличие радиовышек
        radio_ships = []
        for ship_id, ship in state.get('my_ships', {}).items():
            if ship.get('type') == 'Радиовышка' and ship.get('alive'):
                radio_ships.append(ship)
        
        if radio_ships:
            layers = [f"Z={ship['z']}" for ship in radio_ships]
            self.message_label.config(
                text=f"📡 Радиовышка сканирует: {', '.join(layers)}",
                fg=self.colors['accent1']
            )
        else:
            self.message_label.config(text=message, fg='white')
        
        if game_over:
            winner = state.get('winner', 'Не определен')
            if winner == self.team.value:
                self.message_label.config(text="🏆 ПОБЕДА! ВЫ ВЫИГРАЛИ!", fg=self.colors['accent4'])
            else:
                self.message_label.config(text=f"Игра окончена. Победитель: {winner}", fg='gray')
            self.plan_button.config(state=DISABLED)
            self.send_button.config(state=DISABLED)
        else:
            # Активируем кнопки в фазе планирования
            if phase == 'planning':
                self.plan_button.config(state=NORMAL, bg=self.colors['accent3'])
                self.send_button.config(state=NORMAL, bg='orange')
            else:
                self.plan_button.config(state=DISABLED, bg='gray')
                self.send_button.config(state=DISABLED, bg='gray')
        
        # Обновляем список кораблей
        self.update_ships_list(state)
        
        # Обновляем список врагов
        self.update_enemies_list(state)
        
        # Обновляем карту, если она открыта
        if self.map_window and self.map_window.window.winfo_exists():
            self.map_window.update_data(
                state.get('my_ships', {}),
                state.get('visible_enemies', {})
            )

        # Журнал попаданий (cumulative за всю партию).
        self.update_history(state.get('hit_history', []))

        # Плашки команд в HUD.
        self._update_team_pills(state)

        # Визуальный таймер фазы планирования — приходит через poll_timer().
        self._render_timer_from_state(state)

    def _update_team_pills(self, state):
        """Обновляет плашки команд в HUD по данным state."""
        if not hasattr(self, "_team_pill_widgets"):
            return
        pal = Palette()

        my_team = state.get("team", "")
        my_ships = state.get("my_ships", {}) or {}
        vis_enemies = state.get("visible_enemies", {}) or {}
        history = state.get("hit_history", []) or []

        # Считаем урон и киллы по атакующим командам из hit_history.
        dmg_by = {"Team A": 0, "Team B": 0, "Team C": 0}
        kill_by = {"Team A": 0, "Team B": 0, "Team C": 0}
        for ev in history:
            atk = ev.get("attacker") or ev.get("owner")  # мина/голо — owner.
            dmg = ev.get("damage") or 0
            if atk in dmg_by:
                dmg_by[atk] += dmg
                if ev.get("killed"):
                    kill_by[atk] += 1

        # Живые по командам: свою знаем, чужие — только по visible_enemies
        # (+ сколько уже гарантированно убито из kill_by).
        alive_my = sum(1 for s in my_ships.values() if s.get("alive"))
        total_my = len(my_ships) if my_ships else 8

        enemy_alive_seen = {"Team A": 0, "Team B": 0, "Team C": 0}
        for s in vis_enemies.values():
            if s.get("alive"):
                t = s.get("team")
                if t in enemy_alive_seen:
                    enemy_alive_seen[t] += 1

        for team_name, widgets in self._team_pill_widgets.items():
            color = TEAM_COLORS.get(team_name, pal.fg_primary)
            if team_name == my_team:
                widgets["alive"].config(text=f"{alive_my}/{total_my}",
                                        fg=color)
            else:
                seen = enemy_alive_seen.get(team_name, 0)
                # Нижняя оценка: видим `seen`, и убитые (не путать с видимыми
                # вражескими живыми) учтены как убитые.
                widgets["alive"].config(
                    text=f"{seen}+?", fg=pal.fg_primary,
                )
            widgets["dmg"].config(text=f"⚡{dmg_by.get(team_name, 0)}")
            widgets["kill"].config(text=f"✖{kill_by.get(team_name, 0)}")

            # Подсветка своей команды: сделаем имя ярче.
            if team_name == my_team:
                widgets["name"].config(fg=color, font=Fonts().body_bold)
                widgets["frame"].config(
                    highlightbackground=color, highlightthickness=2,
                )
            else:
                widgets["frame"].config(highlightthickness=0)
    
    def update_ships_list(self, state):
        """Обновляет карточки своих кораблей."""
        if not hasattr(self, '_ships_cards_inner'):
            return
        for w in self._ships_cards_inner.winfo_children():
            w.destroy()
        ships = state.get('my_ships', {})
        # Сперва живые, потом мёртвые (группами, отсортированы по ID).
        sorted_items = sorted(
            ships.items(),
            key=lambda kv: (not kv[1].get('alive', True), kv[0]),
        )
        for ship_id, ship in sorted_items:
            self._render_ship_card(self._ships_cards_inner, ship, ship_id,
                                   is_enemy=False)

    def update_enemies_list(self, state):
        """Обновляет карточки обнаруженных врагов."""
        if not hasattr(self, '_enemies_cards_inner'):
            return
        pal = Palette()
        for w in self._enemies_cards_inner.winfo_children():
            w.destroy()
        enemies = state.get('visible_enemies', {}) or {}
        visible_alive = {sid: s for sid, s in enemies.items() if s.get('alive')}
        if not visible_alive:
            Label(
                self._enemies_cards_inner,
                text="👁️ Врагов не обнаружено",
                bg=pal.bg_panel, fg=pal.fg_secondary,
                font=Fonts().body, pady=20,
            ).pack(fill=X)
            return
        for ship_id, ship in sorted(visible_alive.items()):
            self._render_ship_card(self._enemies_cards_inner, ship, ship_id,
                                   is_enemy=True)
    
    def _render_timer_from_state(self, state):
        """Форматирует текст таймера из state (deadline + counts)."""
        if state is None:
            self.timer_label.config(text="")
            return
        phase = state.get('phase')
        if phase != 'planning':
            self.timer_label.config(text="")
            return
        deadline = state.get('planning_deadline')
        if not deadline:
            self.timer_label.config(text="")
            return
        remaining = max(0, int(deadline - time.time()))
        self.timer_label.config(
            text=f"⏱ {remaining}с до конца фазы планирования",
            fg=(self.colors['accent2'] if remaining <= 10 else self.colors['accent4']),
        )

    def poll_timer(self):
        """Каждые 0.5с пересчитывает таймер из current_state."""
        try:
            self._render_timer_from_state(self.current_state)
        finally:
            self.root.after(500, self.poll_timer)

    # --- Клик по карте → запись координат в форму планирования ------------

    def _legal_cells_for(self, ship, kind):
        """Возвращает множество (x,y,z) легальных клеток для хода или
        выстрела конкретного корабля по его типу/диапазону.

        Баланс v7 / Devin Review #1: повторяет правила сервера из
        ``_execute_move``. Для Прыгуна авторитетен ``jump_range`` (кулдаун
        применяется отдельно на сервере, но для подсветки берём как верх).
        Для Бурава эффективная дальность — ``max(move_range, drill_range)``,
        а перемещение должно быть либо по одной оси, либо строго по
        двум осям с равным модулем смещения (диагональ в плоскости)."""
        cells = set()
        sx, sy, sz = ship['x'], ship['y'], ship['z']
        if kind == 'move':
            ship_type = ship.get('ship_type') or ship.get('type')
            jump_range = ship.get('jump_range', 0) or 0
            drill_range = ship.get('drill_range', 0) or 0
            move_range = ship.get('move_range', 1) or 0
            is_jumper = ship_type == 'Прыгун' and jump_range > 0
            is_drill = ship_type == 'Бурав' and drill_range > 0
            if is_jumper:
                effective_range = jump_range
            elif is_drill:
                effective_range = max(move_range, drill_range)
            else:
                effective_range = move_range
            if effective_range <= 0:
                return cells
            for dx in range(-effective_range, effective_range + 1):
                for dy in range(-effective_range, effective_range + 1):
                    for dz in range(-effective_range, effective_range + 1):
                        if dx == 0 and dy == 0 and dz == 0:
                            continue
                        if max(abs(dx), abs(dy), abs(dz)) > effective_range:
                            continue
                        if is_drill:
                            nonzero = [v for v in (dx, dy, dz) if v != 0]
                            axes = len(nonzero)
                            if axes == 3:
                                continue
                            if axes == 2 and abs(nonzero[0]) != abs(nonzero[1]):
                                continue
                        nx, ny, nz = sx + dx, sy + dy, sz + dz
                        if 0 <= nx < 10 and 0 <= ny < 10 and 0 <= nz < 10:
                            cells.add((nx, ny, nz))
            return cells
        if kind == 'shoot':
            sr = ship.get('shoot_range', 0)
            if sr <= 0:
                return cells
            if ship.get('shoot_anywhere'):
                # Артиллерия: любая клетка куба в пределах sr по каждой оси.
                for x in range(10):
                    for y in range(10):
                        for z in range(10):
                            if (abs(x - sx) <= sr and abs(y - sy) <= sr
                                    and abs(z - sz) <= sr):
                                cells.add((x, y, z))
                return cells
            # Обычные: стрельба по прямой вдоль одной оси (остальные оси
            # совпадают с позицией корабля), дистанция >= 1 и <= shoot_range.
            for d in range(1, sr + 1):
                for (dx, dy, dz) in (
                    (d, 0, 0), (-d, 0, 0),
                    (0, d, 0), (0, -d, 0),
                    (0, 0, d), (0, 0, -d),
                ):
                    nx, ny, nz = sx + dx, sy + dy, sz + dz
                    if 0 <= nx < 10 and 0 <= ny < 10 and 0 <= nz < 10:
                        cells.add((nx, ny, nz))
            return cells
        return cells

    def start_target_capture(self, ship, kind, vars_tuple, status_label=None):
        """Активирует режим выбора цели на карте для данного корабля.

        Открывает окно карты, подсвечивает легальные клетки. Клик по клетке
        попадёт в on_map_click и запишет координаты в vars_tuple.
        """
        legal = self._legal_cells_for(ship, kind)
        self.target_capture = {
            'ship': ship,
            'kind': kind,
            'vars': vars_tuple,
            'legal': legal,
            'status_label': status_label,
        }
        self.show_map()
        if self.map_window:
            self.map_window.set_targeting(legal_cells=legal, mode=kind)
            # Переключимся на Z корабля для удобства.
            self.map_window.current_layer.set(ship['z'])
        if status_label is not None:
            status_label.config(text="👉 Кликните по карте, чтобы выбрать цель")

    def on_map_click(self, x, y, z):
        """Обработчик клика по клетке карты в режиме выбора цели."""
        cap = self.target_capture
        if cap is None:
            return
        if (x, y, z) not in cap['legal']:
            if self.map_window:
                self.map_window.set_targeting(
                    legal_cells=cap['legal'], selected=None, mode=cap['kind']
                )
            messagebox.showinfo(
                "Недоступная клетка",
                "Эта клетка недоступна для выбранного действия.\n"
                "Подсвеченные рамкой клетки — легальные.",
            )
            return
        x_var, y_var, z_var = cap['vars']
        x_var.set(str(x)); y_var.set(str(y)); z_var.set(str(z))
        if self.map_window:
            self.map_window.set_targeting(
                legal_cells=cap['legal'], selected=(x, y, z), mode=cap['kind']
            )
        status_label = cap.get('status_label')
        if status_label is not None:
            status_label.config(text=f"✅ Выбрано: ({x}, {y}, {z})")
        self.target_capture = None

    def show_map(self):
        """Показывает окно с картой"""
        if not self.current_state:
            messagebox.showinfo("Информация", "Нет данных для отображения карты")
            return
        
        if self.map_window and self.map_window.window.winfo_exists():
            self.map_window.window.lift()
            self.map_window.window.focus()
        else:
            self.map_window = MapWindow(
                self.root, self.team.value, self.team_color, gui=self
            )
            self.map_window.update_data(
                self.current_state.get('my_ships', {}),
                self.current_state.get('visible_enemies', {})
            )
    
    def show_planning_window(self):
        """Показывает окно планирования действий"""
        if not self.current_state or self.current_state.get('phase') != 'planning':
            messagebox.showinfo("Информация", "Сейчас не фаза планирования")
            return
        
        ships = self.current_state.get('my_ships', {})
        alive_ships = [s for s in ships.values() if s['alive']]
        
        if not alive_ships:
            messagebox.showinfo("Информация", "У вас не осталось живых кораблей")
            return
        
        planning_window = Toplevel(self.root)
        planning_window.title("📝 Планирование действий")
        planning_window.geometry("700x800")
        planning_window.configure(bg=self.colors['bg'])
        planning_window.transient(self.root)
        
        # Заголовок
        title_frame = Frame(planning_window, bg='#000000', height=60)
        title_frame.pack(fill=X)
        title_frame.pack_propagate(False)
        
        Label(title_frame, text="📝 ПЛАНИРОВАНИЕ ДЕЙСТВИЙ",
              bg='#000000', fg=self.colors['accent4'],
              font=('Arial', 16, 'bold')).pack(expand=True)
        
        # Правила
        rules_frame = Frame(planning_window, bg=self.colors['panel'])
        rules_frame.pack(fill=X, padx=10, pady=10)
        
        Label(rules_frame, text="📋 ПРАВИЛА:",
              bg=self.colors['panel'], fg=self.colors['accent1'],
              font=('Arial', 11, 'bold')).pack(anchor=W, padx=10, pady=5)
        
        rules_text = """• 🚀 Перемещение: на 1 клетку в любом направлении (Прыгун — 3, Бурав — 3 по прямой, Факел/Базовый — 1)
• 🎯 Стрельба: обычные корабли — по прямой до 5 клеток; Артиллерия — куда угодно (2 урона)
• 📡 Радиовышка: не стреляет, сканирует весь слой Z
• 💚 Факел: лечит союзников в радиусе 1 (действие HEAL — без цели)
• 🌀 Тишина: вкл/выкл фазу (неуязвимость и невидимость), действие PHASE
• 🪞 Провокатор: ставит голограмму (декой) в соседнюю клетку
• 💣 Паук: ставит мину (2 урона врагу при входе) в соседнюю клетку"""
        
        Label(rules_frame, text=rules_text, bg=self.colors['panel'],
              fg='white', font=('Arial', 9), justify=LEFT).pack(anchor=W, padx=20, pady=5)
        
        # Прокручиваемый фрейм для кораблей
        canvas = Canvas(planning_window, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = Scrollbar(planning_window, orient=VERTICAL, command=canvas.yview)
        scrollable_frame = Frame(canvas, bg=self.colors['bg'])
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Список действий
        self.actions = []
        
        # Для каждого корабля
        for i, ship in enumerate(alive_ships, 1):
            # Создаем фрейм для корабля
            ship_frame = LabelFrame(scrollable_frame,
                                    text=f"🚀 КОРАБЛЬ {i}: {ship['name']}",
                                    bg=self.colors['panel'], fg=self.team_color,
                                    font=('Arial', 11, 'bold'))
            ship_frame.pack(fill=X, padx=10, pady=5, ipady=5)
            
            # Информация о корабле
            info_frame = Frame(ship_frame, bg=self.colors['panel'])
            info_frame.pack(fill=X, padx=10, pady=5)
            
            pos_text = f"📍 Позиция: ({ship['x']}, {ship['y']}, {ship['z']})"
            hits_text = f"💥 Попадания: {ship['hits']}/{ship.get('max_hits', 2)}"
            
            Label(info_frame, text=pos_text, bg=self.colors['panel'],
                  fg='white').pack(anchor=W)
            Label(info_frame, text=hits_text, bg=self.colors['panel'],
                  fg='orange' if ship['hits'] > 0 else 'white').pack(anchor=W)
            
            # Проверяем тип корабля
            ship_type = ship.get('type', 'Базовый')
            can_move = ship_type != 'Артиллерия'
            can_shoot = ship_type != 'Радиовышка'
            
            # Выбор действия
            action_var = StringVar(value="none")
            
            # Пропустить ход
            none_frame = Frame(ship_frame, bg=self.colors['panel'])
            none_frame.pack(anchor=W, padx=20, pady=2)
            Radiobutton(none_frame, text="⏭️ Пропустить ход",
                       variable=action_var, value="none",
                       bg=self.colors['panel'], fg='white',
                       selectcolor=self.colors['bg'],
                       activebackground=self.colors['panel']).pack(side=LEFT)
            
            # Переменные для координат
            move_x_var = StringVar(value=str(ship['x']))
            move_y_var = StringVar(value=str(ship['y']))
            move_z_var = StringVar(value=str(ship['z']))
            
            shoot_x_var = StringVar(value=str(ship['x']))
            shoot_y_var = StringVar(value=str(ship['y']))
            shoot_z_var = StringVar(value=str(ship['z']))
            
            # Перемещение
            if can_move:
                move_frame = Frame(ship_frame, bg=self.colors['panel'])
                move_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                
                Radiobutton(move_frame, text="🚀 Переместиться в:",
                           variable=action_var, value="move",
                           bg=self.colors['panel'], fg='white',
                           selectcolor=self.colors['bg'],
                           activebackground=self.colors['panel']).pack(side=LEFT)
                
                coord_frame = Frame(move_frame, bg=self.colors['panel'])
                coord_frame.pack(side=LEFT, padx=10)
                
                Entry(coord_frame, textvariable=move_x_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(coord_frame, textvariable=move_y_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(coord_frame, textvariable=move_z_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)

                move_status = Label(
                    move_frame, text="", bg=self.colors['panel'],
                    fg=self.colors['accent3'], font=('Arial', 9),
                )
                move_status.pack(side=LEFT, padx=8)
                Button(
                    move_frame, text="🗺 Выбрать на карте",
                    bg=self.colors['accent1'], fg='black',
                    font=('Arial', 9, 'bold'),
                    command=lambda s=ship, mv=(move_x_var, move_y_var, move_z_var),
                                   lbl=move_status:
                        self.start_target_capture(s, 'move', mv, lbl),
                ).pack(side=LEFT, padx=5)
            else:
                Label(ship_frame, text="⚠️ Артиллерия не может двигаться",
                     bg=self.colors['panel'], fg='orange',
                     font=('Arial', 9)).pack(anchor=W, padx=20, pady=2)
            
            # Выстрел
            if can_shoot:
                shoot_frame = Frame(ship_frame, bg=self.colors['panel'])
                shoot_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                
                if ship_type == 'Артиллерия':
                    Radiobutton(shoot_frame, text="💥 Выстрелить в любую точку:",
                               variable=action_var, value="shoot",
                               bg=self.colors['panel'], fg='white',
                               selectcolor=self.colors['bg'],
                               activebackground=self.colors['panel']).pack(side=LEFT)
                else:
                    Radiobutton(shoot_frame, text="🎯 Выстрелить в:",
                               variable=action_var, value="shoot",
                               bg=self.colors['panel'], fg='white',
                               selectcolor=self.colors['bg'],
                               activebackground=self.colors['panel']).pack(side=LEFT)
                
                coord_frame = Frame(shoot_frame, bg=self.colors['panel'])
                coord_frame.pack(side=LEFT, padx=10)
                
                Entry(coord_frame, textvariable=shoot_x_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(coord_frame, textvariable=shoot_y_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(coord_frame, textvariable=shoot_z_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)

                shoot_status = Label(
                    shoot_frame, text="", bg=self.colors['panel'],
                    fg=self.colors['accent4'], font=('Arial', 9),
                )
                shoot_status.pack(side=LEFT, padx=8)
                Button(
                    shoot_frame, text="🗺 Выбрать на карте",
                    bg=self.colors['accent1'], fg='black',
                    font=('Arial', 9, 'bold'),
                    command=lambda s=ship, sv=(shoot_x_var, shoot_y_var, shoot_z_var),
                                   lbl=shoot_status:
                        self.start_target_capture(s, 'shoot', sv, lbl),
                ).pack(side=LEFT, padx=5)
            else:
                Label(ship_frame, text="📡 Радиовышка не может стрелять",
                     bg=self.colors['panel'], fg='#00d4ff',
                     font=('Arial', 9)).pack(anchor=W, padx=20, pady=2)

            # ======= Способности необычных типов (advanced-режим) =======
            # Флаги берём из ship-dict (сервер их теперь пробрасывает).
            heal_range = ship.get('heal_range', 0) or 0
            can_phase = bool(ship.get('can_phase', False))
            can_create_hologram = bool(ship.get('can_create_hologram', False))
            can_place_mine = bool(ship.get('can_place_mine', False))

            # Координаты для HOLOGRAM/MINE (соседняя клетка).
            holo_x_var = StringVar(value=str(ship['x']))
            holo_y_var = StringVar(value=str(ship['y']))
            holo_z_var = StringVar(value=str(ship['z']))
            mine_x_var = StringVar(value=str(ship['x']))
            mine_y_var = StringVar(value=str(ship['y']))
            mine_z_var = StringVar(value=str(ship['z']))

            if heal_range > 0:
                heal_frame = Frame(ship_frame, bg=self.colors['panel'])
                heal_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                Radiobutton(
                    heal_frame,
                    text=f"💚 Лечить союзников в радиусе {heal_range} (HEAL)",
                    variable=action_var, value="heal",
                    bg=self.colors['panel'], fg='#6bff6b',
                    selectcolor=self.colors['bg'],
                    activebackground=self.colors['panel'],
                ).pack(side=LEFT)

            if can_phase:
                phase_frame = Frame(ship_frame, bg=self.colors['panel'])
                phase_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                phase_now = bool(ship.get('is_phased', False))
                phase_state = "выйти из фазы" if phase_now else "войти в фазу"
                Radiobutton(
                    phase_frame,
                    text=f"🌀 Переключить фазу — {phase_state} (PHASE)",
                    variable=action_var, value="phase",
                    bg=self.colors['panel'], fg='#c0a8ff',
                    selectcolor=self.colors['bg'],
                    activebackground=self.colors['panel'],
                ).pack(side=LEFT)

            if can_create_hologram:
                holo_frame = Frame(ship_frame, bg=self.colors['panel'])
                holo_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                Radiobutton(
                    holo_frame,
                    text="🪞 Создать голограмму в:",
                    variable=action_var, value="hologram",
                    bg=self.colors['panel'], fg='#ffd0ff',
                    selectcolor=self.colors['bg'],
                    activebackground=self.colors['panel'],
                ).pack(side=LEFT)
                hcoord = Frame(holo_frame, bg=self.colors['panel'])
                hcoord.pack(side=LEFT, padx=10)
                Entry(hcoord, textvariable=holo_x_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(hcoord, textvariable=holo_y_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(hcoord, textvariable=holo_z_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                holo_status = Label(
                    holo_frame, text="", bg=self.colors['panel'],
                    fg=self.colors['accent4'], font=('Arial', 9),
                )
                holo_status.pack(side=LEFT, padx=8)
                Button(
                    holo_frame, text="🗺 На карте",
                    bg=self.colors['accent1'], fg='black',
                    font=('Arial', 9, 'bold'),
                    command=lambda s=ship, hv=(holo_x_var, holo_y_var, holo_z_var),
                                   lbl=holo_status:
                        self.start_target_capture(s, 'hologram', hv, lbl),
                ).pack(side=LEFT, padx=5)

            if can_place_mine:
                mine_frame = Frame(ship_frame, bg=self.colors['panel'])
                mine_frame.pack(anchor=W, padx=20, pady=5, fill=X)
                Radiobutton(
                    mine_frame,
                    text="💣 Поставить мину в:",
                    variable=action_var, value="mine",
                    bg=self.colors['panel'], fg='#ff8888',
                    selectcolor=self.colors['bg'],
                    activebackground=self.colors['panel'],
                ).pack(side=LEFT)
                mcoord = Frame(mine_frame, bg=self.colors['panel'])
                mcoord.pack(side=LEFT, padx=10)
                Entry(mcoord, textvariable=mine_x_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(mcoord, textvariable=mine_y_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                Entry(mcoord, textvariable=mine_z_var, width=3,
                      bg=self.colors['bg2'], fg='white',
                      insertbackground='white').pack(side=LEFT, padx=1)
                mine_status = Label(
                    mine_frame, text="", bg=self.colors['panel'],
                    fg=self.colors['accent4'], font=('Arial', 9),
                )
                mine_status.pack(side=LEFT, padx=8)
                Button(
                    mine_frame, text="🗺 На карте",
                    bg=self.colors['accent1'], fg='black',
                    font=('Arial', 9, 'bold'),
                    command=lambda s=ship, mv=(mine_x_var, mine_y_var, mine_z_var),
                                   lbl=mine_status:
                        self.start_target_capture(s, 'mine', mv, lbl),
                ).pack(side=LEFT, padx=5)

            # Сохраняем данные для этого корабля
            ship_data = {
                'ship_id': ship['id'],
                'ship_name': ship['name'],
                'ship_type': ship_type,
                'ship_x': ship['x'],
                'ship_y': ship['y'],
                'ship_z': ship['z'],
                'action_var': action_var,
                'move_x': move_x_var,
                'move_y': move_y_var,
                'move_z': move_z_var,
                'shoot_x': shoot_x_var if can_shoot else None,
                'shoot_y': shoot_y_var if can_shoot else None,
                'shoot_z': shoot_z_var if can_shoot else None,
                'holo_x': holo_x_var,
                'holo_y': holo_y_var,
                'holo_z': holo_z_var,
                'mine_x': mine_x_var,
                'mine_y': mine_y_var,
                'mine_z': mine_z_var,
                'can_move': can_move,
                'can_shoot': can_shoot,
                'heal_range': heal_range,
                'can_phase': can_phase,
                'can_create_hologram': can_create_hologram,
                'can_place_mine': can_place_mine,
                # Эффективная дальность перемещения (Прыгун/Бурав > 1).
                'effective_move_range': max(
                    int(ship.get('move_range', 1) or 0),
                    int(ship.get('jump_range', 0) or 0),
                    int(ship.get('drill_range', 0) or 0),
                ) or 1,
                'is_drill': ship_type == 'Бурав' or ship.get('drill_range', 0) > 0,
            }
            
            # Кнопка сохранения
            Button(ship_frame, text="💾 СОХРАНИТЬ ДЕЙСТВИЕ",
                   bg=self.colors['accent3'], fg='black',
                   font=('Arial', 10, 'bold'),
                   command=lambda data=ship_data: self.save_ship_action(data)).pack(pady=10)
        
        # Кнопки внизу
        button_frame = Frame(planning_window, bg=self.colors['bg'])
        button_frame.pack(fill=X, pady=10)
        
        def send_all_actions():
            if not self.actions:
                messagebox.showwarning("Внимание", "Не сохранено ни одного действия")
                return
            
            planning_window.destroy()
            self.send_actions()
        
        Button(button_frame, text="🚀 ОТПРАВИТЬ ВСЕ ДЕЙСТВИЯ",
               bg=self.colors['accent3'], fg='black',
               font=('Arial', 12, 'bold'),
               command=send_all_actions).pack(side=LEFT, padx=10)
        
        Button(button_frame, text="❌ ЗАКРЫТЬ",
               bg='red', fg='white',
               font=('Arial', 12, 'bold'),
               command=planning_window.destroy).pack(side=RIGHT, padx=10)
        
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
    
    def save_ship_action(self, ship_data):
        """Сохраняет действие для корабля"""
        try:
            action_type = ship_data['action_var'].get()
            ship_id = ship_data['ship_id']
            ship_name = ship_data['ship_name']
            
            if action_type == "none":
                # Удаляем существующее действие
                self.actions = [a for a in self.actions if a.ship_id != ship_id]
                self.status_label.config(
                    text=f"⏭️ Действие для {ship_name} удалено",
                    fg=self.colors['accent4'],
                )
                return
            
            if action_type == "move":
                # Проверяем, может ли корабль двигаться
                if not ship_data['can_move']:
                    messagebox.showerror("Ошибка", "Этот корабль не может двигаться!")
                    return
                
                x = int(ship_data['move_x'].get())
                y = int(ship_data['move_y'].get())
                z = int(ship_data['move_z'].get())
                
                # Проверка: эффективная дальность перемещения зависит от типа.
                # Прыгун — 3, Бурав — 3, Факел/Базовый — 1.
                dx = abs(x - ship_data['ship_x'])
                dy = abs(y - ship_data['ship_y'])
                dz = abs(z - ship_data['ship_z'])

                max_dist = ship_data.get('effective_move_range', 1)
                if max(dx, dy, dz) > max_dist:
                    messagebox.showerror(
                        "Ошибка",
                        f"Этот корабль перемещается максимум на {max_dist} клеток!",
                    )
                    return
                if ship_data.get('is_drill'):
                    axes_changed = (1 if dx else 0) + (1 if dy else 0) + (1 if dz else 0)
                    if axes_changed != 1:
                        messagebox.showerror(
                            "Ошибка",
                            "Бурав двигается только по одной оси (прямая линия)!",
                        )
                        return
                
                # Проверка границ
                if x < 0 or x > 9 or y < 0 or y > 9 or z < 0 or z > 9:
                    messagebox.showerror("Ошибка", "Координаты должны быть от 0 до 9!")
                    return
                
                action = Action(
                    ship_id=ship_id,
                    action_type=ActionType.MOVE,
                    target_x=x,
                    target_y=y,
                    target_z=z
                )
            
            elif action_type == "shoot":
                # Проверяем, может ли корабль стрелять
                if not ship_data['can_shoot']:
                    messagebox.showerror("Ошибка", "Этот корабль не может стрелять!")
                    return
                
                # Получаем координаты выстрела
                if ship_data['shoot_x'] is not None:
                    x = int(ship_data['shoot_x'].get())
                else:
                    x = ship_data['ship_x']
                    
                if ship_data['shoot_y'] is not None:
                    y = int(ship_data['shoot_y'].get())
                else:
                    y = ship_data['ship_y']
                    
                if ship_data['shoot_z'] is not None:
                    z = int(ship_data['shoot_z'].get())
                else:
                    z = ship_data['ship_z']
                
                # Проверка границ
                if x < 0 or x > 9 or y < 0 or y > 9 or z < 0 or z > 9:
                    messagebox.showerror("Ошибка", "Координаты должны быть от 0 до 9!")
                    return
                
                # Для артиллерии - любые координаты
                if ship_data['ship_type'] == 'Артиллерия':
                    # Проверка дальности (макс 10 клеток)
                    dx = abs(x - ship_data['ship_x'])
                    dy = abs(y - ship_data['ship_y'])
                    dz = abs(z - ship_data['ship_z'])
                    
                    if dx > 10 or dy > 10 or dz > 10:
                        messagebox.showerror("Ошибка", "Дальность стрельбы не более 10 клеток!")
                        return
                else:
                    # Для обычных кораблей - только по одной оси
                    changed_axes = 0
                    if x != ship_data['ship_x']: changed_axes += 1
                    if y != ship_data['ship_y']: changed_axes += 1
                    if z != ship_data['ship_z']: changed_axes += 1
                    
                    if changed_axes != 1:
                        messagebox.showerror("Ошибка", "Можно стрелять только по одной оси!")
                        return
                    
                    # Проверка дальности (максимум 5 клеток)
                    distance = max(
                        abs(x - ship_data['ship_x']), 
                        abs(y - ship_data['ship_y']), 
                        abs(z - ship_data['ship_z'])
                    )
                    if distance > 5:
                        messagebox.showerror("Ошибка", "Дальность стрельбы не более 5 клеток!")
                        return
                
                action = Action(
                    ship_id=ship_id,
                    action_type=ActionType.SHOOT,
                    target_x=x,
                    target_y=y,
                    target_z=z
                )

            elif action_type == "heal":
                if not ship_data.get('heal_range', 0):
                    messagebox.showerror("Ошибка", "Этот корабль не умеет лечить!")
                    return
                action = Action(
                    ship_id=ship_id,
                    action_type=ActionType.HEAL,
                )

            elif action_type == "phase":
                if not ship_data.get('can_phase'):
                    messagebox.showerror("Ошибка", "Этот корабль не умеет уходить в фазу!")
                    return
                action = Action(
                    ship_id=ship_id,
                    action_type=ActionType.PHASE,
                )

            elif action_type in ("hologram", "mine"):
                if action_type == "hologram":
                    if not ship_data.get('can_create_hologram'):
                        messagebox.showerror("Ошибка", "Этот корабль не умеет создавать голограммы!")
                        return
                    x = int(ship_data['holo_x'].get())
                    y = int(ship_data['holo_y'].get())
                    z = int(ship_data['holo_z'].get())
                    at = ActionType.HOLOGRAM
                    label = "голограмму"
                else:
                    if not ship_data.get('can_place_mine'):
                        messagebox.showerror("Ошибка", "Этот корабль не умеет ставить мины!")
                        return
                    x = int(ship_data['mine_x'].get())
                    y = int(ship_data['mine_y'].get())
                    z = int(ship_data['mine_z'].get())
                    at = ActionType.MINE
                    label = "мину"

                if not (0 <= x <= 9 and 0 <= y <= 9 and 0 <= z <= 9):
                    messagebox.showerror("Ошибка", "Координаты должны быть от 0 до 9!")
                    return
                dx = abs(x - ship_data['ship_x'])
                dy = abs(y - ship_data['ship_y'])
                dz = abs(z - ship_data['ship_z'])
                dist = max(dx, dy, dz)
                if dist == 0 or dist > 1:
                    messagebox.showerror(
                        "Ошибка",
                        f"Можно поставить {label} только в СОСЕДНЮЮ клетку (в радиусе 1).",
                    )
                    return
                action = Action(
                    ship_id=ship_id,
                    action_type=at,
                    target_x=x,
                    target_y=y,
                    target_z=z,
                )

            else:
                return
            
            # Удаляем старое действие и добавляем новое
            self.actions = [a for a in self.actions if a.ship_id != ship_id]
            self.actions.append(action)
            
            self.status_label.config(
                text=f"✅ Действие для {ship_name} сохранено",
                fg=self.colors['accent3'],
            )
            
        except ValueError as e:
            messagebox.showerror("Ошибка", f"Неверный формат координат: {e}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при сохранении: {e}")
    
    def send_actions(self):
        """Отправляет действия на сервер"""
        if not self.actions:
            messagebox.showwarning("Внимание", "Нет действий для отправки")
            return
        
        try:
            actions_data = [a.to_dict() for a in self.actions]
            self.framed.send(actions_data)

            self.status_label.config(
                text=f"🚀 Отправлено действий: {len(self.actions)}",
                fg=self.colors['accent3'],
            )
            self.actions = []

        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось отправить действия: {e}")
    
    def request_update(self):
        """Запрашивает обновление состояния"""
        if self.current_state and self.connected:
            self.update_interface(self.current_state)
    
    def run(self):
        self.root.after(500, self.poll_timer)
        """Запускает приложение"""
        self.root.mainloop()

if __name__ == "__main__":
    app = GameClientGUI()
    app.run()