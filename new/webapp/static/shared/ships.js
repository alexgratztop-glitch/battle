// Ship catalog, ported from ui_theme.SHIP_TYPE_INFO (Python).
// Single source of truth for the browser. Keep keys in sync with the backend.
export const SHIP_TYPE_INFO = {
  "Крейсер": {
    short: "К", icon: "🚀", role: "боец ближнего боя",
    accent: "#4a9dff",
    stats: { HP: 2, move: 2, "атака": "2 в r=5" },
    abilities: ["Может убивать артиллерию/крейсера за 1 выстрел"],
  },
  "Артиллерия": {
    short: "А", icon: "💥", role: "дальний урон (стреляет куда угодно)",
    accent: "#ffd24a",
    stats: { HP: 1, move: 0, "атака": "2 безлимит" },
    abilities: [
      "🎯 Стреляет в любую клетку карты",
      "⚠ Сама не двигается — легко убить тараном",
    ],
  },
  "Радиовышка": {
    short: "Р", icon: "📡", role: "сканирует свой Z-слой",
    accent: "#6bff9d",
    stats: { HP: 3, move: 2, "атака": "—" },
    abilities: [
      "📡 Видит всех врагов в своей Z-плоскости",
      "⚠ Стрелять не умеет",
    ],
  },
  "Прыгун": {
    short: "П", icon: "🌀", role: "таран через корабли (jump)",
    accent: "#ff9a3c",
    stats: { HP: 2, move: 2, "атака": "1 в r=5" },
    abilities: [
      "🌀 Прыгок/таран через корабли на 2 клетки",
      "💥 Таран пробивает фазу Тишины",
    ],
  },
  "Факел": {
    short: "Ф", icon: "🔥", role: "AoE-лечение союзников",
    accent: "#6bff9d",
    stats: { HP: 6, move: 2, "атака": "1 в r=5" },
    abilities: [
      "🔥 HEAL: +1 HP всем союзникам в r=2",
      "🪶 Самый живучий корабль команды",
    ],
  },
  "Тишина": {
    short: "Т", icon: "👻", role: "фаза — временная неуязвимость",
    accent: "#b57bff",
    stats: { HP: 2, move: 2, "атака": "—" },
    abilities: [
      "👻 PHASE: 1 ход неуязвимости, кулдаун 3",
      "⚠ Таран Прыгуна/Бурава пробивает фазу",
    ],
  },
  "Бурав": {
    short: "У", icon: "⚙", role: "таран по оси/диагонали",
    accent: "#ff9a3c",
    stats: { HP: 4, move: 3, "атака": "—" },
    abilities: [
      "⚙ DRILL: таран по оси/2D-диагонали на 3 клетки",
      "💥 Пробивает фазу Тишины",
    ],
  },
  "Провокатор": {
    short: "Пр", icon: "🎭", role: "голограммы-приманки",
    accent: "#b57bff",
    stats: { HP: 2, move: 2, "атака": "1 в r=5" },
    abilities: [
      "🎭 HOLOGRAM: фальшивый корабль-приманка",
      "🧠 Отвлекает врага от настоящих целей",
    ],
  },
  "Паук": {
    short: "С", icon: "🕷", role: "ставит мины",
    accent: "#ff5c7a",
    stats: { HP: 3, move: 2, "атака": "—" },
    abilities: [
      "🕷 MINE: мина в клетке, взрыв при входе -2 HP",
      "⚠ Мина невидима врагу",
    ],
  },
  "Базовый": {
    short: "Б", icon: "🛰", role: "базовая единица",
    accent: "#9aa3c7",
    stats: { HP: 2, move: 1, "атака": "1 в r=5" },
    abilities: [],
  },
};

export const POOL_PICKABLE = [
  "Артиллерия", "Радиовышка", "Прыгун", "Факел",
  "Тишина", "Бурав", "Провокатор", "Паук", "Крейсер",
];

export function shipIcon(type) {
  return (SHIP_TYPE_INFO[type] || {}).icon || "🛰";
}

export function hpClass(hits, max) {
  if (!max) return "hp-low";
  const ratio = 1 - hits / max;
  if (ratio >= 0.66) return "hp-high";
  if (ratio >= 0.33) return "hp-mid";
  return "hp-low";
}

export function teamClass(letter) {
  return `team-${letter || "A"}`;
}

export const TEAM_COLORS = {
  A: "#4a9dff",
  B: "#ff5c7a",
  C: "#6bff9d",
};
