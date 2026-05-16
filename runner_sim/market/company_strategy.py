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

# Health signal — drives struggling/thriving heuristics (legacy derived view).
STRUGGLE_MA_WINDOW             = 3
STRUGGLE_THRESHOLD             = 0.97    # below MA × this → struggling
THRIVE_THRESHOLD               = 1.03    # above MA × this → thriving

# ── Strategic posture (continuous, persistent state) ──
# Two axes: momentum (fast EMA, "how are we doing now") and risk_appetite
# (slow accumulator, "what kind of company we've become"). All companies
# start at (0.0, 0.0); divergence is emergent from accumulated outcomes.
# Future event-system events will mutate these directly.
MOMENTUM_EMA_ALPHA              = 0.3    # weight on this-week signal; 0.7 retained → half-life ~2 wk
RISK_APPETITE_STEP              = 0.02   # nominal per-week drift on a mixed week
RISK_APPETITE_WIPE_PENALTY      = 0.08   # all-squads-eliminated trauma (4× baseline drift)
RISK_APPETITE_SWEEP_BONUS       = 0.04   # all-squads-extracted hot streak (2× baseline drift)
PRICE_CHANGE_NORM               = 5.0    # divides price_change_pct to land in ~[-1, +1]

# Health bucket thresholds when deriving Health from posture (for display/legacy).
POSTURE_HEALTH_THRIVE_AT        = 0.25
POSTURE_HEALTH_STRUGGLE_AT      = -0.25

# ── Loan system (emergency financing for struggling companies) ──
# When a company is about to sit out (roster < 6) with no remaining cash,
# they can auto-take a 1500cr loan to fund a recovery bid. Loans must be
# repaid within a quarter or they accrue a compounding valuation penalty.
LOAN_AMOUNT                     = 1500.0   # principal per loan
LOAN_TERM_WEEKS                 = 12       # repayment window (= QUARTERLY_REPORT_WEEKS)
LOAN_TRIGGER_BUDGET_THRESHOLD   = 500.0    # below this AND would sit out → trigger
# Auto-repay the moment budget can cover the principal. Originally set to
# 3000 but empirical 60-week diagnostics showed peak budgets in this economy
# rarely exceed 2000cr — at 3000 loans NEVER cycled, just stacked up overdue
# and inflicted compounding valuation penalties. Setting the threshold equal
# to LOAN_AMOUNT means "if you can afford to clear a loan, do it." Companies
# that earn back to 1500cr settle the oldest loan immediately. Side benefit:
# this generates loan_repaid events (+3 valuation score per cycle) and
# avoids future loan_overdue events (-5 score per quarter per outstanding
# loan), so loan cycling becomes a small but real valuation tailwind.
LOAN_REPAY_BUDGET_THRESHOLD     = LOAN_AMOUNT
MAX_OUTSTANDING_LOANS           = 3        # hard cap on stacking debt

Health = Literal["thriving", "neutral", "struggling"]


# ---------------------------------------------------------------------------
# LOAN — emergency financing instrument
# ---------------------------------------------------------------------------
@dataclass
class Loan:
    """An emergency loan a company can take when about to sit out.

    Auto-repaid when the company's budget rises above LOAN_REPAY_BUDGET_THRESHOLD.
    If left outstanding past the quarterly mark, accrues a -5 valuation counter
    at each subsequent quarterly tick (compounding pain). Repaying it fires a
    one-time +3 counter reward.
    """
    amount: float = LOAN_AMOUNT
    week_taken: int = 0
    repaid: bool = False
    week_repaid: int | None = None


def outstanding_loans(loans: list[Loan]) -> list[Loan]:
    """Filter to currently-outstanding (unrepaid) loans."""
    return [l for l in loans if not l.repaid]


def overdue_loans(loans: list[Loan], current_week: int) -> list[Loan]:
    """Outstanding loans whose age exceeds LOAN_TERM_WEEKS."""
    return [
        l for l in loans
        if not l.repaid and (current_week - l.week_taken) >= LOAN_TERM_WEEKS
    ]


def take_loan_if_needed(
    company,
    roster: CompanyRoster,
    current_week: int,
) -> Loan | None:
    """Auto-loan decision — call at the end of the AI cycle.

    Triggers a loan when ALL of:
      • budget < LOAN_TRIGGER_BUDGET_THRESHOLD
      • roster size below MIN_ROSTER_FOR_DEPLOYMENT (would sit out next week)
      • outstanding loans count < MAX_OUTSTANDING_LOANS

    Returns the new Loan (also appended to company.loans), or None.
    """
    from runner_sim.market.deployment import MIN_ROSTER_FOR_DEPLOYMENT
    if company.budget >= LOAN_TRIGGER_BUDGET_THRESHOLD:
        return None
    if len(roster.runners) >= MIN_ROSTER_FOR_DEPLOYMENT:
        return None
    if len(outstanding_loans(company.loans)) >= MAX_OUTSTANDING_LOANS:
        return None
    loan = Loan(amount=LOAN_AMOUNT, week_taken=current_week)
    company.budget += LOAN_AMOUNT
    company.loans.append(loan)
    return loan


