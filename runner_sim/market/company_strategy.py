"""
Company AI — weekly roster decisions driven by budget, upkeep, and a
closed free-agent pool. This is the v1 of "Adaptive Company AI" described
in docs/future_design.md, deliberately narrow in scope.

The weekly cycle (called from week.simulate_week before assign_squads):
  0. tick_free_agent_pool        — orphans tick weeks_orphaned, retire after N
  1. compute_upkeep for each employed runner (cached on the runner)
  2. settle_payroll              — pay cheapest first; unpaid runners orphan
  3. decide_voluntary_drops      — struggling companies dump expensive misfits
  4. resolve_bidding             — sequential random-order draft refills to ≤9

After the zone sims and pricing, week.py calls:
  5. collect_company_income      — 30% of extracted credits → company.budget
  6. release_to_free_agents      — dead runners return to the pool (consciousness
                                   persists; bio-synthetic body destroyed)

Upkeep is pure earned-value: base + (career net_loot) + (eliminations) +
(deployments survived). No shell-tier multiplier — a Destroyer rookie costs
the same as a Recon rookie until they prove themselves in the field.
"""

from __future__ import annotations
import random
import statistics
from dataclasses import dataclass, field
from typing import Literal

from runner_sim.runners import Runner
from runner_sim.market.roster import (
    CompanyRoster,
    RECRUIT_ALLOWANCE,
    _hire_one,
    collect_used_names,
)
from runner_sim.market.shell_market import ShellMarket


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
# Upkeep formula coefficients — three orthogonal axes of earned value.
BASE_UPKEEP                    = 15.0
UPKEEP_PER_NET_LOOT            = 0.015   # earned extraction value
UPKEEP_PER_ELIM                = 1.5     # earned combat value
UPKEEP_PER_DEPLOYMENT_SURVIVED = 2.5     # earned longevity / institutional knowledge

# Company income — fraction of last week's extracted credits routed to budget.
CREDIT_SHARE_TO_COMPANY        = 0.30

# Free-agent pool dynamics.
ORPHAN_RETIRE_AFTER_WEEKS      = 8       # idle free agents leave the world after N weeks
MIN_GLOBAL_POOL                = 42      # 4·9 employed + 6 bench floor; rookie spawns below
INITIAL_FREE_AGENT_BENCH       = 8       # week-0 seed so week-1 bidding has contention

# Health signal — drives struggling/thriving heuristics.
STRUGGLE_MA_WINDOW             = 3
STRUGGLE_THRESHOLD             = 0.97    # below MA × this → struggling
THRIVE_THRESHOLD               = 1.03    # above MA × this → thriving

Health = Literal["thriving", "neutral", "struggling"]


# ---------------------------------------------------------------------------
# RUNNER ID COUNTER — shared across rosters + free agents to guarantee
# unique ids even as runners migrate between companies.
# ---------------------------------------------------------------------------
@dataclass
class RunnerIdCounter:
    next_id: int = 0

    def __call__(self) -> int:
        v = self.next_id
        self.next_id += 1
        return v


# ---------------------------------------------------------------------------
# WEEKLY EVENT LOG
# ---------------------------------------------------------------------------
@dataclass
class CompanyRosterEvents:
    """Per-company roster changes recorded during one week's cycle.

    The simulator splits these by *cause* so the UI can communicate the
    timing — deaths happen *during* deployment, the other three happen
    *after* (when the AI reacts to the week's outcomes).
    """
    signed: list[str] = field(default_factory=list)                 # hired via bidding draft
    voluntarily_dropped: list[str] = field(default_factory=list)    # cut for being too expensive
    orphaned_unaffordable: list[str] = field(default_factory=list)  # couldn't make payroll
    died: list[str] = field(default_factory=list)                   # killed in zone deployment

    @property
    def total_gained(self) -> int:
        return len(self.signed)

    @property
    def total_lost(self) -> int:
        return (len(self.voluntarily_dropped)
                + len(self.orphaned_unaffordable)
                + len(self.died))


# ---------------------------------------------------------------------------
# UPKEEP
# ---------------------------------------------------------------------------
def compute_upkeep(runner: Runner) -> float:
    """Earned-value upkeep — three orthogonal axes, no shell-tier bias.

    A rookie costs BASE_UPKEEP regardless of shell. Veterans accrue cost
    along three independent axes, so a long-serving Triage support can be
    valuable without ever needing combat stats.
    """
    return (
        BASE_UPKEEP
        + UPKEEP_PER_NET_LOOT * runner.net_loot
        + UPKEEP_PER_ELIM * runner.eliminations
        + UPKEEP_PER_DEPLOYMENT_SURVIVED * runner.deployments_survived
    )


def refresh_upkeep(runner: Runner) -> None:
    """Recompute and cache the runner's upkeep_cost field."""
    runner.upkeep_cost = compute_upkeep(runner)


