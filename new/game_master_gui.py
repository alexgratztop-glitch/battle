# game_master_gui.py — панель гейммастера (UI overhaul v2).
#
# Цели переработки:
#   • Единая тема из ui_theme.py (палитра, шрифты, цвета команд, иконки).
#   • Карта как Canvas-клетки с иконками типов кораблей и HP-бейджами —
#     читается так же, как клиентская карта.
#   • HUD-плашки команд вместо Treeview-статистики.
#   • Журнал боя с иконками и цветными тегами — как у игрока.
#   • Панель «Арбитраж» прямо в правой колонке: выбор корабля, быстрые
#     кнопки HIT/HEAL/REVIVE/KILL/PHASE, ручная правка X/Y/Z, история
#     последних override'ов.
#   • Кнопка «?» — та же справка по типам, что и у игрока (импортируется).

import socket
import threading
import time
from tkinter import (
    BOTH, BOTTOM, CENTER, DISABLED, E, END, EW, FLAT, HORIZONTAL, LEFT, N, NE,
    NORMAL, NS, NSEW, NW, RIGHT, S, SUNKEN, TOP, VERTICAL, W, WORD, X, Y,
    BooleanVar, Button, Canvas, Checkbutton, Entry, Frame, IntVar, Label,
    LabelFrame, Scale, Scrollbar, Spinbox, StringVar, Text, Tk, Toplevel,
    messagebox,
)
from tkinter import ttk, font

from shared_simple import *
from protocol import Framed, ProtocolError
from ui_theme import (
    Fonts,
    Palette,
    SHIP_TYPE_INFO,
    TEAM_COLORS,
    apply_theme,
    hp_color,
    ship_accent,
    ship_icon,
    ship_role,
    ship_short,
)


# --------------------------------------------------------------------------- #
# Tooltip
# --------------------------------------------------------------------------- #

