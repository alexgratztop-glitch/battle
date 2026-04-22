"""Единая визуальная тема для клиента игрока, GM-панели и вспомогательных окон.

Централизуем цветовую палитру, типографику, цвета команд, иконки типов
кораблей и базовый `ttk.Style`, чтобы:

1. Избавиться от россыпи хардкодных ``bg=/fg=`` в коде клиентов
   (сейчас их > 270 на Tkinter-слое).
2. Давать согласованные акценты между всеми окнами (команды A/B/C
   везде одного цвета, HP-бар — тех же оттенков, фокус-стиль кнопок
   одинаков).
3. Иметь одну точку правки темы — поменять здесь и сразу видно во всех
   окнах.

Модуль не зависит от логики игры и может импортироваться где угодно.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# --------------------------------------------------------------------------- #
# Цветовая палитра.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Palette:
    """Основные цвета тёмной космической темы."""

    # Фоны (от самого тёмного к самому светлому «слою»).
    bg_root: str = "#0a0e27"        # основной фон окон.
    bg_panel: str = "#151a33"       # фон панелей/групп.
    bg_card: str = "#1a2140"        # фон «карточек» (корабль, лог-запись).
    bg_map: str = "#0c1030"         # фон игрового поля 10×10.
    bg_cell_empty: str = "#16204a"  # пустая клетка карты.
    bg_cell_hover: str = "#22306a"  # клетка под курсором.
    bg_cell_legal_move: str = "#1f3a5e"   # подсветка легального хода.
    bg_cell_legal_shoot: str = "#5c1f1f"  # подсветка легальной стрельбы/тарана.
    bg_cell_selected: str = "#f4c430"     # выбранная клетка-цель.

    # Тексты.
    fg_primary: str = "#e8ecff"     # основной текст.
    fg_secondary: str = "#9aa3c7"   # подписи, пояснения.
    fg_muted: str = "#606a8a"       # отключённый/неактивный текст.
    fg_title: str = "#ffffff"       # заголовки.

    # Семантические акценты.
    accent_info: str = "#00d4ff"     # информация/ссылки.
    accent_success: str = "#6bff9d"  # успех/живой/полный HP.
    accent_warning: str = "#ffd24a"  # предупреждение/среднее HP.
    accent_danger: str = "#ff5c7a"   # опасность/низкий HP/смерть.
    accent_phase: str = "#b57bff"    # фаза Тишины.
    accent_mine: str = "#ff9a3c"     # мины/взрывы.
    accent_heal: str = "#6bff9d"     # лечение (тот же тон, что success).

    # Бордюры/разделители.
    border: str = "#2a355f"
    border_strong: str = "#3e4c84"


@dataclass(frozen=True)
class Fonts:
    """Единая типографическая шкала.

    В Tkinter задаём кортежами ``(family, size, style)``.
    """

    family_sans: str = "DejaVu Sans"
    family_mono: str = "DejaVu Sans Mono"
    # Заголовки.
    h1: tuple = ("DejaVu Sans", 18, "bold")
    h2: tuple = ("DejaVu Sans", 14, "bold")
    h3: tuple = ("DejaVu Sans", 12, "bold")
    # Основной текст.
    body: tuple = ("DejaVu Sans", 11)
    body_bold: tuple = ("DejaVu Sans", 11, "bold")
    small: tuple = ("DejaVu Sans", 9)
    small_bold: tuple = ("DejaVu Sans", 9, "bold")
    # Карта/цифры в клетках.
    cell_icon: tuple = ("DejaVu Sans", 16, "bold")
    cell_label: tuple = ("DejaVu Sans Mono", 9, "bold")
    # Лог боя (моноширинный, чтобы значения выравнивались).
    log: tuple = ("DejaVu Sans Mono", 10)


# --------------------------------------------------------------------------- #
# Команды.
# --------------------------------------------------------------------------- #

# Цвет команды — постоянный во всех окнах. Оттенки подобраны так, чтобы
# хорошо читались на тёмном фоне и различались даже при цветовой слепоте.
TEAM_COLORS: Dict[str, str] = {
    "Team A": "#4a9dff",   # голубой
    "Team B": "#ff5c7a",   # коралловый
    "Team C": "#6bff9d",   # мятный
}

# Цвет-контур для «своей» команды в клиенте игрока (используем тот же
# TEAM_COLORS — просто ссылка). Враги в клиенте подсвечиваются
# сплошным акцентом danger, свои — цветом их команды.


# --------------------------------------------------------------------------- #
# Иконки и цвета типов кораблей.
# --------------------------------------------------------------------------- #

# Короткая буква (Ru-алфавит) + эмодзи-иконка. Карта показывает обе:
# буква всегда читаема даже при отключении эмодзи-шрифта.
SHIP_TYPE_INFO: Dict[str, dict] = {
    "Крейсер":     {"short": "К",  "icon": "🚀", "role": "боец ближнего боя",
                    "accent": "#4a9dff",
                    "stats": {"HP": 2, "move": 2, "атака": "2 в r=5"},
                    "abilities": ["Может убивать артиллерию/крейсера за 1 выстрел"]},
    "Артиллерия":  {"short": "А",  "icon": "💥", "role": "дальний урон (стреляет куда угодно)",
                    "accent": "#ffd24a",
                    "stats": {"HP": 1, "move": 0, "атака": "2 безлимит"},
                    "abilities": ["🎯 Стреляет в любую клетку карты",
                                  "⚠ Сама не двигается — легко убить тараном"]},
    "Радиовышка":  {"short": "Р",  "icon": "📡", "role": "сканирует свой Z-слой",
                    "accent": "#6bff9d",
                    "stats": {"HP": 3, "move": 2, "атака": "—"},
                    "abilities": ["📡 Видит всех врагов в своей Z-плоскости",
                                  "⚠ Стрелять не умеет"]},
    "Прыгун":      {"short": "П",  "icon": "🌀", "role": "таран через корабли (jump)",
                    "accent": "#ff9a3c",
                    "stats": {"HP": 2, "move": 2, "атака": "1 в r=5"},
                    "abilities": ["🌀 Прыгок/таран через корабли на 2 клетки",
                                  "💥 Таран пробивает фазу Тишины"]},
    "Факел":       {"short": "Ф",  "icon": "🔥", "role": "AoE-лечение союзников",
                    "accent": "#6bff9d",
                    "stats": {"HP": 6, "move": 2, "атака": "1 в r=5"},
                    "abilities": ["🔥 HEAL: +1 HP всем союзникам в r=2",
                                  "🪶 Самый живучий корабль команды"]},
    "Тишина":      {"short": "Т",  "icon": "👻", "role": "фаза — временная неуязвимость",
                    "accent": "#b57bff",
                    "stats": {"HP": 2, "move": 2, "атака": "—"},
                    "abilities": ["👻 PHASE: 1 ход неуязвимости, кулдаун 3",
                                  "⚠ Таран Прыгуна/Бурава пробивает фазу"]},
    "Бурав":       {"short": "У",  "icon": "⚙",  "role": "таран по оси/диагонали",
                    "accent": "#ff9a3c",
                    "stats": {"HP": 4, "move": 3, "атака": "—"},
                    "abilities": ["⚙ DRILL: таран по оси/2D-диагонали на 3 клетки",
                                  "💥 Пробивает фазу Тишины"]},
    "Провокатор":  {"short": "Пр", "icon": "🎭", "role": "голограммы-приманки",
                    "accent": "#b57bff",
                    "stats": {"HP": 2, "move": 2, "атака": "1 в r=5"},
                    "abilities": ["🎭 HOLOGRAM: фальшивый корабль-приманка",
                                  "🧠 Отвлекает врага от настоящих целей"]},
    "Паук":        {"short": "С",  "icon": "🕷", "role": "ставит мины",
                    "accent": "#ff5c7a",
                    "stats": {"HP": 3, "move": 2, "атака": "—"},
                    "abilities": ["🕷 MINE: мина в клетке, взрыв при входе -2 HP",
                                  "⚠ Мина невидима врагу"]},
    "Базовый":     {"short": "Б",  "icon": "🛰", "role": "базовая единица",
                    "accent": "#9aa3c7",
                    "stats": {"HP": 2, "move": 1, "атака": "1 в r=5"},
                    "abilities": []},
}


def ship_icon(ship_type: str) -> str:
    """Эмодзи-иконка по названию типа. Дефолт — кораблик."""
    return SHIP_TYPE_INFO.get(ship_type, {}).get("icon", "🛰")


def ship_short(ship_type: str) -> str:
    """Короткая русская буква по типу (для клеток карты)."""
    return SHIP_TYPE_INFO.get(ship_type, {}).get("short", "?")


def ship_role(ship_type: str) -> str:
    """Однострочное описание роли — используется в легенде и tooltip'ах."""
    return SHIP_TYPE_INFO.get(ship_type, {}).get("role", "")