# ---------------------------------------------------------------------------
# INCOME
# ---------------------------------------------------------------------------
def collect_company_income(
    company,  # marathon_market.Company; loose type to avoid import cycle
    roster: CompanyRoster,
    credits_by_runner_id: dict[int, float],
) -> float:
    """Credit the company's budget with CREDIT_SHARE_TO_COMPANY of each
    employed runner's extracted credits this week. Returns the amount added.
    """
    earned = sum(credits_by_runner_id.get(r.id, 0.0) for r in roster.runners)
    share = earned * CREDIT_SHARE_TO_COMPANY
    company.budget += share
    return share


# ---------------------------------------------------------------------------
# PAYROLL
# ---------------------------------------------------------------------------
def settle_payroll(
    company,
    roster: CompanyRoster,
) -> tuple[list[Runner], list[Runner]]:
    """Pay each runner's upkeep, cheapest first. Mutates company.budget and
    roster.runners. Returns (kept, orphaned).

    Cheapest-first ordering is deliberate: one elite veteran cannot bankrupt
    the entire roster — a struggling company keeps a larger, cheaper core
    rather than going broke on a single star.
    """
    for r in roster.runners:
        refresh_upkeep(r)

    ordered = sorted(roster.runners, key=lambda r: r.upkeep_cost)
    kept: list[Runner] = []
    orphaned: list[Runner] = []

    for r in ordered:
        if company.budget >= r.upkeep_cost:
            company.budget -= r.upkeep_cost
            kept.append(r)
        else:
            orphaned.append(r)

    roster.runners = kept
    return kept, orphaned


# ---------------------------------------------------------------------------
# HEALTH SIGNAL
# ---------------------------------------------------------------------------
def company_health(price_history: list[float]) -> Health:
    """Classify the company based on current price vs. a 3-week moving average.

    Fewer than STRUGGLE_MA_WINDOW prior prices → neutral (no signal yet).
    """
    if len(price_history) < STRUGGLE_MA_WINDOW + 1:
        return "neutral"
    current = price_history[-1]
    ma = statistics.mean(price_history[-(STRUGGLE_MA_WINDOW + 1):-1])
    if ma <= 0:
        return "neutral"
    ratio = current / ma
    if ratio >= THRIVE_THRESHOLD:
        return "thriving"
    if ratio <= STRUGGLE_THRESHOLD:
        return "struggling"
    return "neutral"


# ---------------------------------------------------------------------------
# VOLUNTARY DROPS
# ---------------------------------------------------------------------------
def decide_voluntary_drops(
    company,
    roster: CompanyRoster,
    health: Health,
) -> list[Runner]:
    """Struggling companies dump runners whose upkeep is > 2× roster median.

    Thriving and neutral companies don't drop anyone voluntarily — they prefer
    to keep talent and let the market take its course. Returns the runners to
    orphan (also removed from roster.runners in-place).
    """
    if health != "struggling" or not roster.runners:
        return []

    upkeeps = [r.upkeep_cost for r in roster.runners]
    median = statistics.median(upkeeps)
    threshold = 2.0 * median
    drops = [r for r in roster.runners if r.upkeep_cost > threshold]
    roster.runners = [r for r in roster.runners if r not in drops]
    return drops


# ---------------------------------------------------------------------------
# ACQUISITION DECISIONS
# ---------------------------------------------------------------------------
def _roster_median_upkeep(roster: CompanyRoster) -> float:
    """Median upkeep of currently-employed runners; 0 if roster empty."""
    if not roster.runners:
        return 0.0
    return statistics.median(r.upkeep_cost for r in roster.runners)


def _max_affinity(runner: Runner) -> float:
    if not runner.shell_affinities:
        return 0.0
    return max(runner.shell_affinities.values())


def _safe_shell_score(runner: Runner) -> float:
    """Sum of Recon + Triage affinity — a struggling company's preferred profile."""
    return (
        runner.shell_affinities.get("Recon", 0.0)
        + runner.shell_affinities.get("Triage", 0.0)
    )


def _aggressive_shell_score(runner: Runner) -> float:
    """Sum of Destroyer + Assassin affinity — a thriving company's preferred profile."""
    return (
        runner.shell_affinities.get("Destroyer", 0.0)
        + runner.shell_affinities.get("Assassin", 0.0)
    )


def decide_acquisitions(
    company,
    roster: CompanyRoster,
    free_agents: list[Runner],
    health: Health,
    max_slots: int,
) -> list[tuple[Runner, float]]:
    """Return a ranked list of (runner, bid_amount) up to max_slots entries.

    The list is the company's preference order; the bidding resolver walks
    it from top to bottom, picking the first runner not yet claimed by a
    higher-priority bidder this round.
    """
    if max_slots <= 0 or not free_agents:
        return []

    # Ensure every candidate has a current upkeep cached for the bid math.
    for r in free_agents:
        refresh_upkeep(r)

    if health == "struggling":
        cap = max(1.0, 1.1 * _roster_median_upkeep(roster) or 1.1 * BASE_UPKEEP)
        eligible = [r for r in free_agents if r.upkeep_cost <= cap]
        eligible.sort(key=lambda r: (-_safe_shell_score(r), r.upkeep_cost))
        return [(r, r.upkeep_cost) for r in eligible[:max_slots]]

    if health == "thriving":
        eligible = [r for r in free_agents if _max_affinity(r) > 0.4] or list(free_agents)
        eligible.sort(key=lambda r: (-_aggressive_shell_score(r), -_max_affinity(r), r.upkeep_cost))
        return [(r, 1.5 * r.upkeep_cost) for r in eligible[:max_slots]]

    # neutral — cheapest available at min legal bid
    eligible = sorted(free_agents, key=lambda r: r.upkeep_cost)
    return [(r, r.upkeep_cost) for r in eligible[:max_slots]]


