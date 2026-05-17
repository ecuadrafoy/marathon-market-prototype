"""
Headless calibration for the integrated market layer.

Runs simulate_week for many weeks WITH the company-AI loop enabled (so it
exercises the real variable-roster steady state — orphans, signings, bidding)
but WITHOUT the valuation anchor (so it measures the pure performance signal).
Returns mean + stddev of per-company-week credit totals. These feed
BASE_EXPECTATION and EXPECTED_DELTA_RANGE in pricing.py.

The split is deliberate: the performance term is calibrated under the real
roster dynamics so the constants match what the live game actually sees,
but the anchor is layered on additively in `_build_company_result` — leaving
the calibration meaning intact regardless of how the anchor is tuned.

Also exposes bootstrap_default_state() — a single entry point that
constructs (rosters, shell market, free agents, id counter) ready to feed
simulate_week, used by both the live game and calibration.
"""

from __future__ import annotations
import random
import statistics
from dataclasses import dataclass, field

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
    CompanyMemory,
    INITIAL_FREE_AGENT_BENCH,
    Loan,
    PostureState,
    RunnerIdCounter,
)
from runner_sim.runners import Runner


# Starting prices mirror marathon_market.GameEngine.__init__ — kept local
# so calibration doesn't import marathon_market. Order matches DEFAULT_COMPANY_NAMES.
DEFAULT_STARTING_PRICES: tuple[float, ...] = (450.0, 380.0, 300.0, 200.0)
DEFAULT_STARTING_BUDGET: float = 600.0   # mirror of marathon_market.STARTING_COMPANY_BUDGET


@dataclass
class _CalibCompany:
    """Minimal Company stand-in for headless calibration.

    The AI cycle in simulate_week (settle_payroll / decide_acquisitions /
    resolve_bidding / take_loan_if_needed) reads .name / .price / .budget /
    .posture / .loans and mutates .budget + .posture + .loans; valuation
    fields are present with zero defaults but never touched in calibration
    mode (no anchor, no quarterly tick fires).
    """
    name: str
    price: float
    budget: float = DEFAULT_STARTING_BUDGET
    valuation: float = 0.0
    pending_valuation_delta: float = 0.0
    posture: PostureState = field(default_factory=PostureState)
    memory: CompanyMemory = field(default_factory=CompanyMemory)
    loans: list[Loan] = field(default_factory=list)


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
    per-company-week total credits extracted under live AI dynamics.

    Caller paste into pricing.py:
      BASE_EXPECTATION    = mean / 3
      EXPECTED_DELTA_RANGE = stdev

    Runs with the company-AI loop ACTIVE (budgets, payroll, orphaning, bidding,
    variable 6–9 rosters) — so the distribution we measure is the real
    steady-state the live game sees. Runs WITHOUT anchor_inputs, so the
    `total_credits_extracted` collected here is the pure performance signal,
    untouched by the valuation mean-reversion term. The two layers stay
    cleanly separable.
    """
    random.seed(seed)
    rng = random.Random(seed)

    # Local import to avoid loading week.py (and zone_sim) just to import this module.
    from runner_sim.market.week import simulate_week
    from runner_sim.zone_sim.zones import ZONES
    from runner_sim.zone_sim.items import load_items

    rosters, market, free_agents, id_supplier = bootstrap_default_state()
    item_catalog = load_items()

    # Stand-in companies — minimal stubs the AI cycle can mutate.
    companies = [
        _CalibCompany(name=name, price=price)
        for name, price in zip(DEFAULT_COMPANY_NAMES, DEFAULT_STARTING_PRICES)
    ]
    price_histories: dict[str, list[float]] = {c.name: [c.price] for c in companies}

    per_company_credits: list[float] = []
    for _ in range(weeks):
        # AI loop ON, anchor OFF — measures the pure performance distribution
        # under live roster dynamics.
        result = simulate_week(
            rosters, market, ZONES, item_catalog,
            company_prices={c.name: c.price for c in companies},
            companies=companies,
            free_agents=free_agents,
            id_supplier=id_supplier,
            price_histories=price_histories,
            rng=rng,
            # anchor_inputs intentionally omitted → anchor term = 0.0
        )
        # Mirror advance_week: write price_after back, update histories.
        for r in result.company_results:
            for c in companies:
                if c.name == r.company_name:
                    c.price = r.price_after
                    price_histories[c.name].append(r.price_after)
        # Calibrate on ACTIVE company-weeks only — i.e. conditional on having
        # actually deployed. A company that sat out (roster<6 → no squads)
        # contributes total_credits=0, but the live game still measures it
        # against the full baseline (3 × BASE_EXPECTATION), so including those
        # zeros here would double-count the under-deployment penalty and
        # collapse the mean to near-zero. The performance term is meant to
        # answer "given that you deployed, what's expected?"
        per_company_credits.extend(
            r.total_credits_extracted for r in result.company_results
            if r.squads_deployed > 0
        )

    # Use MEDIAN, not mean, as the "typical week" reference. Loot is heavily
    # right-skewed (rare Epics at 1000cr pull the mean up); a mean-based
    # baseline makes every median-week look like underperformance and produces
    # systematic price drift down. The median is what a player intuitively
    # reads as "expected" and keeps the typical week's price move near zero.
    median = statistics.median(per_company_credits)
    stdev = statistics.stdev(per_company_credits)
    return median, stdev
