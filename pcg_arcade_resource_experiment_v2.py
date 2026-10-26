#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сравнение штатного плейтестинга PCG Benchmark Arcade Rules с ресурсной проверкой.

Установка: pip install pcg_benchmark numpy pandas pillow
Запуск: python pcg_arcade_resource_experiment.py --n 200 --seed 2026 --output ./arcade_results

Внешний движок НЕ подменяется собственной реализацией. Функция env.info(content)
запускает штатные DoNothing, Random и FlatMCTS. Метод baseline и гибрид
интерпретируют ОДНИ И ТЕ ЖЕ трассы. Проверяются только score-win типы 1/2/3.

Внимание: количество испытаний и оценок в двух методах одинаковое, но гибрид
дополнительно тратит CPU на статическую оценку; отдельно измеряем этот расход.
"""
from __future__ import annotations

import argparse
import csv
import importlib.metadata as metadata
import hashlib
import json
import platform
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

COLORS = ("red", "green", "yellow")
PAIRS = (
    ("player", "red"), ("player", "green"), ("player", "yellow"),
    ("red", "red"), ("red", "green"), ("red", "yellow"),
    ("green", "green"), ("green", "yellow"), ("yellow", "yellow"),
)
POINTS = (0, 1, 2, 4)
TARGET = {1: 5, 2: 10, 3: 20}


def as_native(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Cannot JSON encode {type(obj)}")


def arcade_layout(env):
    """Получить фиксированную карту Arcade Rules из содержащейся в PCGEnv задачи.

    В PCG Benchmark 0.1.0 PCGEnv оборачивает ArcadeRulesProblem:
    env._problem._layout. Публичного свойства для чтения фиксированной карты
    нет, поэтому это ЕДИНСТВЕННОЕ обращение скрипта к внутреннему атрибуту.
    При обновлении библиотеки интерфейс и доказательство нужно проверить снова.
    """
    problem = getattr(env, "_problem", None)
    layout = getattr(problem, "_layout", None)
    if layout is None:
        raise RuntimeError(
            "Не удалось найти карту Arcade Rules: ожидается "
            "env._problem._layout. Проверьте установленную версию PCG Benchmark."
        )
    layout = np.asarray(layout)
    if layout.ndim != 2:
        raise RuntimeError(f"Некорректная размерность карты Arcade Rules: {layout.shape}")
    return layout


def active_counts(content, layout):
    """Число реально созданных объектов с учётом непроходимых клеток карты.

    Проверено по Engine.initialize() официального Arcade Rules.
    """
    h, w = layout.shape
    counts = {"player": 1}
    for color in COLORS:
        starts = content[f"{color}Start"]
        current = 0
        for i in range(int(starts["num"])):
            x, y = int(starts["x"][i]), int(starts["y"][i])
            if 0 <= x < w and 0 <= y < h and layout[y, x] == 1:
                current += 1
        counts[color] = current
    return counts


def score_bounds(content, layout, max_time=40):
    """Две ДОКАЗУЕМЫЕ верхние границы очков для win in {1,2,3}.

    Каждая пара объектов проверяется Engine.updateCollision не более 1 раза
    за один игровой шаг. Для action == 0 пара может столкнуться не более T раз.
    Для action != 0 хотя бы один из пары гибнет НАВСЕГДА (flag killed=True),
    поэтому такая пара может дать очки не более одного раза за всю партию.
    Сумма позитивных верхних границ по парам безопасна независимо от геометрии,
    управления игрока, поведения объектов и порядка обработки коллизий.
    """
    mode = int(content["win"])
    if mode not in TARGET:
        return {"applicable": False, "reason": "time-win, not score-win"}
    n = active_counts(content, layout)
    coarse = 0
    resource = 0
    details = []
    for first, second in PAIRS:
        pair = f"{first}-{second}"
        rule = content[pair]
        reward = POINTS[int(rule["score"])]
        action = int(rule["action"])
        available_pairs = (n[first] * n[second] if first != second
                           else n[first] * (n[first] - 1) // 2)
        c0 = available_pairs * reward * max_time
        c1 = available_pairs * reward * (max_time if action == 0 else 1)
        coarse += c0
        resource += c1
        details.append({"pair": pair, "n_pairs": available_pairs,
                        "reward": reward, "action": action, "naive": c0, "bound": c1})
    assert resource <= coarse
    return {"applicable": True, "u_naive": coarse, "u_resource": resource,
            "threshold": TARGET[mode], "proven_impossible_naive": coarse < TARGET[mode],
            "proven_impossible_resource": resource < TARGET[mode],
            "counts": n, "details": details, "max_time": int(max_time)}


def observed_win(info):
    agents = ("do_nothing", "random", "flat_mcts")
    result = {}
    for agent in agents:
        trace = info.get(agent, [])
        result[agent] = bool(trace and trace[-1][0]["isWin"])
    return result


def status(negative, positive):
    if negative:
        return "PROVEN_UNREACHABLE_SCORE_WIN"
    if positive:
        return "WITNESSED_SCORE_WIN"
    return "INCONCLUSIVE"


def example_tests():
    """Модульные тесты НЕ заменяют запуск PCG Benchmark."""
    layout = np.ones((7, 7), dtype=int)
    content = {"win": 3}
    for name in COLORS:
        content[f"{name}Start"] = {"num": 0, "x": [0]*4, "y": [0]*4}
    for a, b in PAIRS:
        content[f"{a}-{b}"] = {"action": 0, "score": 0}
    content["redStart"] = {"num": 2, "x": [1, 2, 0, 0], "y": [1, 2, 0, 0]}
    content["player-red"] = {"action": 2, "score": 3}  # killSecond, +4 once / object
    bound = score_bounds(content, layout)
    assert bound["u_naive"] == 320, bound
    assert bound["u_resource"] == 8, bound
    assert bound["proven_impossible_resource"]
    assert not bound["proven_impossible_naive"]
    content["player-red"]["action"] = 0
    assert score_bounds(content, layout)["u_resource"] == 320
    content["win"] = 0
    assert not score_bounds(content, layout)["applicable"]


def sampled_configs(env, desired, rng_seed, layout, restricted_max_objects=None):
    """Фиксированная выборка правил из официального content_space без отбора по результату."""
    env.seed(rng_seed)
    yielded = 0
    attempts = 0
    seen = set()
    while yielded < desired:
        attempts += 1
        if attempts > 100000:
            raise RuntimeError("Too many rejected samples")
        c = env.content_space.sample()
        if int(c["win"]) not in TARGET:
            continue
        if layout[int(c["y"]), int(c["x"])] != 1:
            continue
        if restricted_max_objects is not None and any(
            int(c[f"{color}Start"]["num"]) > restricted_max_objects for color in COLORS
        ):
            continue
        string = json.dumps(c, sort_keys=True, default=as_native)
        digest = hashlib.sha256(string.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        yielded += 1
        yield f"arcade-{yielded:04}", c, digest


def run(n: int, seed: int, outdir: Path, max_objects: int | None):
    import pcg_benchmark
    from pcg_benchmark.probs.arcaderules import engine as arcade_engine
    from pcg_benchmark.probs.arcaderules import problem as arcade_problem
    outdir.mkdir(parents=True, exist_ok=True)
    random.seed(seed)
    np.random.seed(seed)
    env = pcg_benchmark.make("arcade-v0")
    layout = arcade_layout(env)
    rows, certificates, data = [], [], []
    start = time.perf_counter()
    for number, (name, content, digest) in enumerate(sampled_configs(env, n, seed, layout, max_objects), 1):
        # Основной эксперимент: ОБЕ схемы получают те же симуляционные трассы.
        t0 = time.perf_counter()
        sim_cpu_start = time.process_time()
        info = env.info(content)  # официальные DoNothing / Random / FlatMCTS
        sim_cpu_seconds = time.process_time() - sim_cpu_start
        sim_seconds = time.perf_counter() - t0
        t_quality = time.perf_counter()
        quality_cpu_start = time.process_time()
        # PCGEnv.quality возвращает (доля прошедших критерий, значение качества, info).
        # Для единственной конфигурации нужна именно вторая компонента.
        _, quality_value, _ = env.quality(info)
        official_quality = float(quality_value)
        quality_cpu_seconds = time.process_time() - quality_cpu_start
        quality_seconds = time.perf_counter() - t_quality
        t1 = time.perf_counter()
        static_cpu_start = time.process_time()
        check = score_bounds(content, layout, int(info["max_time"]))
        static_cpu_seconds = time.process_time() - static_cpu_start
        proof_seconds = time.perf_counter() - t1
        wins = observed_win(info)
        any_win = any(wins.values())
        baseline = status(False, any_win)
        hybrid = status(check["proven_impossible_resource"], any_win)
        naive = status(check["proven_impossible_naive"], any_win)
        if check["proven_impossible_resource"] and any_win:
            raise AssertionError(f"COUNTEREXAMPLE to proof for {name}: {digest}")
        row = {"id": name, "sha256": digest, "win_mode": int(content["win"]),
               "threshold": check["threshold"], "u_naive": check["u_naive"],
               "u_resource": check["u_resource"],
               "baseline_status": baseline, "coarse_status": naive, "hybrid_status": hybrid,
               "do_nothing_win": wins["do_nothing"], "random_win": wins["random"],
               "flat_mcts_win": wins["flat_mcts"],
               "official_quality": round(official_quality, 6),
               "engine_seconds": round(sim_seconds, 6), "quality_seconds": round(quality_seconds, 6),
               "static_seconds": round(proof_seconds, 6),
               "engine_cpu_seconds": round(sim_cpu_seconds, 9),
               "quality_cpu_seconds": round(quality_cpu_seconds, 9),
               "static_cpu_seconds": round(static_cpu_seconds, 9),
               "objects_red": check["counts"]["red"],
               "objects_green": check["counts"]["green"],
               "objects_yellow": check["counts"]["yellow"]}
        rows.append(row)
        data.append({"id": name, "content": content})
        if check["proven_impossible_resource"]:
            certificates.append({"id": name, "sha256": digest, "threshold": check["threshold"],
                                 "u_naive": check["u_naive"], "u_resource": check["u_resource"],
                                 "counts": check["counts"], "pair_wise_certificate": check["details"]})
        if number % 25 == 0 or number == n:
            print(f"{number}/{n} configs; proven={len(certificates)}; elapsed={time.perf_counter()-start:.1f}s", flush=True)
    with (outdir / "arcade_cases.json").open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=as_native)
    with (outdir / "arcade_proof_certificates.json").open("w", encoding="utf-8") as f:
        json.dump(certificates, f, ensure_ascii=False, indent=2, default=as_native)
    with (outdir / "arcade_results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    summary = {"n": n, "seed": seed, "sampling": "official arcade-v0 content_space with predeclared exclusions",
               "restriction_max_objects_per_color": max_objects,
               "baseline": dict(Counter(r["baseline_status"] for r in rows)),
               "coarse": dict(Counter(r["coarse_status"] for r in rows)),
               "hybrid": dict(Counter(r["hybrid_status"] for r in rows)),
               "extra_proofs_vs_coarse": sum(r["hybrid_status"] == "PROVEN_UNREACHABLE_SCORE_WIN" and
                                             r["coarse_status"] != "PROVEN_UNREACHABLE_SCORE_WIN" for r in rows),
               "simulation_seconds_total": sum(r["engine_seconds"] for r in rows),
               "additional_static_seconds_total": sum(r["static_seconds"] for r in rows),
               "official_quality_seconds_total": sum(r["quality_seconds"] for r in rows),
               "engine_source_sha256": hashlib.sha256(Path(arcade_engine.__file__).read_bytes()).hexdigest(),
               "problem_source_sha256": hashlib.sha256(Path(arcade_problem.__file__).read_bytes()).hexdigest(),
               "python": sys.version, "platform": platform.platform(),
               "pcg_benchmark_version": metadata.version('pcg_benchmark'),
               "cpu_time_seconds_total": {
                   "engine": sum(r["engine_cpu_seconds"] for r in rows),
                   "quality": sum(r["quality_cpu_seconds"] for r in rows),
                   "static": sum(r["static_cpu_seconds"] for r in rows)
               },
               "wall_time_seconds_baseline": sum(r["engine_seconds"]+r["quality_seconds"] for r in rows),
               "wall_time_seconds_hybrid": sum(r["engine_seconds"]+r["quality_seconds"]+r["static_seconds"] for r in rows),
               "timing_note": "Wall times from perf_counter; CPU times from process_time. Rounded per case to 6 decimals.",
               "warning": "No universal precision/recall: without an independent ground truth INCONCLUSIVE is not false."}
    with (outdir / "arcade_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    try:
        from collect_pcg_environment_v2 import capture
        provenance = capture([Path(__file__)])
        (outdir / 'pcg_environment_manifest.json').write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    except (ImportError, OSError) as exc:
        print(f'WARNING: provenance manifest not written: {exc}', file=sys.stderr)


if __name__ == "__main__":
    example_tests()
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=Path("arcade_results"))
    parser.add_argument("--max-objects", type=int, default=None,
                        help="Additional pre-registered stratum: each color <= k objects; report separately")
    args = parser.parse_args()
    if args.n < 1:
        parser.error("--n must be >= 1")
    run(args.n, args.seed, args.output, args.max_objects)
