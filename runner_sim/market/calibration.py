"""
Headless calibration for the integrated market layer.

Runs simulate_week for many weeks with no UI/no price update, collects
per-company-week credit totals, and returns mean + stddev. These feed
BASE_EXPECTATION and EXPECTED_DELTA_RANGE in pricing.py.

Also exposes bootstrap_default_state() — a single entry point that
constructs (rosters, shell market) ready to feed simulate_week, used
by both the live game and calibration.
"""

from __future__ import annotations
import random
import statistics

from runner_sim.market.roster import (
    CompanyRoster,
    create_roster,
    collect_used_names,
    all_runners,
)
from runner_sim.market.shell_market import ShellMarket, make_initial_market, update_prices


# ---------------------------------------------------------------------------
# DEFAULT COMPANY NAMES
# ---------------------------------------------------------------------------
# Mirrors the four companies used in marathon_market.run_game(); kept here
# so calibration can run without importing marathon_market.
DEFAULT_COMPANY_NAMES: tuple[str, ...] = (
    "CyberAcme", "Sekiguchi", "Traxus", "NuCaloric",
)


# ---------------------------------------------------------------------------
# BOOTSTRAP
# ---------------------------------------------------------------------------
def bootstrap_default_state(
    company_names: tuple[str, ...] = DEFAULT_COMPANY_NAMES,
) -> tuple[dict[str, CompanyRoster], ShellMarket]:
    """Build initial rosters + shell market in lockstep.

    Each company hires STARTING_ROSTER_SIZE recruits from a uniform-price
    shelf (BASE_SHELL_PRICE for every shell). Once all recruits are placed,
    update_prices runs once so the market reflects week-0 adoption.

    Returns: (rosters_by_company_name, shell_market)
    """
    market = make_initial_market()
    rosters: dict[str, CompanyRoster] = {}
    used_names: set[str] = set()

    for company_name in company_names:
        rosters[company_name] = create_roster(company_name, market, used_names)

    # Capture the initial week-0 adoption so prices have signal before week 1.
    update_prices(market, all_runners(rosters))
    return rosters, market


# ---------------------------------------------------------------------------
# HEADLESS CALIBRATION
# ---------------------------------------------------------------------------
def headless_calibration(weeks: int = 1000, seed: int = 42) -> tuple[float, float]:
    """Run the full integrated stack for many weeks; return (mean, stdev) of
    per-company-week total credits extracted.

    Caller paste:
      BASE_EXPECTATION = mean / 3       (per-squad expectation)
      EXPECTED_DELTA_RANGE = stdev      (typical fluctuation magnitude)

    Steady-state with drift: rosters persist for the full run; runners
    that survive specialize naturally. Recruits replace the dead and
    enter at current shell-market prices.

    Note: simulate_week is implemented in step 4. Until then this raises
    ImportError when called — leaving the surface in place so step 6
    can plug in cleanly.
    """
    random.seed(seed)

    # Local import to avoid loading week.py (and zone_sim) just to import this module.
    from runner_sim.market.week import simulate_week
    from runner_sim.zone_sim.zones import ZONES
    from runner_sim.zone_sim.items import load_items

    rosters, market = bootstrap_default_state()
    item_catalog = load_items()

    per_company_credits: list[float] = []
    for _ in range(weeks):
        result = simulate_week(rosters, market, ZONES, item_catalog)
        per_company_credits.extend(r.total_credits_extracted for r in result.company_results)

    mean = statistics.mean(per_company_credits)
    stdev = statistics.stdev(per_company_credits)
    return mean, stdev