def auto_repay_loan(
    company,
    current_week: int,
) -> Loan | None:
    """Auto-repay logic — call at the end of the AI cycle.

    When budget exceeds LOAN_REPAY_BUDGET_THRESHOLD and at least one loan is
    outstanding, pay off the OLDEST outstanding loan. Marks it repaid and
    returns it. Caller is responsible for firing the loan_repaid valuation
    event (kept out of this fn to avoid coupling to marathon_market.py).
    """
    outstanding = outstanding_loans(company.loans)
    if not outstanding:
        return None
    if company.budget < LOAN_REPAY_BUDGET_THRESHOLD:
        return None
    # Repay the oldest first (FIFO).
    oldest = min(outstanding, key=lambda l: l.week_taken)
    company.budget -= oldest.amount
    oldest.repaid = True
    oldest.week_repaid = current_week
    return oldest


# ---------------------------------------------------------------------------
# STRATEGIC POSTURE — continuous, persistent state on Company
# ---------------------------------------------------------------------------
@dataclass
class PostureState:
    """Two-axis strategic posture: how a company feels and who it's becoming.

    Both axes range over [-1.0, +1.0]:
      - momentum: fast EMA from this-week outcomes. Half-life ~2 weeks.
      - risk_appetite: slow accumulator from accumulated history. Takes
        ~10+ weeks to swing; traumas (full wipes) hit ~4× harder than
        baseline drift, so reputation is fragile.

    All companies init at (0.0, 0.0). No pre-loaded personality — every
    company starts identical and diverges purely from outcomes. Future
    events (MIDA / UESC / Arachne, random events) will mutate these
    fields directly; update_posture is the only writer today.
    """
    momentum: float = 0.0
    risk_appetite: float = 0.0
    weeks_observed: int = 0    # diagnostic; lets tests assert convergence pace


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
    """Per-company roster + financing changes recorded during one week's cycle.

    Roster events are split by *cause* so the UI can communicate timing —
    deaths happen *during* deployment, signings/drops/orphans happen *after*
    (when the AI reacts), loans happen at the very end of the AI cycle.
    """
    signed: list[str] = field(default_factory=list)                 # hired via bidding draft
    voluntarily_dropped: list[str] = field(default_factory=list)    # cut for being too expensive
    orphaned_unaffordable: list[str] = field(default_factory=list)  # couldn't make payroll
    died: list[str] = field(default_factory=list)                   # killed in zone deployment
    loans_taken: int = 0                                            # new loans this week
    loans_repaid: int = 0                                           # loans settled this week

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

    LEGACY: this function survives for callers that only have price history
    (e.g. UI / display code). The AI decision path no longer uses it — it
    reads `company.posture` directly. See `posture_to_health` for the
    posture-based equivalent.
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
# POSTURE UPDATE + DERIVED HEALTH VIEW
# ---------------------------------------------------------------------------
def update_posture(
    posture: PostureState,
    price_change_pct: float,
    squads_deployed: int,
    squads_returned: int,
    squads_eliminated: int,
) -> None:
    """Mutate posture in place from this week's outcome.

    Called at the end of simulate_week, after the bidding draft. The posture
    used by THIS week's deployment was the posture going INTO the week; we
    only update it here so that the next week's deploy + decisions read a
    posture that reflects what just happened.
    """
    # Momentum — fast EMA. Blend price signal + return rate as the per-week input.
    if squads_deployed > 0:
        net = squads_returned - squads_eliminated
        return_rate = max(-1.0, min(1.0, net / squads_deployed))
    else:
        return_rate = 0.0
    price_signal = max(-1.0, min(1.0, price_change_pct / PRICE_CHANGE_NORM))
    this_week = 0.5 * price_signal + 0.5 * return_rate
    posture.momentum = (1.0 - MOMENTUM_EMA_ALPHA) * posture.momentum + MOMENTUM_EMA_ALPHA * this_week
    posture.momentum = max(-1.0, min(1.0, posture.momentum))

    # Risk appetite — slow accumulator. Asymmetric: wipes 4× baseline, sweeps 2×.
    if squads_deployed == 0:
        pass  # sat out the week, no signal
    elif squads_eliminated == squads_deployed and squads_eliminated > 0:
        # Total wipe — trauma
        posture.risk_appetite -= RISK_APPETITE_WIPE_PENALTY
    elif squads_eliminated == 0 and squads_returned == squads_deployed:
        # Clean sweep — hot streak
        posture.risk_appetite += RISK_APPETITE_SWEEP_BONUS
    else:
        # Mixed week — drift based on net return rate sign
        direction = 1.0 if return_rate >= 0 else -1.0
        posture.risk_appetite += RISK_APPETITE_STEP * direction
    posture.risk_appetite = max(-1.0, min(1.0, posture.risk_appetite))

    posture.weeks_observed += 1


