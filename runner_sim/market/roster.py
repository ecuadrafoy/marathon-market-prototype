"""
Company roster management.

Each company holds a fixed-size roster of persistent runners. Every week,
all 9 deploy. Runners whose squad was eliminated this week are replaced
between weeks via fresh recruitment.

Recruitment uses the shell market: each new recruit gets RECRUIT_ALLOWANCE
credits, picks the best affordable shell, and has the price deducted from
their personal balance.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field

from runner_sim.runners import Runner
from runner_sim.market.shell_market import ShellMarket, choose_affordable_shell


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
STARTING_ROSTER_SIZE = 9        # 3 squads × 3 runners; zero bench
RECRUIT_ALLOWANCE    = 250.0    # starting credit balance for a fresh recruit


# Flavor names for runner identities — purely cosmetic
RUNNER_NAME_POOL: tuple[str, ...] = (
    "Vega", "Orion", "Lyra", "Sable", "Crow", "Echo", "Ash", "Wren",
    "Pike", "Onyx", "Juno", "Cipher", "Nova", "Ridge", "Hex", "Kite",
    "Mara", "Tully", "Quinn", "Shrike", "Vesper", "Pax", "Cinder", "Halo",
    "Glass", "Kestrel", "Thorne", "Reno", "Slate", "Brand", "Lark", "Mire",
    "Drift", "Polar", "Soren", "Tessa", "Volk", "Wynn", "Yara", "Zephyr",
    "Briar", "Coil", "Daven", "Fjord", "Gale", "Husk", "Iris", "Jet",
)


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class CompanyRoster:
    company_name: str
    runners: list[Runner] = field(default_factory=list)
    next_runner_id: int = 0


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _random_simplex_triple() -> tuple[float, float, float]:
    """Uniform random point on the 2-simplex — (a, b, c) >= 0 with a+b+c=1."""
    a, b = sorted((random.random(), random.random()))
    return a, b - a, 1.0 - b


def _random_runner_name(used_names: set[str]) -> str:
    """Pick a flavor name not already taken across all rosters; fall back to numbered ID."""
    available = [n for n in RUNNER_NAME_POOL if n not in used_names]
    if available:
        return random.choice(available)
    return f"Runner-{len(used_names):03d}"


def _hire_one(
    company_name: str,
    runner_id: int,
    market: ShellMarket,
    used_names: set[str],
) -> Runner:
    """Build one fresh recruit and equip them with a budget-aware shell.

    The shell purchase price is deducted from RECRUIT_ALLOWANCE; whatever's
    left becomes the runner's starting credit_balance for future spending.
    """
    c, e, s = _random_simplex_triple()
    name = _random_runner_name(used_names)
    used_names.add(name)

    runner = Runner(
        id=runner_id,
        name=name,
        company_name=company_name,
        combat=c,
        extraction=e,
        support=s,
        current_shell="",          # set below after shell purchase
        credit_balance=RECRUIT_ALLOWANCE,
    )
    shell = choose_affordable_shell(runner, market.prices, runner.credit_balance)
    runner.current_shell = shell.name
    runner.credit_balance -= market.prices[shell.name]
    return runner


# ---------------------------------------------------------------------------
# ROSTER LIFECYCLE
# ---------------------------------------------------------------------------
def create_roster(
    company_name: str,
    market: ShellMarket,
    used_names: set[str],
) -> CompanyRoster:
    """Build a fresh roster of STARTING_ROSTER_SIZE recruits."""
    roster = CompanyRoster(company_name=company_name, next_runner_id=0)
    for _ in range(STARTING_ROSTER_SIZE):
        runner = _hire_one(company_name, roster.next_runner_id, market, used_names)
        roster.runners.append(runner)
        roster.next_runner_id += 1
    return roster


def replace_dead_runners(
    roster: CompanyRoster,
    market: ShellMarket,
    used_names: set[str],
) -> int:
    """Remove runners who died this week and hire replacements.

    A runner is considered "dead this week" if their `death_count` was just
    incremented (squad eliminated). We detect this by checking a sentinel
    flag set on the runner during apply_zone_outcome — see week.py.

    Returns the number of replacements hired.
    """
    survivors = [r for r in roster.runners if not getattr(r, "_died_this_week", False)]
    deaths = len(roster.runners) - len(survivors)
    roster.runners = survivors

    for _ in range(deaths):
        runner = _hire_one(roster.company_name, roster.next_runner_id, market, used_names)
        roster.runners.append(runner)
        roster.next_runner_id += 1

    return deaths


def collect_used_names(rosters: dict[str, CompanyRoster]) -> set[str]:
    """Return the set of all runner names currently across all rosters."""
    return {r.name for roster in rosters.values() for r in roster.runners}


def all_runners(rosters: dict[str, CompanyRoster]) -> list[Runner]:
    """Flat list of every runner across all rosters — convenience for shell_market.update_prices."""
    return [r for roster in rosters.values() for r in roster.runners]