class _Tooltip:
    """Подсказка, появляющаяся над виджетом при hover."""

    def __init__(self, widget, text_fn, delay=400):
        self.widget = widget
        self.text_fn = text_fn if callable(text_fn) else (lambda: text_fn)
        self.delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        text = self.text_fn()
        if not text:
            return
        pal = Palette()
        fnt = Fonts()
        self._tip = Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip.geometry(f"+{x}+{y}")
        Label(self._tip, text=text, bg=pal.bg_card, fg=pal.fg_primary,
              font=fnt.small, justify=LEFT, bd=1, relief=SUNKEN,
              padx=8, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

class GameMasterGUI:
    """Панель гейммастера.

    Публичный поведенческий контракт (не менять — сервер ждёт):
      * message {'type': 'game_master', 'player_name': ...}
      * gm_command {'type':'gm_command', 'command': 'start_turn'|'end_planning'|
                    'stop'|'override_ship', ...}
    """

    CELL_SIZE = 42
    GRID = 10

    def __init__(self):
        self.pal = Palette()
        self.fnt = Fonts()

        self.socket = None
        self.framed = None
        self.connected = False
        self.current_state = None
        self.override_history = []   # list[str]

        # Цвета сохранены для обратной совместимости с тестами (они
        # обращаются к self.colors напрямую, см. tests/test_gm_and_history).
        self.colors = {
            'bg': self.pal.bg_root,
            'bg2': self.pal.bg_panel,
            'fg': self.pal.fg_primary,
            'accent1': self.pal.accent_info,
            'accent2': self.pal.accent_danger,
            'accent3': self.pal.accent_success,
            'accent4': self.pal.accent_warning,
            'panel': self.pal.bg_panel,
            'text': self.pal.fg_primary,
        }

        self.root = Tk()
        self.root.title("🎮 ГЕЙММАСТЕР — КОСМИЧЕСКИЙ БОЙ")
        self.root.geometry("1360x900")
        apply_theme(self.root, self.pal, self.fnt)
        self.root.configure(bg=self.pal.bg_root)

        self.current_layer = IntVar(value=0)
        self.selected_ship_id = StringVar(value="")

        # (row, col) -> (Canvas, dict_cell_state)
        self.map_cells = {}
        # ship_id -> Frame (карточка в списке кораблей)
        self.ship_cards = {}

        self._build()
        self.root.after(500, self.tick_timer)
        self.show_connection_window()

    # --------------------------------------------------------------- build ---

    def _build(self):
        pal, fnt = self.pal, self.fnt

        # Header ---------------------------------------------------------------
        header = Frame(self.root, bg=pal.bg_panel, height=64)
        header.pack(fill=X)
        header.pack_propagate(False)
        Label(header, text="🎮  ГЕЙММАСТЕР",
              bg=pal.bg_panel, fg=pal.accent_warning, font=fnt.h1
              ).pack(side=LEFT, padx=18)
        Label(header, text="полная видимость · арбитраж · лог",
              bg=pal.bg_panel, fg=pal.fg_secondary, font=fnt.body
              ).pack(side=LEFT, padx=(0, 20))
        help_btn = Button(header, text="?", width=3,
                          bg=pal.bg_card, fg=pal.accent_info,
                          activebackground=pal.border_strong,
                          activeforeground=pal.fg_title,
                          font=fnt.h3, bd=0, relief=FLAT, cursor="hand2",
                          command=self.open_legend)
        help_btn.pack(side=RIGHT, padx=14)
        _Tooltip(help_btn, "Справка по типам кораблей")

        self.status_label = Label(header, text="⚫ не подключён",
                                  bg=pal.bg_panel, fg=pal.accent_danger,
                                  font=fnt.body_bold)
        self.status_label.pack(side=RIGHT, padx=14)

        # Three-column body ---------------------------------------------------
        body = Frame(self.root, bg=pal.bg_root)
        body.pack(fill=BOTH, expand=True, padx=10, pady=(8, 10))

        self._build_map_column(body)
        self._build_center_column(body)
        self._build_right_column(body)

    # ------------------------------------------------------------------ MAP --

    def _build_map_column(self, parent):
        pal, fnt = self.pal, self.fnt

        col = Frame(parent, bg=pal.bg_root)
        col.pack(side=LEFT, fill=BOTH, expand=True)

        # Z-slider control.
        ctrl = Frame(col, bg=pal.bg_panel)
        ctrl.pack(fill=X, pady=(0, 6))
        Label(ctrl, text="🗺  КАРТА · Z-слой",
              bg=pal.bg_panel, fg=pal.accent_info, font=fnt.h3
              ).pack(side=LEFT, padx=10, pady=6)
        Scale(ctrl, from_=0, to=9, variable=self.current_layer,
              orient=HORIZONTAL, length=260, showvalue=False,
              bg=pal.bg_panel, fg=pal.accent_info,
              troughcolor=pal.bg_root, activebackground=pal.accent_info,
              highlightbackground=pal.bg_panel, bd=0,
              command=lambda v: self._on_layer_change(v)
              ).pack(side=LEFT, padx=6, pady=6)
        self.layer_label = Label(ctrl, text="Z = 0",
                                 bg=pal.bg_panel, fg=pal.accent_info,
                                 font=fnt.h2, width=7, anchor=W)
        self.layer_label.pack(side=LEFT, padx=10)
        Button(ctrl, text="🔄 Обновить",
               bg=pal.accent_info, fg="#06122a",
               font=fnt.body_bold, bd=0, relief=FLAT, padx=12, pady=2,
               activebackground="#3be6ff",
               command=self.update_map).pack(side=RIGHT, padx=10, pady=6)

        # Map grid.
        map_wrap = Frame(col, bg=pal.bg_map, bd=0)
        map_wrap.pack(fill=BOTH, expand=True)
        grid_w = self.CELL_SIZE * self.GRID
        grid = Frame(map_wrap, bg=pal.bg_map)
        grid.pack(padx=10, pady=10)

        # Column/row headers.
        Label(grid, text="", bg=pal.bg_map).grid(row=0, column=0)
        for c in range(self.GRID):
            Label(grid, text=str(c), bg=pal.bg_map, fg=pal.fg_muted,
                  font=fnt.small).grid(row=0, column=c + 1, sticky=NSEW)
        for r in range(self.GRID):
            Label(grid, text=str(r), bg=pal.bg_map, fg=pal.fg_muted,
                  font=fnt.small).grid(row=r + 1, column=0, sticky=NSEW,
                                        padx=(0, 3))
            for c in range(self.GRID):
                cv = Canvas(grid, width=self.CELL_SIZE, height=self.CELL_SIZE,
                            bg=pal.bg_cell_empty,
                            highlightthickness=1,
                            highlightbackground=pal.border)
                cv.grid(row=r + 1, column=c + 1, padx=1, pady=1)
                cv.bind("<Button-1>",
                        lambda e, rr=r, cc=c: self._on_cell_click(rr, cc))
                self.map_cells[(r, c)] = {"canvas": cv, "ship_id": None,
                                          "tooltip": None}

        # Footer: legend mini.
        foot = Frame(col, bg=pal.bg_panel)
        foot.pack(fill=X, pady=(6, 0))
        for team in ("Team A", "Team B", "Team C"):
            dot = Canvas(foot, width=14, height=14, bg=pal.bg_panel,
                         highlightthickness=0)
            dot.pack(side=LEFT, padx=(12, 4), pady=6)
            dot.create_oval(1, 1, 13, 13, fill=TEAM_COLORS[team], outline="")
            Label(foot, text=team, bg=pal.bg_panel, fg=pal.fg_secondary,
                  font=fnt.small_bold).pack(side=LEFT, padx=(0, 8), pady=6)
        Label(foot, text="клик по клетке → выбор корабля",
              bg=pal.bg_panel, fg=pal.fg_muted, font=fnt.small
              ).pack(side=RIGHT, padx=10, pady=6)

    # -------------------------------------------------------------- CENTER --

    def _build_center_column(self, parent):
        pal, fnt = self.pal, self.fnt
        col = Frame(parent, bg=pal.bg_root, width=360)
        col.pack(side=LEFT, fill=Y, padx=(10, 0))
        col.pack_propagate(False)

        # Turn / phase.
        top = Frame(col, bg=pal.bg_panel)
        top.pack(fill=X, pady=(0, 8))
        head_row = Frame(top, bg=pal.bg_panel)
        head_row.pack(fill=X, padx=12, pady=(10, 4))
        self.turn_label = Label(head_row, text="ХОД 0 /30",
                                bg=pal.bg_panel, fg=pal.fg_title,
                                font=fnt.h2)
        self.turn_label.pack(side=LEFT)
        self.phase_label = Label(head_row, text="⏳ ожидание",
                                 bg=pal.bg_panel, fg=pal.fg_secondary,
                                 font=fnt.body_bold)
        self.phase_label.pack(side=LEFT, padx=12)
        self.timer_label = Label(top, text="⏱ ожидание…",
                                 bg=pal.bg_panel, fg=pal.accent_warning,
                                 font=fnt.body_bold)
        self.timer_label.pack(anchor=W, padx=12, pady=(0, 10))

        # Turn controls.
        ctl = Frame(col, bg=pal.bg_panel)
        ctl.pack(fill=X, pady=(0, 8))
        Label(ctl, text="🎛  УПРАВЛЕНИЕ ХОДОМ",
              bg=pal.bg_panel, fg=pal.accent_info, font=fnt.h3
              ).grid(row=0, column=0, columnspan=3, sticky=W, padx=10,
                     pady=(8, 6))
        self.btn_start = Button(ctl, text="▶ Начать ход",
                                bg="#228B22", fg="white", font=fnt.body_bold,
                                bd=0, relief=FLAT, padx=8, pady=6,
                                state=DISABLED,
                                command=lambda: self.send_gm_command(
                                    'start_turn'))
        self.btn_start.grid(row=1, column=0, sticky=EW, padx=8, pady=2)
        self.btn_end = Button(ctl, text="⏹ Завершить сбор",
                              bg="#b8860b", fg="white", font=fnt.body_bold,
                              bd=0, relief=FLAT, padx=8, pady=6,
                              state=DISABLED,
                              command=lambda: self.send_gm_command(
                                  'end_planning'))
        self.btn_end.grid(row=1, column=1, sticky=EW, padx=4, pady=2)
        self.btn_stop = Button(ctl, text="🛑 Стоп",
                               bg="#8b0000", fg="white", font=fnt.body_bold,
                               bd=0, relief=FLAT, padx=8, pady=6,
                               state=DISABLED,
                               command=lambda: self.send_gm_command('stop'))
        self.btn_stop.grid(row=1, column=2, sticky=EW, padx=(4, 8), pady=(2, 10))
        for c in range(3):
            ctl.grid_columnconfigure(c, weight=1, uniform="gm_btn")

        # Team pills.
        pills = Frame(col, bg=pal.bg_root)
        pills.pack(fill=X, pady=(0, 8))
        self.team_pill_widgets = {}
        for team in ("Team A", "Team B", "Team C"):
            pill = Frame(pills, bg=pal.bg_panel)
            pill.pack(fill=X, pady=2)
            stripe = Canvas(pill, width=6, height=1,
                            bg=TEAM_COLORS[team],
                            highlightthickness=0)
            stripe.pack(side=LEFT, fill=Y)
            body = Frame(pill, bg=pal.bg_panel)
            body.pack(side=LEFT, fill=X, expand=True, padx=10, pady=6)
            name = Label(body, text=team, bg=pal.bg_panel,
                         fg=TEAM_COLORS[team], font=fnt.body_bold)
            name.pack(anchor=W)
            details = Label(body, text="—", bg=pal.bg_panel,
                            fg=pal.fg_primary, font=fnt.small)
            details.pack(anchor=W)
            self.team_pill_widgets[team] = details

        # Battle log.
        log_frame = Frame(col, bg=pal.bg_panel)
        log_frame.pack(fill=BOTH, expand=True)
        Label(log_frame, text="📜  ЖУРНАЛ БОЯ",
              bg=pal.bg_panel, fg=pal.accent_danger, font=fnt.h3
              ).pack(anchor=W, padx=10, pady=(8, 4))
        log_body = Frame(log_frame, bg=pal.bg_panel)
        log_body.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))
        self.log_text = Text(log_body, wrap=WORD, bg=pal.bg_root,
                             fg=pal.fg_primary, font=fnt.log, bd=0,
                             insertbackground=pal.fg_primary, height=12)
        sb = Scrollbar(log_body, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)
        self._configure_log_tags()

    # --------------------------------------------------------------- RIGHT --

    def _build_right_column(self, parent):
        pal, fnt = self.pal, self.fnt
        col = Frame(parent, bg=pal.bg_root, width=360)
        col.pack(side=RIGHT, fill=Y, padx=(10, 0))
        col.pack_propagate(False)

        # Arbiter tools (override).
        arb = Frame(col, bg=pal.bg_panel)
        arb.pack(fill=X, pady=(0, 8))
        Label(arb, text="🛠  АРБИТРАЖ",
              bg=pal.bg_panel, fg=pal.accent_warning, font=fnt.h3
              ).pack(anchor=W, padx=10, pady=(8, 4))
        self.selected_ship_label = Label(
            arb, text="выберите корабль: клик по клетке или в списке ниже",
            bg=pal.bg_panel, fg=pal.fg_secondary, font=fnt.small,
            wraplength=330, justify=LEFT)
        self.selected_ship_label.pack(anchor=W, padx=10, pady=(0, 6))

        xyz = Frame(arb, bg=pal.bg_panel)
        xyz.pack(anchor=W, padx=10, pady=(0, 6))
        self.x_var = IntVar(value=0)
        self.y_var = IntVar(value=0)
        self.z_var = IntVar(value=0)
        self.hits_var = IntVar(value=0)
        self.alive_var = BooleanVar(value=True)
        for i, (lbl, var) in enumerate((("X", self.x_var),
                                         ("Y", self.y_var),
                                         ("Z", self.z_var))):
            Label(xyz, text=lbl, bg=pal.bg_panel, fg=pal.fg_secondary,
                  font=fnt.small_bold).grid(row=0, column=i * 2,
                                             padx=(0 if i == 0 else 6, 2))
            Spinbox(xyz, from_=0, to=9, textvariable=var, width=3,
                    font=fnt.body).grid(row=0, column=i * 2 + 1)
        Label(xyz, text="HP", bg=pal.bg_panel, fg=pal.fg_secondary,
              font=fnt.small_bold).grid(row=0, column=6, padx=(10, 2))
        Spinbox(xyz, from_=0, to=10, textvariable=self.hits_var, width=3,
                font=fnt.body).grid(row=0, column=7)
        Checkbutton(xyz, text="жив", variable=self.alive_var,
                    bg=pal.bg_panel, fg=pal.fg_primary,
                    selectcolor=pal.bg_root,
                    activebackground=pal.bg_panel,
                    font=fnt.small_bold
                    ).grid(row=0, column=8, padx=(8, 0))

        # Apply override row.
        q1 = Frame(arb, bg=pal.bg_panel)
        q1.pack(fill=X, padx=10, pady=(4, 2))
        self.btn_apply = Button(
            q1, text="✅ Применить override",
            bg=pal.accent_success, fg="#0a2a12", font=fnt.body_bold,
            bd=0, relief=FLAT, padx=10, pady=6,
            state=DISABLED, command=self._apply_override)
        self.btn_apply.pack(fill=X)

        # Quick actions row.
        q2 = Frame(arb, bg=pal.bg_panel)
        q2.pack(fill=X, padx=10, pady=(2, 8))
        self.btn_hit = Button(q2, text="−1 HP", bg="#8b0000", fg="white",
                              font=fnt.small_bold, bd=0, relief=FLAT,
                              pady=6, state=DISABLED,
                              command=lambda: self._quick(hits_delta=+1))
        self.btn_hit.pack(side=LEFT, fill=X, expand=True, padx=(0, 2))
        self.btn_heal = Button(q2, text="+1 HP", bg="#228B22", fg="white",
                               font=fnt.small_bold, bd=0, relief=FLAT,
                               pady=6, state=DISABLED,
                               command=lambda: self._quick(hits_delta=-1))
        self.btn_heal.pack(side=LEFT, fill=X, expand=True, padx=2)
        self.btn_kill = Button(q2, text="✖ KILL", bg="#400000", fg="white",
                               font=fnt.small_bold, bd=0, relief=FLAT,
                               pady=6, state=DISABLED,
                               command=lambda: self._quick(kill=True))
        self.btn_kill.pack(side=LEFT, fill=X, expand=True, padx=(2, 0))

        # Ship list.
        sl = Frame(col, bg=pal.bg_panel)
        sl.pack(fill=BOTH, expand=True)
        Label(sl, text="🛰  КОРАБЛИ",
              bg=pal.bg_panel, fg=pal.accent_info, font=fnt.h3
              ).pack(anchor=W, padx=10, pady=(8, 4))
        canvas = Canvas(sl, bg=pal.bg_panel, highlightthickness=0)
        sb = Scrollbar(sl, orient=VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True, padx=(6, 0),
                    pady=(0, 8))
        sb.pack(side=RIGHT, fill=Y, pady=(0, 8))
        self.ship_list_inner = Frame(canvas, bg=pal.bg_panel)
        inner_id = canvas.create_window((0, 0), window=self.ship_list_inner,
                                         anchor="nw")
        self.ship_list_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>",
            lambda e, c=canvas, i=inner_id: c.itemconfigure(i, width=e.width))
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(
                ev, lambda e, c=canvas: self._scroll(e, c), add="+")
        self.ship_list_canvas = canvas

        # Override history.
        hist = Frame(col, bg=pal.bg_panel)
        hist.pack(fill=X, pady=(8, 0))
        Label(hist, text="📋  ИСТОРИЯ OVERRIDE",
              bg=pal.bg_panel, fg=pal.fg_secondary, font=fnt.h3
              ).pack(anchor=W, padx=10, pady=(8, 4))
        self.history_text = Text(hist, height=6, bg=pal.bg_root,
                                 fg=pal.fg_secondary, font=fnt.log, bd=0,
                                 wrap=WORD)
        self.history_text.pack(fill=X, padx=8, pady=(0, 8))
        self.history_text.insert(END, "—\n")
        self.history_text.configure(state=DISABLED)

        # Message label at bottom.
        self.message_label = Label(col, text="",
                                    bg=pal.bg_root, fg=pal.accent_warning,
                                    font=fnt.small, anchor=W)
        self.message_label.pack(fill=X, pady=(6, 0))

    # --------------------------------------------------------- log tags ----

    def _configure_log_tags(self):
        p = self.pal
        T = self.log_text
        T.tag_config("turn_hdr", foreground=p.accent_info,
                     font=(Fonts().family_sans, 11, "bold"))
        T.tag_config("turn_sum", foreground=p.fg_muted)
        T.tag_config("team_A", foreground=TEAM_COLORS["Team A"],
                     font=(Fonts().family_sans, 10, "bold"))
        T.tag_config("team_B", foreground=TEAM_COLORS["Team B"],
                     font=(Fonts().family_sans, 10, "bold"))
        T.tag_config("team_C", foreground=TEAM_COLORS["Team C"],
                     font=(Fonts().family_sans, 10, "bold"))
        T.tag_config("dmg", foreground=p.accent_warning)
        T.tag_config("killed", foreground=p.accent_danger,
                     font=(Fonts().family_sans, 10, "bold"))
        T.tag_config("ram", foreground=p.accent_phase,
                     font=(Fonts().family_sans, 10, "bold"))
        T.tag_config("mine", foreground=p.accent_mine)
        T.tag_config("holo", foreground=p.accent_phase)
        T.tag_config("arrow", foreground=p.fg_muted)
        T.tag_config("muted", foreground=p.fg_muted)

    # ----------------------------------------------------- connection ------

    def show_connection_window(self):
        pal, fnt = self.pal, self.fnt
        conn = Toplevel(self.root)
        conn.title("🎮 Подключение гейммастера")
        conn.geometry("420x320")
        conn.configure(bg=pal.bg_root)
        conn.transient(self.root)
        conn.grab_set()
        Label(conn, text="🎮  ГЕЙММАСТЕР",
              bg=pal.bg_root, fg=pal.accent_warning,
              font=fnt.h1).pack(pady=16)
        frm = Frame(conn, bg=pal.bg_panel)
        frm.pack(padx=24, pady=8, fill=BOTH, expand=True)
        Label(frm, text="🌐 IP сервера:", bg=pal.bg_panel, fg=pal.fg_primary,
              font=fnt.body).pack(anchor=W, padx=16, pady=(14, 4))
        self.ip_entry = Entry(frm, bg=pal.bg_root, fg=pal.fg_primary,
                              insertbackground=pal.fg_primary,
                              font=fnt.body, bd=0, relief=FLAT)
        self.ip_entry.insert(0, "localhost")
        self.ip_entry.pack(padx=16, pady=(0, 10), fill=X, ipady=4)
        Label(frm, text="👤 Ваше имя:", bg=pal.bg_panel, fg=pal.fg_primary,
              font=fnt.body).pack(anchor=W, padx=16, pady=(6, 4))
        self.name_entry = Entry(frm, bg=pal.bg_root, fg=pal.fg_primary,
                                insertbackground=pal.fg_primary,
                                font=fnt.body, bd=0, relief=FLAT)
        self.name_entry.insert(0, "Гейммастер")
        self.name_entry.pack(padx=16, pady=(0, 14), fill=X, ipady=4)
        btns = Frame(conn, bg=pal.bg_root)
        btns.pack(pady=10)
        Button(btns, text="🎮 Подключиться",
               bg=pal.accent_success, fg="#0a2a12", font=fnt.body_bold,
               bd=0, relief=FLAT, padx=18, pady=6,
               command=self.connect).pack(side=LEFT, padx=10)
        Button(btns, text="❌ Выход",
               bg=pal.accent_danger, fg="white", font=fnt.body_bold,
               bd=0, relief=FLAT, padx=18, pady=6,
               command=self.root.quit).pack(side=LEFT, padx=10)

    def connect(self):
        server_ip = self.ip_entry.get().strip() or "localhost"
        player_name = self.name_entry.get().strip() or "Гейммастер"
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            self.socket.connect((server_ip, 5555))
            self.framed = Framed(self.socket)
            self.framed.send({'type': 'game_master',
                              'player_name': player_name})
            self.connected = True
            self.status_label.config(text="🟢 подключён",
                                     fg=self.pal.accent_success)
            self.ip_entry.master.master.destroy()
            self.receive_thread = threading.Thread(target=self.receive_loop,
                                                    daemon=True)
            self.receive_thread.start()
            messagebox.showinfo("Успех",
                                "🎮 Подключение установлено как гейммастер.")
        except Exception as e:
            messagebox.showerror("Ошибка подключения",
                                 f"Не удалось подключиться: {e}")

    def receive_loop(self):
        while self.connected:
            try:
                msg = self.framed.recv_once(timeout=1)
            except ProtocolError as e:
                if self.connected:
                    self.connected = False
                    self.root.after(
                        0,
                        lambda err=str(e): messagebox.showinfo(
                            "Соединение", f"Сервер закрыл соединение: {err}"))
                break
            except Exception as e:
                if self.connected:
                    err_text = f"Ошибка получения данных: {e}"
                    self.root.after(
                        0,
                        lambda t=err_text: self.message_label.config(
                            text=t, fg=self.pal.accent_danger))
                    time.sleep(1)
                continue
            if msg is None:
                continue
            if isinstance(msg, dict) and msg.get('type') == 'reject':
                reason = msg.get('reason', 'Сервер отклонил подключение')
                self.connected = False
                self.root.after(
                    0,
                    lambda r=reason: messagebox.showerror("Отказ сервера", r))
                break
            self.current_state = msg
            self.root.after(0, self.update_interface, msg)

    # ---------------------------------------------------- interface upd ----

    def update_interface(self, state):
        turn = state.get('turn', 0) + 1
        limit = state.get('turn_limit', 30)
        phase = state.get('phase', 'unknown')
        message = state.get('message', '')
        game_over = state.get('game_over', False)

        self.turn_label.config(text=f"ХОД {turn} /{limit}")
        if phase == 'planning':
            self.phase_label.config(text="📝 планирование",
                                    fg=self.pal.accent_success)
        elif phase == 'results':
            self.phase_label.config(text="📊 результаты",
                                    fg=self.pal.accent_warning)
        elif phase == 'waiting_for_gm':
            self.phase_label.config(text="⏳ ждёт GM",
                                    fg=self.pal.accent_info)
        else:
            self.phase_label.config(text=phase, fg=self.pal.fg_secondary)

        if game_over:
            winner = state.get('winner', 'Не определён')
            self.message_label.config(
                text=f"🏆 ИГРА ОКОНЧЕНА! Победитель: {winner}",
                fg=self.pal.accent_warning)
        else:
            self.message_label.config(text=message,
                                      fg=self.pal.fg_secondary)

        # Turn-controls enable.
        can_start = (phase == 'waiting_for_gm') and not game_over
        can_end = (phase == 'planning') and not game_over
        can_stop = not game_over
        self.btn_start.config(state=(NORMAL if can_start else DISABLED))
        self.btn_end.config(state=(NORMAL if can_end else DISABLED))
        self.btn_stop.config(state=(NORMAL if can_stop else DISABLED))

        # Timer display.
        deadline = state.get('planning_deadline')
        if phase == 'planning' and deadline:
            remaining = max(0, int(deadline - time.time()))
            received = state.get('actions_received_teams', [])
            connected = state.get('connected_teams', [])
            self.timer_label.config(
                text=f"⏱ {remaining}с  ·  сборы {len(received)}/{len(connected)}")
        elif phase == 'waiting_for_gm':
            self.timer_label.config(text="⏸ ждём старта — нажмите «Начать ход»")
        elif phase == 'results':
            self.timer_label.config(text="📊 результаты хода")
        elif game_over:
            self.timer_label.config(text="🏁 игра окончена")
        else:
            self.timer_label.config(text="⏱ ожидание…")

        self._update_team_pills(state)
        self._rebuild_ship_list(state)
        self._rebuild_battle_log(state)
        self.update_map()

    # -------------------------------------------------------- team pills ---

    def _update_team_pills(self, state):
        all_ships = state.get('all_ships', {})
        stats = {t: {'alive': 0, 'total': 0, 'dmg': 0, 'kills': 0}
                 for t in TEAM_COLORS}
        for ship in all_ships.values():
            t = ship.get('team')
            if t not in stats:
                continue
            stats[t]['total'] += 1
            if ship.get('alive'):
                stats[t]['alive'] += 1
        for h in state.get('hit_history', []):
            atk = h.get('attacker')
            if atk in stats:
                stats[atk]['dmg'] += 1
                if h.get('killed'):
                    stats[atk]['kills'] += 1
        for team, w in self.team_pill_widgets.items():
            s = stats[team]
            w.config(text=f"{s['alive']}/{s['total']} живых · "
                          f"⚡{s['dmg']} урона · ✖{s['kills']} киллов")

    # ------------------------------------------------------- ship list ----

    def _rebuild_ship_list(self, state):
        pal, fnt = self.pal, self.fnt
        for w in self.ship_list_inner.winfo_children():
            w.destroy()
        self.ship_cards.clear()
        all_ships = state.get('all_ships', {})
        # Сортируем: Team A, B, C, по id.
        for sid, s in sorted(all_ships.items(),
                              key=lambda x: (x[1].get('team', ''), x[0])):
            self._render_ship_card(sid, s)

    def _render_ship_card(self, sid, s):
        pal, fnt = self.pal, self.fnt
        team = s.get('team', 'Team A')
        team_c = TEAM_COLORS.get(team, pal.accent_info)
        tname = s.get('type', '?')
        alive = bool(s.get('alive', True))
        hp_max = max(1, int(s.get('max_hits', 1)))
        hp = max(0, hp_max - int(s.get('hits', 0))) if alive else 0

        card = Frame(self.ship_list_inner, bg=pal.bg_card, bd=0,
                     highlightthickness=1,
                     highlightbackground=pal.border)
        card.pack(fill=X, padx=6, pady=2)
        card.bind("<Button-1>", lambda e, i=sid: self._select_ship(i))
        stripe = Canvas(card, width=4, height=1, bg=team_c,
                        highlightthickness=0)
        stripe.pack(side=LEFT, fill=Y)
        body = Frame(card, bg=pal.bg_card)
        body.pack(side=LEFT, fill=X, expand=True, padx=6, pady=4)
        body.bind("<Button-1>", lambda e, i=sid: self._select_ship(i))

        top = Frame(body, bg=pal.bg_card)
        top.pack(fill=X)
        top.bind("<Button-1>", lambda e, i=sid: self._select_ship(i))
        Label(top, text=ship_icon(tname), bg=pal.bg_card,
              fg=ship_accent(tname), font=fnt.body_bold
              ).pack(side=LEFT, padx=(0, 4))
        Label(top, text=f"{sid}", bg=pal.bg_card, fg=team_c,
              font=fnt.small_bold).pack(side=LEFT)
        Label(top, text=f"· {tname}", bg=pal.bg_card,
              fg=pal.fg_primary, font=fnt.small).pack(side=LEFT, padx=4)
        pos = Label(top,
                    text=f"({s.get('x','?')},{s.get('y','?')},{s.get('z','?')})",
                    bg=pal.bg_card, fg=pal.fg_secondary, font=fnt.small)
        pos.pack(side=RIGHT)

        hp_row = Frame(body, bg=pal.bg_card)
        hp_row.pack(fill=X, pady=(2, 0))
        hp_row.bind("<Button-1>", lambda e, i=sid: self._select_ship(i))
        if alive:
            col = hp_color(hp, hp_max, pal)
            bar = Canvas(hp_row, height=6, bg=pal.bg_panel,
                         highlightthickness=0)
            bar.pack(fill=X, side=LEFT, expand=True)
            bar.bind("<Button-1>", lambda e, i=sid: self._select_ship(i))
            bar.bind("<Configure>",
                     lambda e, c=bar, h=hp, m=hp_max, col=col:
                     self._draw_hp_bar(c, h, m, col))
            Label(hp_row, text=f"{hp}/{hp_max}", bg=pal.bg_card,
                  fg=col, font=fnt.small_bold).pack(side=RIGHT, padx=6)
        else:
            Label(hp_row, text="💀  УНИЧТОЖЕН", bg=pal.bg_card,
                  fg=pal.accent_danger, font=fnt.small_bold
                  ).pack(side=LEFT, padx=4)
        self.ship_cards[sid] = card

        # Highlight if selected.
        if self.selected_ship_id.get() == sid:
            card.configure(highlightbackground=pal.accent_info,
                           highlightthickness=2)

    @staticmethod
    def _draw_hp_bar(canvas, hp, hp_max, color):
        canvas.delete("all")
        w = max(1, canvas.winfo_width())
        h = canvas.winfo_height()
        canvas.create_rectangle(0, 0, w, h,
                                 fill=Palette().bg_panel, outline="")
        filled = int(w * (hp / hp_max)) if hp_max else 0
        canvas.create_rectangle(0, 0, filled, h, fill=color, outline="")

    @staticmethod
    def _scroll(event, canvas):
        # Only respond if pointer is over this canvas.
        x, y = event.x_root, event.y_root
        try:
            over = canvas.winfo_containing(x, y)
        except Exception:
            over = None
        w = over
        while w is not None:
            if w is canvas:
                break
            w = getattr(w, "master", None)
        if w is not canvas:
            return
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        else:
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

    # ---------------------------------------------------- battle log ------

    def _rebuild_battle_log(self, state):
        T = self.log_text
        T.configure(state=NORMAL)
        T.delete("1.0", END)
        history = state.get('hit_history', [])
        if not history:
            T.insert(END, "Событий ещё нет.\n", "muted")
            T.configure(state=DISABLED)
            return
        # Group by turn; compute per-turn summary.
        from collections import defaultdict
        by_turn = defaultdict(list)
        for h in history:
            by_turn[int(h.get('turn', 0))].append(h)
        for turn in sorted(by_turn):
            events = by_turn[turn]
            dmg_by = defaultdict(int)
            kills_by = defaultdict(int)
            for h in events:
                atk = h.get('attacker', '?')
                dmg_by[atk] += 1
                if h.get('killed'):
                    kills_by[atk] += 1
            parts = []
            for t in ("Team A", "Team B", "Team C"):
                if dmg_by[t] or kills_by[t]:
                    bit = f"{t[-1]}:{dmg_by[t]}dmg"
                    if kills_by[t]:
                        bit += f"/{kills_by[t]}✖"
                    parts.append(bit)
            T.insert(END, f"── Ход {turn} ", "turn_hdr")
            if parts:
                T.insert(END, "(" + " · ".join(parts) + ")\n", "turn_sum")
            else:
                T.insert(END, "\n", "turn_sum")
            for h in events:
                self._append_log_line(h)
        T.configure(state=DISABLED)
        T.see(END)

    def _append_log_line(self, h):
        T = self.log_text
        kind = h.get('kind', 'hit')
        atk = h.get('attacker', '?')
        tgt = h.get('target', '?')
        atk_tag = f"team_{atk[-1]}" if atk in TEAM_COLORS else "muted"
        tgt_tag = f"team_{tgt[-1]}" if tgt in TEAM_COLORS else "muted"
        pos = h.get('position', '?')
        dmg = h.get('damage', 1)
        killed = bool(h.get('killed'))
        atk_name = h.get('attacker_name', '')
        tgt_name = h.get('target_name', '')

        T.insert(END, "   ", "muted")
        if kind == "ram":
            T.insert(END, "💥 ", "ram")
            T.insert(END, f"{atk} {atk_name} ", atk_tag)
            T.insert(END, "⚡таран ", "ram")
            T.insert(END, f"→ {tgt} {tgt_name}", tgt_tag)
            T.insert(END, f"  @ {pos}", "muted")
        elif kind == "mine":
            T.insert(END, "💀 ", "mine")
            T.insert(END, f"Мина({atk}) ", "mine")
            T.insert(END, f"→ {tgt} {tgt_name}", tgt_tag)
            T.insert(END, f"  @ {pos}", "muted")
        elif kind == "holo":
            T.insert(END, "🎭 ", "holo")
            T.insert(END, f"{tgt} {tgt_name} ", tgt_tag)
            T.insert(END, "раскрыл голограмму ", "holo")
            T.insert(END, f"{atk}", atk_tag)
            T.insert(END, f"  @ {pos}", "muted")
        else:
            T.insert(END, "🎯 ", atk_tag)
            T.insert(END, f"{atk} {atk_name} ", atk_tag)
            T.insert(END, "→ ", "arrow")
            T.insert(END, f"{tgt} {tgt_name}", tgt_tag)
            T.insert(END, f"  @ {pos}", "muted")
        if dmg:
            T.insert(END, f"  -{dmg}HP", "dmg")
        if killed:
            T.insert(END, "  ✖УБИТ", "killed")
        T.insert(END, "\n")

    # ------------------------------------------------------- map draw -----

    def update_map(self, *_):
        if not self.current_state:
            self._clear_map_cells()
            return
        layer = self.current_layer.get()
        self.layer_label.config(text=f"Z = {layer}")
        all_ships = self.current_state.get('all_ships', {})

        # Clear cells.
        self._clear_map_cells()

        # Build {(x,y): best_ship} on current layer (prefer alive over dead).
        by_cell = {}
        for sid, s in all_ships.items():
            if s.get('z') != layer:
                continue
            key = (s.get('x'), s.get('y'))
            cur = by_cell.get(key)
            if cur is None:
                by_cell[key] = (sid, s)
            else:
                # prefer alive
                if s.get('alive') and not cur[1].get('alive'):
                    by_cell[key] = (sid, s)

        for (x, y), (sid, s) in by_cell.items():
            if not (0 <= y < self.GRID and 0 <= x < self.GRID):
                continue
            self._draw_ship_cell(y, x, sid, s)

    def _clear_map_cells(self):
        pal = self.pal
        for (r, c), info in self.map_cells.items():
            cv = info["canvas"]
            cv.delete("all")
            cv.configure(bg=pal.bg_cell_empty,
                         highlightbackground=pal.border)
            info["ship_id"] = None

    def _draw_ship_cell(self, r, c, sid, s):
        pal, fnt = self.pal, self.fnt
        info = self.map_cells[(r, c)]
        cv = info["canvas"]
        cv.delete("all")
        team = s.get('team', 'Team A')
        team_c = TEAM_COLORS.get(team, pal.accent_info)
        alive = bool(s.get('alive', True))
        tname = s.get('type', '?')
        hp_max = max(1, int(s.get('max_hits', 1)))
        hp = max(0, hp_max - int(s.get('hits', 0)))

        # Background.
        if not alive:
            cv.configure(bg=pal.bg_cell_empty,
                         highlightbackground=pal.accent_danger)
        else:
            cv.configure(bg=pal.bg_card, highlightbackground=team_c)

        W = self.CELL_SIZE
        # Team corner-stripe.
        cv.create_rectangle(0, 0, W, 4, fill=team_c, outline="")
        # Icon.
        cv.create_text(W / 2, W / 2 - 4,
                       text=ship_icon(tname) if alive else "💀",
                       fill=ship_accent(tname) if alive else pal.accent_danger,
                       font=fnt.cell_icon)
        # Ship id small badge bottom-left.
        cv.create_text(4, W - 4, anchor="sw", text=sid,
                       fill=pal.fg_secondary, font=fnt.small_bold)
        # HP bar.
        if alive:
            bar_w = int((W - 6) * (hp / hp_max)) if hp_max else 0
            cv.create_rectangle(3, W - 7, W - 3, W - 4,
                                fill=pal.bg_panel, outline="")
            col = hp_color(hp, hp_max, pal)
            cv.create_rectangle(3, W - 7, 3 + bar_w, W - 4,
                                fill=col, outline="")
        # Phase ring.
        if alive and s.get('is_phased'):
            cv.create_oval(4, 4, W - 4, W - 4,
                           outline=pal.accent_phase, width=2, dash=(3, 2))
        info["ship_id"] = sid

        # Tooltip text.
        ttext = (f"{sid} · {team} · {tname}\n"
                 f"({s.get('x')},{s.get('y')},{s.get('z')})  "
                 f"HP {hp}/{hp_max}" + ("  ФАЗА" if s.get('is_phased') else ""))
        if not alive:
            ttext += "\n💀 уничтожен"
        existing = info.get("tooltip")
        if existing is not None:
            existing.text_fn = lambda t=ttext: t
        else:
            info["tooltip"] = _Tooltip(cv, (lambda t=ttext: t))

    def _on_cell_click(self, r, c):
        info = self.map_cells.get((r, c), {})
        sid = info.get("ship_id")
        if sid:
            self._select_ship(sid)

    def _on_layer_change(self, value):
        try:
            layer = int(float(value))
        except Exception:
            layer = 0
        self.layer_label.config(text=f"Z = {layer}")
        self.update_map()

    # ------------------------------------------------------ selection -----

    def _select_ship(self, sid):
        self.selected_ship_id.set(sid)
        state = self.current_state or {}
        s = state.get('all_ships', {}).get(sid)
        if not s:
            return
        self.selected_ship_label.config(
            text=f"выбран: {sid}  [{s.get('team','?')}]  {s.get('type','?')}  "
                 f"({s.get('x','?')},{s.get('y','?')},{s.get('z','?')})  "
                 f"hits={s.get('hits',0)}  "
                 f"{'alive' if s.get('alive') else 'DEAD'}",
            fg=self.pal.fg_primary)
        try:
            self.x_var.set(int(s.get('x', 0)))
            self.y_var.set(int(s.get('y', 0)))
            self.z_var.set(int(s.get('z', 0)))
            self.hits_var.set(int(s.get('hits', 0)))
            self.alive_var.set(bool(s.get('alive', True)))
        except Exception:
            pass
        for b in (self.btn_apply, self.btn_hit, self.btn_heal, self.btn_kill):
            b.config(state=NORMAL)
        # Highlight card.
        for other_sid, card in self.ship_cards.items():
            if other_sid == sid:
                card.configure(highlightbackground=self.pal.accent_info,
                               highlightthickness=2)
            else:
                card.configure(highlightbackground=self.pal.border,
                               highlightthickness=1)

    # ------------------------------------------------------- override -----

    def _apply_override(self):
        sid = self.selected_ship_id.get()
        if not sid:
            return
        ok = self.send_gm_command(
            'override_ship', ship_id=sid,
            x=int(self.x_var.get()), y=int(self.y_var.get()),
            z=int(self.z_var.get()), alive=bool(self.alive_var.get()),
            hits=int(self.hits_var.get()))
        if ok:
            state = "жив" if self.alive_var.get() else "МЁРТВ"
            self._push_history(
                f"{sid} → ({self.x_var.get()},{self.y_var.get()},"
                f"{self.z_var.get()})  HP:{self.hits_var.get()}  {state}")

    def _quick(self, hits_delta=0, kill=False):
        sid = self.selected_ship_id.get()
        if not sid:
            return
        s = (self.current_state or {}).get('all_ships', {}).get(sid)
        if not s:
            return
        if kill:
            new_alive = False
            new_hits = int(s.get('max_hits', 1))
            action = "✖ KILL"
        else:
            new_alive = bool(s.get('alive', True))
            new_hits = max(0, int(s.get('hits', 0)) + hits_delta)
            action = f"{'−' if hits_delta>0 else '+'}{abs(hits_delta)} HP"
        ok = self.send_gm_command(
            'override_ship', ship_id=sid,
            x=int(s.get('x', 0)), y=int(s.get('y', 0)),
            z=int(s.get('z', 0)), alive=new_alive, hits=new_hits)
        if ok:
            self._push_history(f"{sid}  {action}")

    def _push_history(self, line):
        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        self.override_history.append(entry)
        self.override_history = self.override_history[-100:]
        T = self.history_text
        T.configure(state=NORMAL)
        T.delete("1.0", END)
        for e in reversed(self.override_history[-20:]):
            T.insert(END, e + "\n")
        T.configure(state=DISABLED)

    # ------------------------------------------------- legend (modal) -----

    def open_legend(self):
        pal, fnt = self.pal, self.fnt
        if getattr(self, "_legend_win", None) and \
                self._legend_win.winfo_exists():
            self._legend_win.lift()
            self._legend_win.focus_force()
            return
        win = Toplevel(self.root)
        self._legend_win = win
        win.title("Справка · Типы кораблей")
        win.configure(bg=pal.bg_root)
        win.transient(self.root)
        win.geometry("760x620+120+60")

        head = Frame(win, bg=pal.bg_root)
        head.pack(fill=X, padx=20, pady=(16, 6))
        Label(head, text="📖  Справочник по типам кораблей",
              bg=pal.bg_root, fg=pal.fg_title, font=fnt.h1
              ).pack(side=LEFT)
        Button(head, text="✕", bg=pal.bg_card, fg=pal.fg_primary,
               activebackground=pal.border_strong,
               font=fnt.h3, bd=0, relief=FLAT, cursor="hand2", width=3,
               command=win.destroy).pack(side=RIGHT)

        canvas = Canvas(win, bg=pal.bg_root, highlightthickness=0)
        canvas.pack(side=LEFT, fill=BOTH, expand=True, padx=(20, 0), pady=4)
        sb = ttk.Scrollbar(win, orient=VERTICAL, command=canvas.yview)
        sb.pack(side=RIGHT, fill=Y, padx=(0, 20), pady=4)
        canvas.configure(yscrollcommand=sb.set)
        inner = Frame(canvas, bg=pal.bg_root)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e, c=canvas, i=inner_id:
                    c.itemconfigure(i, width=e.width))
        for wheel_ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(
                wheel_ev,
                lambda e, c=canvas: self._scroll(e, c), add="+")

        order = ["Прыгун", "Артиллерия", "Бурав", "Факел", "Тишина",
                 "Провокатор", "Паук", "Радиовышка", "Крейсер"]
        for typ in order:
            info = SHIP_TYPE_INFO.get(typ)
            if not info:
                continue
            self._render_legend_card(inner, typ, info, pal, fnt)

        footer = Frame(win, bg=pal.bg_root)
        footer.pack(fill=X, padx=20, pady=(0, 14), side=BOTTOM)
        Label(footer, text="Победа: уничтожить корабли всех других команд. "
                           "При таймауте (30 ходов) побеждает команда с "
                           "наибольшим нанесённым уроном.",
              bg=pal.bg_root, fg=pal.fg_secondary, font=fnt.small,
              wraplength=700, justify=LEFT).pack(anchor=W)
        win.bind("<Escape>", lambda e: win.destroy())

    def _render_legend_card(self, parent, typ, info, pal, fnt):
        card = Frame(parent, bg=pal.bg_card, bd=0,
                     highlightthickness=1, highlightbackground=pal.border)
        card.pack(fill=X, padx=10, pady=6)
        stripe = Canvas(card, width=6, height=1,
                        bg=info.get("accent", pal.accent_info),
                        highlightthickness=0)
        stripe.pack(side=LEFT, fill=Y)
        body = Frame(card, bg=pal.bg_card)
        body.pack(side=LEFT, fill=BOTH, expand=True, padx=10, pady=8)

        head = Frame(body, bg=pal.bg_card)
        head.pack(fill=X)
        Label(head, text=info.get("icon", "🛰"), bg=pal.bg_card,
              fg=info.get("accent", pal.accent_info), font=fnt.h1
              ).pack(side=LEFT, padx=(0, 10))
        Label(head, text=typ, bg=pal.bg_card, fg=pal.fg_title,
              font=fnt.h2).pack(side=LEFT)
        Label(head, text="  · " + info.get("role", ""),
              bg=pal.bg_card, fg=pal.fg_secondary, font=fnt.small
              ).pack(side=LEFT)

        stats = info.get("stats") or {}
        if stats:
            st = Frame(body, bg=pal.bg_card)
            st.pack(fill=X, pady=(4, 2))
            for k, v in stats.items():
                pill = Frame(st, bg=pal.bg_panel, bd=0)
                pill.pack(side=LEFT, padx=(0, 6))
                Label(pill, text=f"{k}", bg=pal.bg_panel,
                      fg=pal.fg_muted, font=fnt.small
                      ).pack(side=LEFT, padx=(6, 2), pady=2)
                Label(pill, text=f"{v}", bg=pal.bg_panel,
                      fg=pal.fg_primary, font=fnt.small_bold
                      ).pack(side=LEFT, padx=(0, 6), pady=2)
        for line in info.get("abilities") or []:
            Label(body, text=f"• {line}", bg=pal.bg_card,
                  fg=pal.fg_primary, font=fnt.small, justify=LEFT,
                  anchor=W).pack(anchor=W, pady=1)

    # ----------------------------------------------- gm send + timers -----

    def send_gm_command(self, command, **payload):
        if not self.connected or self.framed is None:
            messagebox.showwarning("Нет соединения", "Не подключен к серверу")
            return False
        msg = {'type': 'gm_command', 'command': command}
        msg.update(payload)
        try:
            self.framed.send(msg)
        except ProtocolError as e:
            messagebox.showerror("Ошибка", f"Не удалось отправить: {e}")
            self.connected = False
            return False
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось отправить: {e}")
            return False
        return True

    def tick_timer(self):
        try:
            state = self.current_state
            if state is not None and state.get('phase') == 'planning':
                deadline = state.get('planning_deadline')
                if deadline:
                    remaining = max(0, int(deadline - time.time()))
                    received = state.get('actions_received_teams', [])
                    connected = state.get('connected_teams', [])
                    self.timer_label.config(
                        text=f"⏱ {remaining}с  ·  сборы "
                             f"{len(received)}/{len(connected)}")
        finally:
            self.root.after(500, self.tick_timer)

    # ------------------------------------------------------------ run -----

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    GameMasterGUI().run()
