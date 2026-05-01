"""
Marathon Market Simulator — Analysis Charts (TO BE REWRITTEN).

The previous charts modeled the old skill/yield/congestion formulas
which no longer exist after the runner_sim/zone_sim integration. New
charts to add for the new mechanic:

  - Distribution of squad credits extracted per zone (histogram)
  - Squad elimination rate by zone
  - Doctrine performance — avg credits by GREEDY/CAUTIOUS/BALANCED/SUPPORT
  - Shell adoption over time (from market.adoption_history)
  - Shell prices over time

Run: uv run python charts.py
"""

import sys

from runner_sim.zone_sim.zones import ZONES


def main() -> None:
    print("charts.py: pending rewrite for the new tick-based mechanic.")
    print("  Old skill/yield/congestion formulas no longer apply.")
    print()
    print("Loaded zones (verifying imports work):")
    for z in ZONES:
        tag = " ★ monitored" if z.monitored else ""
        print(f"  - {z.name}  difficulty={z.difficulty}  pool_size={z.pool_size}{tag}")


if __name__ == "__main__":
    main()
    sys.exit(0)