def posture_to_health(posture: PostureState) -> Health:
    """Bucket the continuous posture into the legacy Health enum.

    Used for display/UI code that still reads the three-state label. Decision
    logic should NOT route through this — read posture's continuous axes
    directly to preserve gradient response.
    """
    score = 0.5 * posture.momentum + 0.5 * posture.risk_appetite
    if score >= POSTURE_HEALTH_THRIVE_AT:
        return "thriving"
    if score <= POSTURE_HEALTH_STRUGGLE_AT:
        return "struggling"
    return "neutral"


# ---------------------------------------------------------------------------
# VOLUNTARY DROPS
# ---------------------------------------------------------------------------
def decide_voluntary_drops(
    company,
    roster: CompanyRoster,
    posture: PostureState,
) -> list[Runner]:
    """Drop expensive runners as a function of posture.

    Continuous behaviour:
      - threshold_multiplier = 2.5 + 1.5 × risk_appetite
        → 1.0× at risk=-1 (aggressive cuts when conservative), 4.0× at risk=+1 (basically never)
      - Skip the drop pass entirely when momentum and risk are both ≥ -0.1
        — a company that isn't hurting and isn't conservative keeps everyone.

    The runner mutation (removing drops from roster.runners) is in place.
    """
    if not roster.runners:
        return []
    # Companies that aren't hurting AND aren't conservative don't churn.
    if posture.momentum > -0.1 and posture.risk_appetite > -0.1:
        return []

    upkeeps = [r.upkeep_cost for r in roster.runners]
    median = statistics.median(upkeeps)
    threshold_multiplier = 2.5 + 1.5 * posture.risk_appetite
    threshold = threshold_multiplier * median
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
    posture: PostureState,
    max_slots: int,
) -> list[tuple[Runner, float]]:
    """Return a ranked list of (runner, bid_amount), driven by continuous posture.

    Three knobs scale with posture:
      - spend_multiplier   = 1.0 + 0.5 × max(risk + momentum, 0)
          → 1.0× when defensive/neutral, up to 2.0× when aggressive AND on a hot streak.
      - upkeep_cap_mult    = 1.1 + 0.6 × (risk + 1)
          → 1.1× median (defensive: only cheap rookies) up to 2.3× median (aggressive: chase veterans).
      - shell-preference blend: safe_w = 0.5×(1−risk), aggr_w = 0.5×(1+risk).
          → defensive companies prefer Recon/Triage affinity; aggressive prefer Destroyer/Assassin.
    """
    if max_slots <= 0 or not free_agents:
        return []

    # Ensure every candidate has a current upkeep cached for the bid math.
    for r in free_agents:
        refresh_upkeep(r)

    risk = posture.risk_appetite
    mood = posture.momentum

    spend_multiplier = 1.0 + 0.5 * max(risk + mood, 0.0)
    upkeep_cap_mult = 1.1 + 0.6 * (risk + 1.0)

    median_upkeep = _roster_median_upkeep(roster)

    safe_w = 0.5 * (1.0 - risk)
    aggr_w = 0.5 * (1.0 + risk)

    def preference_score(r: Runner) -> float:
        return safe_w * _safe_shell_score(r) + aggr_w * _aggressive_shell_score(r)

    # Upkeep cap only applies when the roster has a real reference. With an
    # empty roster the company has no anchor and shouldn't filter — desperate
    # empty companies need to build up regardless of posture.
    if median_upkeep > 0:
        cap = max(1.0, upkeep_cap_mult * median_upkeep)
        eligible = [r for r in free_agents if r.upkeep_cost <= cap]
        if not eligible:
            # Defensive fallback: nothing in range, take the cheapest anyway.
            eligible = sorted(free_agents, key=lambda r: r.upkeep_cost)[:max_slots]
    else:
        eligible = list(free_agents)

    # Sort by: highest preference (mix of safe + aggressive by risk), then cheapest.
    eligible.sort(key=lambda r: (-preference_score(r), r.upkeep_cost))
    return [(r, spend_multiplier * r.upkeep_cost) for r in eligible[:max_slots]]


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
