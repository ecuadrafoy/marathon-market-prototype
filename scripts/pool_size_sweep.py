"""
Pool size sweep — compare loot outcomes across different per-zone pool size configs.

Each named configuration specifies (Perimeter, Dire Marsh, Outpost) pool sizes.
For each config, runs N weeks × M seeds and reports drawn/extracted/depletion
and per-tier extracted averages. Use this to find a pool config that gives the
desired risk/reward gradient across zones.

Usage:
    uv run python scripts/pool_size_sweep.py
    uv run python scripts/pool_size_sweep.py --weeks 30 --seeds 5
"""

from __future__ import annotations
import argparse
import random
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner_sim.market.calibration import bootstrap_default_state
from runner_sim.market.week import simulate_week
from runner_sim.zone_sim.extraction_ai import Tier
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES


COMPANY_NAMES = ("CyberAcme", "Sekiguchi", "Traxus", "NuCaloric")

# ── Configurations to compare ────────────────────────────────────────────────
# Each entry: (label, {zone_name: pool_size})
CONFIGURATIONS: list[tuple[str, dict[str, int]]] = [
    ("Current  (12 / 8 / 5)",  {"Perimeter": 12, "Dire Marsh": 8,  "Outpost": 5 }),
    ("Equal-8  ( 8 / 8 / 8)",  {"Perimeter": 8,  "Dire Marsh": 8,  "Outpost": 8 }),
    ("Equal-10 (10 /10 /10)",  {"Perimeter": 10, "Dire Marsh": 10, "Outpost": 10}),
    ("Equal-12 (12 /12 /12)",  {"Perimeter": 12, "Dire Marsh": 12, "Outpost": 12}),
    ("Mild     (12 /10 / 8)",  {"Perimeter": 12, "Dire Marsh": 10, "Outpost": 8 }),
    ("Inverted ( 5 / 8 /12)",  {"Perimeter": 5,  "Dire Marsh": 8,  "Outpost": 12}),
]


def _run_config(
    pool_sizes: dict[str, int],
    weeks: int,
    seeds: int,
    item_catalog,
) -> dict:
    """Average loot stats for one pool-size configuration across multiple seeds."""
    zone_names = [z.name for z in ZONES]

    drawn_acc     = defaultdict(list)
    extracted_acc = defaultdict(list)
    depletion_acc = defaultdict(list)
    tier_acc: dict[str, dict[Tier, list]] = {z: {t: [] for t in Tier} for z in zone_names}

    for seed in range(seeds):
        random.seed(seed)
        rosters, market = bootstrap_default_state(company_names=COMPANY_NAMES)

        drawn_s     = defaultdict(int)
        extracted_s = defaultdict(int)
        depleted_s  = defaultdict(int)
        tier_s: dict[str, dict[Tier, int]] = {z: defaultdict(int) for z in zone_names}

        # Build patched zone list for this config.
        patched_zones = [replace(z, pool_size=pool_sizes[z.name]) for z in ZONES]

        for _ in range(weeks):
            result = simulate_week(rosters, market, patched_zones, item_catalog)
            for zone_name, zr in result.zone_results.items():
                found = zr.pool_size_at_start - zr.pool_size_at_end
                drawn_s[zone_name]    += found
                depleted_s[zone_name] += zr.pool_size_at_start
                ext = 0
                for squad in zr.squads:
                    if squad.extracted:
                        for item in squad.loot.items:
                            tier_s[zone_name][item.tier] += 1
                            ext += 1
                extracted_s[zone_name] += ext

        for zone_name in zone_names:
            drawn_acc[zone_name].append(drawn_s[zone_name] / weeks)
            extracted_acc[zone_name].append(extracted_s[zone_name] / weeks)
            cap = depleted_s[zone_name]
            depletion_acc[zone_name].append(drawn_s[zone_name] / cap * 100 if cap else 0)
            for tier in Tier:
                tier_acc[zone_name][tier].append(tier_s[zone_name][tier] / weeks)

    def avg(lst): return sum(lst) / len(lst)

    return {
        "drawn":     {z: avg(drawn_acc[z])     for z in zone_names},
        "extracted": {z: avg(extracted_acc[z]) for z in zone_names},
        "depletion": {z: avg(depletion_acc[z]) for z in zone_names},
        "tiers":     {z: {t: avg(tier_acc[z][t]) for t in Tier} for z in zone_names},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pool size configuration sweep")
    parser.add_argument("--weeks", type=int, default=20)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()

    item_catalog = load_items()
    zone_names   = [z.name for z in ZONES]

    print(f"\nPool size sweep — {args.weeks} weeks × {args.seeds} seeds")

    for label, pool_sizes in CONFIGURATIONS:
        print(f"\n{'─'*62}")
        print(f"  {label}")
        print(f"{'─'*62}")
        stats = _run_config(pool_sizes, args.weeks, args.seeds, item_catalog)

        print(f"  {'Zone':<14} {'Pool':>5}  {'Drawn/wk':>9}  {'Extrd/wk':>9}  {'Deplt':>6}")
        print(f"  {'-'*14} {'-'*5}  {'-'*9}  {'-'*9}  {'-'*6}")
        for zone in zone_names:
            p = pool_sizes[zone]
            d = stats["drawn"][zone]
            e = stats["extracted"][zone]
            dep = stats["depletion"][zone]
            print(f"  {zone:<14} {p:>5}  {d:>9.2f}  {e:>9.2f}  {dep:>5.1f}%")

        print(f"\n  Extracted by tier (avg/wk)  Cmn   Unc   Rare  Epic")
        print(f"  {'-'*40}")
        for zone in zone_names:
            t = stats["tiers"][zone]
            print(f"  {zone:<34}"
                  f"  {t[Tier.COMMON]:>4.2f}"
                  f"  {t[Tier.UNCOMMON]:>4.2f}"
                  f"  {t[Tier.RARE]:>4.2f}"
                  f"  {t[Tier.EPIC]:>4.2f}")

    print(f"\n{'─'*62}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
