"""
Headless shell market price-movement verification.

Runs N weeks of the full simulation across M random seeds and reports
whether shell prices actually move. Use this to validate that the weekly
re-equip pass is working and that the market isn't frozen.

Usage:
    uv run python scripts/check_price_movement.py
    uv run python scripts/check_price_movement.py --seeds 5 --weeks 20
"""

from __future__ import annotations
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner_sim.market.calibration import bootstrap_default_state
from runner_sim.market.week import simulate_week
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES
from runner_sim.shells import SHELL_ROSTER


COMPANY_NAMES = ("CyberAcme", "Sekiguchi", "Traxus", "NuCaloric")
STARTING_PRICES = {"CyberAcme": 450.0, "Sekiguchi": 380.0, "Traxus": 300.0, "NuCaloric": 200.0}


def run_seed(seed: int, weeks: int) -> dict[str, list[float]]:
    """Return per-shell price history across `weeks` for a single seed."""
    random.seed(seed)
    rosters, market = bootstrap_default_state(company_names=COMPANY_NAMES)
    item_catalog = load_items()
    prices = dict(STARTING_PRICES)

    history: dict[str, list[float]] = {s.name: [] for s in SHELL_ROSTER}

    for _ in range(weeks):
        result = simulate_week(rosters, market, ZONES, item_catalog,
                               company_prices=prices)
        for r in result.company_results:
            prices[r.company_name] = r.price_after
        for shell in SHELL_ROSTER:
            history[shell.name].append(market.prices[shell.name])

    return history


def _fmt_delta(lo: float, hi: float) -> str:
    direction = "+" if hi >= lo else ""
    return f"{lo:.1f}→{hi:.1f} ({direction}{hi - lo:.1f})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Shell market price-movement check")
    parser.add_argument("--seeds", type=int, default=3, help="Number of random seeds to test")
    parser.add_argument("--weeks", type=int, default=20, help="Weeks to simulate per seed")
    args = parser.parse_args()

    all_pass = True
    shell_names = [s.name for s in SHELL_ROSTER]

    for seed in range(args.seeds):
        history = run_seed(seed, args.weeks)

        movers = []
        frozen = []
        for name in shell_names:
            prices = history[name]
            lo, hi = min(prices), max(prices)
            moved = (hi - lo) >= 0.5   # threshold: 0.5 cr counts as real movement
            (movers if moved else frozen).append((name, lo, hi))

        seed_pass = len(frozen) == 0 or len(movers) > 0
        status = "PASS" if seed_pass else "FROZEN"
        if not seed_pass:
            all_pass = False

        print(f"\nSeed {seed} ({args.weeks} weeks)  [{status}]")
        for name, lo, hi in sorted(movers + frozen, key=lambda t: -(t[2] - t[1])):
            tag = "  " if hi - lo >= 0.5 else "!!"
            print(f"  {tag} {name:<10}  {_fmt_delta(lo, hi)}")
        if frozen:
            print(f"  !! {len(frozen)} shell(s) never moved: "
                  f"{', '.join(n for n, *_ in frozen)}")

    print()
    if all_pass:
        print(f"OK — at least one shell moved in all {args.seeds} seed runs.")
        return 0
    else:
        print(f"FAIL — one or more seed runs had completely frozen shell prices.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
