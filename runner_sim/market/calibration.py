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
    RECRUIT_ALLOWANCE,
    _hire_one,
    create_roster,
    collect_used_names,
    all_runners,
)
from runner_sim.market.shell_market import ShellMarket, make_initial_market, update_prices
from runner_sim.market.company_strategy import (
    INITIAL_FREE_AGENT_BENCH,
    RunnerIdCounter,
)
from runner_sim.runners import Runner


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
    seed_free_agents: int = INITIAL_FREE_AGENT_BENCH,
) -> tuple[dict[str, CompanyRoster], ShellMarket, list[Runner], RunnerIdCounter]:
    """Build initial rosters + shell market + free-agent bench in lockstep.

    All runners (rosters + free agents) draw ids from a single shared counter
    so cross-roster migration never causes id collisions.

    Each company hires STARTING_ROSTER_SIZE recruits from a uniform-price
    shelf (BASE_SHELL_PRICE for every shell). After roster creation, an
    additional `seed_free_agents` recruits are spawned into the free-agent
    pool — they hold no shell yet (assigned when a company signs them).

    Returns: (rosters_by_company_name, shell_market, free_agents, id_supplier)
    """
    market = make_initial_market()
    rosters: dict[str, CompanyRoster] = {}
    used_names: set[str] = set()
    id_supplier = RunnerIdCounter()

    for company_name in company_names:
        rosters[company_name] = create_roster(
            company_name, market, used_names, id_supplier=id_supplier
        )

    free_agents: list[Runner] = []
    for _ in range(seed_free_agents):
        rookie = _hire_one(
            company_name="",
            runner_id=id_supplier(),
            market=market,
            used_names=used_names,
        )
        rookie.current_shell = ""             # no shell until a company signs them
        rookie.credit_balance = RECRUIT_ALLOWANCE
        free_agents.append(rookie)

    # Capture the initial week-0 adoption so prices have signal before week 1.
    update_prices(market, all_runners(rosters))
    return rosters, market, free_agents, id_supplier


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

    rosters, market, _free_agents, _id_supplier = bootstrap_default_state()
    item_catalog = load_items()

    per_company_credits: list[float] = []
    for _ in range(weeks):
        # Calibration mode runs simulate_week WITHOUT the company-AI loop
        # (no companies/free_agents/id_supplier), so deaths vanish into the
        # void and rosters are not refilled — same behaviour as before, used
        # only for re-deriving pricing constants.
        result = simulate_week(rosters, market, ZONES, item_catalog)
        per_company_credits.extend(r.total_credits_extracted for r in result.company_results)

    mean = statistics.mean(per_company_credits)
    stdev = statistics.stdev(per_company_credits)
    return mean, stdev
