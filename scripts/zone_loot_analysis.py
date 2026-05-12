"""
Zone loot analysis — items found and extracted per zone over N weeks.

Tracks two distinct counts per zone per week:
  - drawn:     items removed from the pool (found by any squad)
  - extracted: items that made it out with a surviving squad
  The gap is loot lost to elimination (Common abandoned or Uncommon+ kill-looted
  by a squad that was itself eliminated later).

Also reports tier breakdown of extracted items and pool depletion rate.

Usage:
    uv run python scripts/zone_loot_analysis.py
    uv run python scripts/zone_loot_analysis.py --weeks 50 --seeds 3
"""

from __future__ import annotations
import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner_sim.market.calibration import bootstrap_default_state
from runner_sim.market.week import simulate_week
from runner_sim.zone_sim.extraction_ai import Tier
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES


COMPANY_NAMES = ("CyberAcme", "Sekiguchi", "Traxus", "NuCaloric")
TIER_LABELS   = {Tier.COMMON: "Common", Tier.UNCOMMON: "Uncommon",
                 Tier.RARE: "Rare", Tier.EPIC: "Epic"}


def run_seed(seed: int, weeks: int) -> dict:
    """Run `weeks` weeks and return per-zone loot accumulation stats."""
    random.seed(seed)
    rosters, market = bootstrap_default_state(company_names=COMPANY_NAMES)
    item_catalog = load_items()

    # Accumulators keyed by zone name.
    drawn     = defaultdict(int)   # items pulled from pool
    extracted = defaultdict(int)   # items that made it out
    lost      = defaultdict(int)   # drawn but not extracted
    depleted  = defaultdict(int)   # total pool capacity consumed across weeks
    tier_counts: dict[str, dict[Tier, int]] = {z.name: defaultdict(int) for z in ZONES}

    for _ in range(weeks):
        result = simulate_week(rosters, market, ZONES, item_catalog)

        for zone_name, zr in result.zone_results.items():
            found_this_week = zr.pool_size_at_start - zr.pool_size_at_end
            drawn[zone_name]    += found_this_week
            depleted[zone_name] += zr.pool_size_at_start

            # Count extracted items (from squads that survived).
            ext_this_week = 0
            for squad in zr.squads:
                if squad.extracted:
                    for item in squad.loot.items:
                        tier_counts[zone_name][item.tier] += 1
                        ext_this_week += 1
            extracted[zone_name] += ext_this_week
            lost[zone_name]      += found_this_week - ext_this_week

    return {
        "drawn": dict(drawn),
        "extracted": dict(extracted),
        "lost": dict(lost),
        "depleted": dict(depleted),
        "tier_counts": tier_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Zone loot distribution analysis")
    parser.add_argument("--weeks", type=int, default=20, help="Weeks per seed (default: 20)")
    parser.add_argument("--seeds", type=int, default=3, help="Seeds to average over (default: 3)")
    args = parser.parse_args()

    zone_names = [z.name for z in ZONES]

    # Collect results across all seeds.
    all_drawn:     dict[str, list[float]] = defaultdict(list)
    all_extracted: dict[str, list[float]] = defaultdict(list)
    all_lost:      dict[str, list[float]] = defaultdict(list)
    all_depletion: dict[str, list[float]] = defaultdict(list)
    all_tiers:     dict[str, dict[Tier, list[float]]] = {
        z: {t: [] for t in Tier} for z in zone_names
    }

    for seed in range(args.seeds):
        stats = run_seed(seed, args.weeks)
        w = args.weeks
        for zone in zone_names:
            all_drawn[zone].append(stats["drawn"][zone] / w)
            all_extracted[zone].append(stats["extracted"][zone] / w)
            all_lost[zone].append(stats["lost"][zone] / w)
            pool_cap = stats["depleted"][zone]   # total possible across all weeks
            all_depletion[zone].append(stats["drawn"][zone] / pool_cap * 100)
            for tier in Tier:
                all_tiers[zone][tier].append(stats["tier_counts"][zone][tier] / w)

    def avg(lst: list[float]) -> float:
        return sum(lst) / len(lst)

    print(f"\nZone loot analysis — {args.weeks} weeks × {args.seeds} seeds\n")
    print(f"  {'Zone':<14} {'Pool':>5}  {'Drawn/wk':>9}  {'Extrd/wk':>9}  {'Lost/wk':>8}  {'Depletion':>9}")
    print(f"  {'-'*14} {'-'*5}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*9}")

    zone_obj = {z.name: z for z in ZONES}
    for zone in zone_names:
        pool = zone_obj[zone].pool_size
        d    = avg(all_drawn[zone])
        e    = avg(all_extracted[zone])
        lo   = avg(all_lost[zone])
        dep  = avg(all_depletion[zone])
        print(f"  {zone:<14} {pool:>5}  {d:>9.2f}  {e:>9.2f}  {lo:>8.2f}  {dep:>8.1f}%")

    print(f"\n  Extracted items by tier (avg per week)\n")
    print(f"  {'Zone':<14} {'Common':>8} {'Uncommon':>10} {'Rare':>8} {'Epic':>8}")
    print(f"  {'-'*14} {'-'*8} {'-'*10} {'-'*8} {'-'*8}")
    for zone in zone_names:
        row = [avg(all_tiers[zone][t]) for t in
               [Tier.COMMON, Tier.UNCOMMON, Tier.RARE, Tier.EPIC]]
        print(f"  {zone:<14} {row[0]:>8.2f} {row[1]:>10.2f} {row[2]:>8.2f} {row[3]:>8.2f}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