# ---------------------------------------------------------------------------
# BIDDING RESOLUTION
# ---------------------------------------------------------------------------
def resolve_bidding(
    companies,                                       # list[Company]
    rosters: dict[str, CompanyRoster],
    free_agents: list[Runner],
    bids_by_company: dict[str, list[tuple[Runner, float]]],
    rng: random.Random,
    target_roster_size: int,
) -> dict[str, list[Runner]]:
    """Sequential random-order draft.

    Each round we shuffle the companies; in order, each company tries to
    claim its top-remaining-affordable preference. A company is skipped
    once it has no remaining viable target. Loop until no company makes a
    pick in a full round.

    Mutates company.budget, roster.runners (in-place via the caller), and
    free_agents (removes claimed runners). Returns {company_name: [signed_runners]}
    so the caller can log the transaction.
    """
    signed: dict[str, list[Runner]] = {c.name: [] for c in companies}
    # Defensive: a runner could appear in multiple preference lists; track ownership.
    claimed_ids: set[int] = set()

    def _next_pick(co_name: str) -> tuple[Runner, float] | None:
        company = next(c for c in companies if c.name == co_name)
        roster = rosters[co_name]
        if len(roster.runners) >= target_roster_size:
            return None
        for runner, bid in bids_by_company.get(co_name, []):
            if runner.id in claimed_ids:
                continue
            if runner not in free_agents:
                continue
            if company.budget >= bid:
                return runner, bid
        return None

    while True:
        order = list(companies)
        rng.shuffle(order)
        made_pick = False
        for company in order:
            pick = _next_pick(company.name)
            if pick is None:
                continue
            runner, bid = pick
            company.budget -= bid
            free_agents.remove(runner)
            claimed_ids.add(runner.id)
            runner.company_name = company.name
            runner.weeks_orphaned = 0
            rosters[company.name].runners.append(runner)
            signed[company.name].append(runner)
            made_pick = True
        if not made_pick:
            break

    return signed


# ---------------------------------------------------------------------------
# FREE-AGENT POOL LIFECYCLE
# ---------------------------------------------------------------------------
def release_to_free_agents(runner: Runner, free_agents: list[Runner]) -> None:
    """Move a runner into the free-agent pool.

    Preserves: shell_affinities, net_loot, eliminations, deployments_survived,
               extraction_attempts/successes, shell_history (history is history).
    Resets:    current_shell (will be repurchased on rehire), weeks_orphaned,
               _died_this_week sentinel.

    This is the consciousness-persists model from runners.py — the bio-synthetic
    body is gone, but the runner's experience and earned affinity are intact.
    """
    runner.current_shell = ""
    runner.weeks_orphaned = 0
    if hasattr(runner, "_died_this_week"):
        delattr(runner, "_died_this_week")
    free_agents.append(runner)


def tick_free_agent_pool(
    free_agents: list[Runner],
    total_employed: int,
    market: ShellMarket,
    used_names: set[str],
    id_supplier: RunnerIdCounter,
) -> tuple[list[Runner], list[Runner]]:
    """Age the free-agent pool one week.

    1. Increment weeks_orphaned for everyone currently idle.
    2. Retire anyone past ORPHAN_RETIRE_AFTER_WEEKS (they leave the world).
    3. If total_employed + len(free_agents) < MIN_GLOBAL_POOL, spawn rookies
       into the pool until the floor is met.

    Returns (retired, spawned) for logging.
    """
    for r in free_agents:
        r.weeks_orphaned += 1

    retired = [r for r in free_agents if r.weeks_orphaned > ORPHAN_RETIRE_AFTER_WEEKS]
    free_agents[:] = [r for r in free_agents if r not in retired]

    spawned: list[Runner] = []
    deficit = MIN_GLOBAL_POOL - (total_employed + len(free_agents))
    while deficit > 0:
        rookie = _hire_one(
            company_name="",          # unemployed; assigned on rehire
            runner_id=id_supplier(),
            market=market,
            used_names=used_names,
        )
        used_names.add(rookie.name)
        # Free agents don't actively wear shells — the recruit kept the shell
        # they bought during _hire_one, but we clear it so the hiring company
        # makes a fresh purchase decision when they sign the runner.
        rookie.current_shell = ""
        rookie.credit_balance = RECRUIT_ALLOWANCE  # restore (they didn't actually buy yet)
        spawned.append(rookie)
        free_agents.append(rookie)
        deficit -= 1

    return retired, spawned