def ship_accent(ship_type: str) -> str:
    """Цвет-акцент для карточки/рамки корабля в списках и тултипах."""
    return SHIP_TYPE_INFO.get(ship_type, {}).get("accent", Palette.fg_secondary)


# --------------------------------------------------------------------------- #
# HP-бар.
# --------------------------------------------------------------------------- #

def hp_color(hp: int, max_hp: int, palette: Palette | None = None) -> str:
    """Цвет HP-бара по отношению hp/max_hp: зелёный → жёлтый → красный."""
    p = palette or Palette()
    if max_hp <= 0:
        return p.fg_muted
    ratio = max(0.0, min(1.0, hp / max_hp))
    if ratio >= 0.66:
        return p.accent_success
    if ratio >= 0.33:
        return p.accent_warning
    return p.accent_danger


# --------------------------------------------------------------------------- #
# ttk.Style — базовая установка.
# --------------------------------------------------------------------------- #

def apply_theme(root, palette: Palette | None = None,
                fonts: Fonts | None = None) -> tuple[Palette, Fonts]:
    """Применяет единый ttk.Style и возвращает использованные
    (palette, fonts), чтобы вызывающий мог ссылаться на конкретные цвета.

    Для Tk-виджетов (Label/Frame/Button) Tk стили не распространяются —
    там используем явные ``bg``/``fg`` из палитры. Этот стиль покрывает
    ``ttk.*`` (Notebook, Progressbar, Treeview, Combobox, Scale).
    """
    from tkinter import ttk

    p = palette or Palette()
    f = fonts or Fonts()
    root.configure(bg=p.bg_root)
    style = ttk.Style(root)
    # clam — самая настраиваемая встроенная тема.
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background=p.bg_panel, foreground=p.fg_primary,
                    font=f.body, bordercolor=p.border,
                    lightcolor=p.bg_panel, darkcolor=p.bg_panel,
                    troughcolor=p.bg_root, focuscolor=p.accent_info)
    style.configure("TFrame", background=p.bg_panel)
    style.configure("Card.TFrame", background=p.bg_card, relief="flat")
    style.configure("TLabel", background=p.bg_panel, foreground=p.fg_primary,
                    font=f.body)
    style.configure("Muted.TLabel", foreground=p.fg_secondary,
                    background=p.bg_panel, font=f.small)
    style.configure("Title.TLabel", font=f.h2, foreground=p.fg_title,
                    background=p.bg_panel)
    style.configure("H1.TLabel", font=f.h1, foreground=p.fg_title,
                    background=p.bg_panel)
    style.configure("TButton", background=p.bg_card, foreground=p.fg_primary,
                    font=f.body_bold, borderwidth=1, focusthickness=2,
                    padding=(10, 6))
    style.map("TButton",
              background=[("active", p.border_strong),
                          ("pressed", p.border_strong)],
              foreground=[("disabled", p.fg_muted)])
    style.configure("Accent.TButton", background=p.accent_info,
                    foreground="#06122a", font=f.body_bold, padding=(12, 8))
    style.map("Accent.TButton",
              background=[("active", "#3be6ff"), ("pressed", "#00aacc")])
    style.configure("Danger.TButton", background=p.accent_danger,
                    foreground="#1a0512", font=f.body_bold, padding=(12, 8))
    style.configure("TLabelframe", background=p.bg_panel,
                    foreground=p.accent_info, bordercolor=p.border,
                    font=f.h3)
    style.configure("TLabelframe.Label", background=p.bg_panel,
                    foreground=p.accent_info, font=f.h3)
    style.configure("Horizontal.TProgressbar", background=p.accent_success,
                    troughcolor=p.bg_root, bordercolor=p.border,
                    lightcolor=p.accent_success, darkcolor=p.accent_success)
    style.configure("Treeview", background=p.bg_card,
                    fieldbackground=p.bg_card, foreground=p.fg_primary,
                    font=f.body)
    style.configure("Treeview.Heading", background=p.bg_panel,
                    foreground=p.fg_title, font=f.h3)
    style.configure("TNotebook", background=p.bg_root, borderwidth=0)
    style.configure("TNotebook.Tab", background=p.bg_panel,
                    foreground=p.fg_secondary, padding=(14, 6),
                    font=f.body_bold)
    style.map("TNotebook.Tab",
              background=[("selected", p.bg_card)],
              foreground=[("selected", p.accent_info)])
    style.configure("TEntry", fieldbackground=p.bg_card,
                    foreground=p.fg_primary, bordercolor=p.border,
                    insertcolor=p.fg_primary)
    style.configure("TCombobox", fieldbackground=p.bg_card,
                    background=p.bg_card, foreground=p.fg_primary,
                    selectbackground=p.bg_card,
                    selectforeground=p.fg_primary)
    style.configure("TScale", background=p.bg_panel, troughcolor=p.bg_root)
    return p, f


__all__ = [
    "Palette", "Fonts", "TEAM_COLORS",
    "SHIP_TYPE_INFO", "ship_icon", "ship_short", "ship_role", "ship_accent",
    "hp_color", "apply_theme",
]
