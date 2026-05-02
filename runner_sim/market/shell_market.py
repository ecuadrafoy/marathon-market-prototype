"""
Shell market — adoption-share-driven pricing for the seven shells.

Capability-only `choose_best_shell` from runner_sim.shells always picks
Destroyer / Thief / Triage. By giving shells prices that scale with
adoption share and runners a budget, we resolve the middle-shell
dominance problem through cost arbitrage:

  - High-adoption shells (the capability winners) get expensive.
  - Recruits whose RECRUIT_ALLOWANCE can't cover the premium fall to
    cheaper middle shells (Vandal, Assassin, Recon, Rook).
  - Equilibrium: prices stabilize where the marginal recruit is indifferent
    between an expensive popular shell and a cheap niche shell.

See docs/runner_design.md:273-305 for the design rationale.
"""

from __future__ import annotations
import collections
from dataclasses import dataclass, field
from typing import Iterable

from runner_sim.runners import Runner, _affinity_score, _runner_attrs, _shell_affinity_vec
from runner_sim.runners import RUNNER_WEIGHT, SHELL_WEIGHT
from runner_sim.shells import Shell, SHELL_ROSTER


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
BASE_SHELL_PRICE = 200.0           # anchor price; tunable via calibration
SHELL_PRICE_SENSITIVITY = 4.0      # k — how steeply price reacts to over/under-adoption
                                   # k=2.0: gentle, premium shells stay affordable longer
                                   # k=4.0: sharp, premium hits 250cr budget at 20.5% adoption
                                   # k=4.0 forces wider middle-shell adoption (Vandal/Assassin)
N_SHELLS = len(SHELL_ROSTER)       # 7


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class ShellMarket:
    """Tracks current shell prices, plus a history of adoption + price snapshots.

    History is appended once per call to update_prices (i.e. once per week).
    Indexed: history[i] corresponds to the state at the END of week i+1.
    """
    prices: dict[str, float] = field(default_factory=dict)
    adoption_history: list[dict[str, int]] = field(default_factory=list)
    price_history: list[dict[str, float]] = field(default_factory=list)


def make_initial_market() -> ShellMarket:
    """Bootstrap market with uniform prices at BASE_SHELL_PRICE for every shell."""
    return ShellMarket(prices={s.name: BASE_SHELL_PRICE for s in SHELL_ROSTER})


# ---------------------------------------------------------------------------
# PRICE UPDATE
# ---------------------------------------------------------------------------
def update_prices(market: ShellMarket, all_runners: Iterable[Runner]) -> None:
    """Recompute prices based on current adoption share across all rosters.

    Formula (per shell s):
        adopted_share[s] = count(runners wearing s) / total_runners
        fair_share       = 1 / N_SHELLS
        price[s] = BASE_SHELL_PRICE * (1 + k * (adopted_share[s] - fair_share))

    A shell adopted by exactly the fair share (1/7 ≈ 14.3%) sits at the base
    price. Above-fair-share shells get more expensive linearly; below-fair
    get cheaper. With k=2.0:
        - 50% adoption → price = BASE * (1 + 2*(0.5 - 0.143)) = BASE * 1.71
        -  0% adoption → price = BASE * (1 + 2*(0.0 - 0.143)) = BASE * 0.71
    """
    runners = list(all_runners)
    total = len(runners)
    if total == 0:
        return
    counts = collections.Counter(r.current_shell for r in runners)
    fair_share = 1.0 / N_SHELLS
    for shell in SHELL_ROSTER:
        adopted_share = counts.get(shell.name, 0) / total
        market.prices[shell.name] = BASE_SHELL_PRICE * (
            1.0 + SHELL_PRICE_SENSITIVITY * (adopted_share - fair_share)
        )
    # Snapshot for charts/debug
    market.adoption_history.append({s.name: counts.get(s.name, 0) for s in SHELL_ROSTER})
    market.price_history.append(dict(market.prices))


# ---------------------------------------------------------------------------
# CAPABILITY SCORING
# ---------------------------------------------------------------------------
def _effective_capability(runner: Runner, shell: Shell) -> float:
    """Scalar score for ranking shells by how well they fit a runner.

    Mirrors choose_best_shell's attribute-weighted greedy score (runner_sim/runners.py:128-130):
    each axis is weighted by how much of that attribute the runner already has,
    so a combat-heavy runner cares more about combat capability than support.
    Then scale by the runner's affinity score for that shell.
    """
    attrs = _runner_attrs(runner)
    inner_axes = attrs * (attrs * RUNNER_WEIGHT + _shell_affinity_vec(shell) * SHELL_WEIGHT)
    return float(inner_axes.sum() * _affinity_score(runner, shell.name))


# ---------------------------------------------------------------------------
# SHELL SELECTION (BUDGET-AWARE)
# ---------------------------------------------------------------------------
def choose_affordable_shell(runner: Runner, prices: dict[str, float], budget: float) -> Shell:
    """Pick the highest-effective-capability shell within budget.

    Falls back to the cheapest shell on the market if the runner can't afford
    anything (a 'broke recruit' takes whatever's available — they don't go
    unshelled).

    Returns the Shell object so callers can both deduct prices[shell.name]
    from the budget and assign runner.current_shell = shell.name.
    """
    affordable = [s for s in SHELL_ROSTER if prices[s.name] <= budget]
    if not affordable:
        return min(SHELL_ROSTER, key=lambda s: prices[s.name])
    return max(affordable, key=lambda s: _effective_capability(runner, s))
