"""
Standalone CLI harness for the tick-based zone simulation.

Run:
    uv run python -m runner_sim.zone_sim.harness --seed 42

What it does each invocation:
1. Load items from data/items.csv
2. Create a runner pool and form squads of 3
3. Randomly distribute squads across the three zones
4. For each zone, run the tick simulation and print the match log
5. Print a final per-squad summary with doctrine, items found, and credits earned

This is intentionally separate from the main runner_sim harness — no interaction
with resolve_week() or marathon_market.py. The goal is fast iteration on the
zone mechanics in isolation.
"""

from __future__ import annotations
import argparse
import random
import sys

from ..encounters import form_squads
from ..harness import create_runner_pool
from .items import load_items
from .sim import SQUAD_NAMES, Squad, make_squad, run_zone
from .zones import ZONES


# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_POOL_SIZE = 27   # 9 squads of 3 → 3 squads per zone


def _distribute_squads_to_zones(squads: list[Squad], zone_count: int) -> list[list[Squad]]:
    """Random distribution of squads across N zones. Remainders favour earlier zones."""
    shuffled = list(squads)
    random.shuffle(shuffled)
    bins: list[list[Squad]] = [[] for _ in range(zone_count)]
    for idx, squad in enumerate(shuffled):
        bins[idx % zone_count].append(squad)
    return bins


def _print_match_log(log: list[str]) -> None:
    for line in log:
        print(line)
    print()


def _print_final_summary(all_squads: list[Squad], zone_assignments: dict[str, str]) -> None:
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    header = f"{'Squad':<10} {'Zone':<12} {'Doctrine':<10} {'Status':<12} {'Items':<6} {'Credits':>8}"
    print(header)
    print("-" * len(header))

    # Sort by credits descending so the leaderboard is readable
    sorted_squads = sorted(all_squads, key=lambda s: -s.loot.total_credits())
    for squad in sorted_squads:
        if squad.eliminated:
            status = "ELIMINATED"
        elif squad.extracted:
            status = "extracted"
        else:
            status = "stranded"
        print(
            f"{squad.name:<10} {zone_assignments.get(squad.name, '?'):<12} "
            f"{squad.doctrine.value.upper():<10} {status:<12} "
            f"{len(squad.loot.items):<6} {squad.loot.total_credits():>8}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tick-based zone simulation harness")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--pool-size", type=int, default=DEFAULT_POOL_SIZE,
        help=f"Number of runners to create (default {DEFAULT_POOL_SIZE})",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    # 1. Load item catalog
    items = load_items()
    print(f"Loaded {len(items)} items from data/items.csv\n")

    # 2. Build runner pool and form squads of 3
    runners = create_runner_pool(args.pool_size)
    raw_squads, sit_outs = form_squads(runners)
    if sit_outs:
        print(f"(Note: {len(sit_outs)} runner(s) sat out — pool not divisible by squad size.)\n")

    # 3. Wrap raw squads in zone_sim Squad objects with names + doctrines
    squads = [
        make_squad(SQUAD_NAMES[i % len(SQUAD_NAMES)], runners=members)
        for i, members in enumerate(raw_squads)
    ]

    # 4. Distribute across zones
    zone_bins = _distribute_squads_to_zones(squads, len(ZONES))
    zone_assignments: dict[str, str] = {}
    for zone, zone_squads in zip(ZONES, zone_bins):
        for squad in zone_squads:
            zone_assignments[squad.name] = zone.name

    # 5. Run each zone, print log
    all_squads: list[Squad] = []
    for zone, zone_squads in zip(ZONES, zone_bins):
        if not zone_squads:
            print(f"=== {zone.name} === (no squads assigned, skipping)\n")
            continue
        result = run_zone(zone, zone_squads, items)
        _print_match_log(result.match_log)
        all_squads.extend(result.squads)

    # 6. Final summary
    _print_final_summary(all_squads, zone_assignments)


if __name__ == "__main__":
    main()
