"""Проводит серию симуляций и формирует `game_logs/summary.md`.

Использование::

    python run_tournament.py --games 20 --mode advanced --max-turns 30

По умолчанию — 20 партий с сидами 1..20 в advanced-режиме.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

from simulate_game import simulate


TEAMS = ("Team A", "Team B", "Team C")


def _fmt_bytype(d: dict) -> str:
    if not d:
        return "—"
    return "; ".join(f"{k}:{v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def run_tournament(
    games: int = 20,
    mode: str = "advanced",
    max_turns: int = 30,
    start_seed: int = 1,
) -> str:
    results = []
    for i in range(games):
        seed = start_seed + i
        r = simulate(max_turns=max_turns, seed=seed, game_mode=mode, write_log=True)
        results.append(r)
        print(
            f"[{i + 1:>2}/{games}] seed={seed:>3} winner={r['winner']:>6} "
            f"turns={r['turns']:>2} damage={r['total_damage']:>3}"
        )

    lines = []
    lines.append(f"# Турнир: {games} игр ({mode}, max_turns={max_turns})")
    lines.append("")
    lines.append(
        "Симуляции headless (3 бота-команды + GM-бот), детерминированные по сиду. "
        "Действия каждого бота используют все 6 способностей (HEAL / PHASE / "
        "HOLOGRAM / MINE / jump-ram / drill-ram) плюс стандартные MOVE/SHOOT."
    )
    lines.append("")

    # ---- Сводная таблица по партиям ---------------------------------------
    lines.append("## Таблица по партиям")
    lines.append("")
    header = [
        "Seed", "Winner", "Turns",
        "A живых", "B живых", "C живых",
        "A hp", "B hp", "C hp",
        "A урон", "B урон", "C урон",
        "A хиты", "B хиты", "C хиты",
        "A тараны", "B тараны", "C тараны",
        "A мины(×)", "B мины(×)", "C мины(×)",
        "A heal", "B heal", "C heal",
        "A phase", "B phase", "C phase",
        "A holo", "B holo", "C holo",
        "A mine(пост)", "B mine(пост)", "C mine(пост)",
        "A kills", "B kills", "C kills",
        "A move", "B move", "C move",
        "A shoot", "B shoot", "C shoot",
        "Σ урон", "Σ урон/ход",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for r in results:
        st = r["stats"]
        row = [
            str(r["seed"]), r["winner"] or "—", str(r["turns"]),
            *(str(r["survivors"][t]) for t in TEAMS),
            *(str(r["remaining_hp"][t]) for t in TEAMS),
            *(str(st[t]["damage_dealt"]) for t in TEAMS),
            *(str(st[t]["shoot_hits"]) for t in TEAMS),
            *(str(st[t]["rams"]) for t in TEAMS),
            *(str(st[t]["mines_triggered"]) for t in TEAMS),
            *(str(st[t]["heals"]) for t in TEAMS),
            *(str(st[t]["phase_actions"]) for t in TEAMS),
            *(str(st[t]["hologram_actions"]) for t in TEAMS),
            *(str(st[t]["mines_placed"]) for t in TEAMS),
            *(str(st[t]["kills"]) for t in TEAMS),
            *(str(st[t]["move_actions"]) for t in TEAMS),
            *(str(st[t]["shoot_actions"]) for t in TEAMS),
            str(r["total_damage"]),
            f"{r['avg_damage_per_turn']:.2f}",
        ]
        lines.append("| " + " | ".join(row) + " |")

    # ---- Агрегаты ---------------------------------------------------------
    lines.append("")
    lines.append("## Агрегированная статистика (по всем играм)")
    lines.append("")
    wins = Counter(r["winner"] for r in results)
    total_turns = sum(r["turns"] for r in results)
    total_damage = sum(r["total_damage"] for r in results)

    lines.append(f"- Всего партий: **{games}**")
    lines.append(
        "- Победителей: "
        + ", ".join(f"{w}: {c}" for w, c in sorted(wins.items()))
    )
    lines.append(f"- Суммарно ходов: **{total_turns}**, среднее: **{total_turns / games:.2f}**")
    lines.append(f"- Суммарный урон: **{total_damage}**, среднее/партия: **{total_damage / games:.2f}**")
    lines.append("")

    lines.append("### Вклад команд (суммарно по всем играм)")
    lines.append("")
    header2 = [
        "Команда",
        "Урон", "Хиты", "Тараны", "Мины(сработ)",
        "Heal", "Phase", "Hologram", "Mine(поставл)",
        "Убийств", "Шагов MOVE", "Выстрелов",
        "Убитые типы", "Потерянные типы",
    ]
    lines.append("| " + " | ".join(header2) + " |")
    lines.append("|" + "|".join(["---"] * len(header2)) + "|")
    for t in TEAMS:
        dmg = sum(r["stats"][t]["damage_dealt"] for r in results)
        hits = sum(r["stats"][t]["shoot_hits"] for r in results)
        rams = sum(r["stats"][t]["rams"] for r in results)
        mt = sum(r["stats"][t]["mines_triggered"] for r in results)
        he = sum(r["stats"][t]["heals"] for r in results)
        ph = sum(r["stats"][t]["phase_actions"] for r in results)
        ho = sum(r["stats"][t]["hologram_actions"] for r in results)
        mp = sum(r["stats"][t]["mines_placed"] for r in results)
        kl = sum(r["stats"][t]["kills"] for r in results)
        mv = sum(r["stats"][t]["move_actions"] for r in results)
        sh = sum(r["stats"][t]["shoot_actions"] for r in results)
        kbt = Counter()
        dbt = Counter()
        for r in results:
            for k, v in r["stats"][t]["kills_by_ship_type"].items():
                kbt[k] += v
            for k, v in r["stats"][t]["deaths_by_ship_type"].items():
                dbt[k] += v
        row = [
            t, str(dmg), str(hits), str(rams), str(mt),
            str(he), str(ph), str(ho), str(mp),
            str(kl), str(mv), str(sh),
            _fmt_bytype(kbt), _fmt_bytype(dbt),
        ]
        lines.append("| " + " | ".join(row) + " |")

    # ---- Таблица по типам кораблей (для балансировки) --------------------
    lines.append("")
    lines.append("## Таблица по типам кораблей (балансировка)")
    lines.append("")
    lines.append(
        "Агрегировано по всем трём командам во всех партиях. На каждую партию "
        "выставляется по 3 корабля каждого типа (всего 9 за игру), поэтому "
        f"«Всего» ≈ количество экземпляров типа во всех {games} играх. "
        "«Урон/корабль» — средний урон, который корабль этого типа нанёс за "
        "партию (damage_dealt / deployed). «Убийств/смертей» — как часто "
        "корабль этого типа убивает / гибнет (на 1 экз.). «Выживаемость» — "
        "доля экземпляров, доживших до конца партии. «HP %» — доля "
        "оставшегося hp у выживших (hp_left / max_hp среди выживших)."
    )
    lines.append("")
    type_header = [
        "Тип", "Всего", "Выжило", "Выживаемость", "HP %",
        "Урон нанёс", "Урон принял", "Урон/корабль",
        "Убийств", "Смертей", "K/D",
        "Попаданий", "Таранов+", "Мин сработ+", "Мин по нему", "Hеaled+", "Healed−",
        "MOVE", "SHOOT", "HEAL", "PHASE", "HOLO", "MINE", "SKIP",
    ]
    lines.append("| " + " | ".join(type_header) + " |")
    lines.append("|" + "|".join(["---"] * len(type_header)) + "|")

    # Собираем агрегат по типу.
    agg_type: dict[str, dict] = {}
    for r in results:
        for tp, s in r.get("type_stats", {}).items():
            if tp not in agg_type:
                agg_type[tp] = {
                    k: 0 for k in s.keys()
                }
            for k, v in s.items():
                agg_type[tp][k] = agg_type[tp].get(k, 0) + v

    # Сортируем по «весу»: убийства + damage_dealt.
    def _sort_key(kv):
        tp, s = kv
        return -(s.get("kills", 0) * 10 + s.get("damage_dealt", 0))

    for tp, s in sorted(agg_type.items(), key=_sort_key):
        deployed = s.get("deployed", 0)
        survived_hp = s.get("survivor_hp_sum", 0)
        max_hp_sum = s.get("survivor_hp_max_sum", 0)
        # «выжило» = сколько раз корабль дожил до конца партии.
        # У нас нет прямого счётчика, считаем через сумму max_hp выживших / max_hp одного экз.
        # Но max_hp зависит только от типа, поэтому: survived = survivor_hp_max_sum / per_ship_max_hp.
        # Нам доступно только суммарное max_hp. Узнаем через deployed — pr game на команду 1.
        # Проще: max_hp одного экземпляра = max_hp_sum / survivor_count, но сами хотим survivor_count.
        # Альтернатива: считать deaths, и survivor = deployed - deaths.
        deaths = s.get("deaths", 0)
        survived = max(deployed - deaths, 0)
        surv_rate = (survived / deployed * 100.0) if deployed else 0.0
        hp_pct = (survived_hp / max_hp_sum * 100.0) if max_hp_sum else 0.0
        dmg_per_ship = (s.get("damage_dealt", 0) / deployed) if deployed else 0.0
        kills_per_ship = (s.get("kills", 0) / deployed) if deployed else 0.0
        deaths_per_ship = (deaths / deployed) if deployed else 0.0
        kd = (s.get("kills", 0) / deaths) if deaths else float('inf') if s.get("kills", 0) else 0.0
        kd_str = "∞" if kd == float('inf') else f"{kd:.2f}"
        row = [
            tp,
            str(deployed),
            str(survived),
            f"{surv_rate:.1f}%",
            f"{hp_pct:.0f}%" if max_hp_sum else "—",
            str(s.get("damage_dealt", 0)),
            str(s.get("damage_taken", 0)),
            f"{dmg_per_ship:.2f}",
            f"{s.get('kills', 0)} ({kills_per_ship:.2f})",
            f"{deaths} ({deaths_per_ship:.2f})",
            kd_str,
            str(s.get("shots_hit", 0)),
            str(s.get("rams_scored", 0)),
            str(s.get("mines_dealt", 0)),
            str(s.get("mines_received", 0)),
            f"{s.get('heals_given', 0):.0f}",
            str(s.get("heals_received", 0)),
            str(s.get("action_move", 0)),
            str(s.get("action_shoot", 0)),
            str(s.get("action_heal", 0)),
            str(s.get("action_phase", 0)),
            str(s.get("action_hologram", 0)),
            str(s.get("action_mine", 0)),
            str(s.get("skip_turns", 0)),
        ]
        lines.append("| " + " | ".join(row) + " |")

    # ---- Побочные метрики -------------------------------------------------
    lines.append("")
    lines.append("### Дополнительно")
    lines.append("")

    decisive = [r for r in results if r["winner"] in TEAMS]
    draws = [r for r in results if r["winner"] not in TEAMS]
    lines.append(f"- Победы до лимита ходов: **{len(decisive)}** / ничьих/патов: **{len(draws)}**")
    if decisive:
        avg_turns_decisive = sum(r["turns"] for r in decisive) / len(decisive)
        lines.append(f"- Ср. ходов в результативных играх: **{avg_turns_decisive:.2f}**")
    # Топ-3 самых «боевых» партий по суммарному урону
    top_dmg = sorted(results, key=lambda r: r["total_damage"], reverse=True)[:3]
    lines.append(
        "- Самые боевые партии (урон/сид/победитель): "
        + ", ".join(f"{r['total_damage']}@seed={r['seed']}→{r['winner']}" for r in top_dmg)
    )
    quiet = sorted(results, key=lambda r: r["total_damage"])[:3]
    lines.append(
        "- Самые «тихие» партии (мин. урон): "
        + ", ".join(f"{r['total_damage']}@seed={r['seed']}→{r['winner']}" for r in quiet)
    )

    # ---- Путь к логам -----------------------------------------------------
    lines.append("")
    lines.append("## Логи партий")
    lines.append("")
    for r in results:
        rel = os.path.relpath(r["log_path"], start=os.path.dirname(os.path.abspath(__file__)))
        lines.append(f"- seed={r['seed']}: `{rel}`")

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "game_logs", "summary.md"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--mode", choices=("advanced", "basic"), default="advanced")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--start-seed", type=int, default=1)
    args = parser.parse_args()

    path = run_tournament(
        games=args.games,
        mode=args.mode,
        max_turns=args.max_turns,
        start_seed=args.start_seed,
    )
    print("\nSummary:", path)
