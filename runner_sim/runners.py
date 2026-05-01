"""
Runner module — persistent identities that inhabit shells across a career.

A runner is the consciousness; the shell is the body. The runner carries a
continuous (combat, extraction, support) attribute vector summing to 1.0.
Match results pull this vector toward the affinity profile of the shell the
runner is currently wearing — so specialization emerges from shell exposure
over time, with one attribute growing at the expense of the others.

Death is a stat counter, not a state — a runner whose squad loses combat is
respawned in a fresh shell next week. The bio-synthetic body is replaceable;
the consciousness persists.
"""

from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np

from .shells import Shell, SHELL_BY_NAME, SHELL_ROSTER


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
RUNNER_WEIGHT = 0.6   # weight of runner attributes in effective capability
SHELL_WEIGHT  = 0.4   # weight of shell affinities (must sum with RUNNER_WEIGHT to 1.0)

AFFINITY_PER_WEEK = 0.05   # base shell affinity gained per surviving week
AFFINITY_CAP      = 1.0
AFFINITY_FLOOR    = 0.2    # minimum effective affinity score so brand-new runners aren't useless

ATTRIBUTE_DRIFT_RATE = 0.05  # EMA step toward shell affinity vector per surviving week
                             # half-life ~14 weeks; ~72% converged after 25 weeks of survival

assert abs((RUNNER_WEIGHT + SHELL_WEIGHT) - 1.0) < 1e-9, "RUNNER_WEIGHT + SHELL_WEIGHT must equal 1.0"


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class Runner:
    # --- identity (fixed at creation) ---
    id: int
    name: str
    company_name: str

    # --- attributes (career; sum to 1.0; drift toward current shell on survival) ---
    combat: float
    extraction: float
    support: float

    # --- state (changes week to week) ---
    current_shell: str

    # --- capability bookkeeping ---
    shell_affinities: dict[str, float] = field(default_factory=lambda: {s.name: 0.0 for s in SHELL_ROSTER})

    # --- per-week shell record ---
    # One entry per simulated week — the shell name the runner used in that week's
    # encounter. Indexed: shell_history[i] corresponds to week i+1.
    shell_history: list[str] = field(default_factory=list)

    # --- pure leaderboard stats (no longer feed back into capability) ---
    extraction_attempts: int = 0
    extraction_successes: int = 0
    net_loot: float = 0.0
    eliminations: int = 0
    death_count: int = 0

    # --- spending budget (debited on shell purchase at recruitment;
    #     credited weekly by per-runner extraction credit share) ---
    credit_balance: float = 0.0


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _runner_attrs(runner: Runner) -> np.ndarray:
    """Return runner's (combat, extraction, support) as a 3-vector."""
    return np.array([runner.combat, runner.extraction, runner.support])


def _shell_affinity_vec(shell: Shell) -> np.ndarray:
    """Return shell's (combat_affinity, extraction_affinity, support_affinity) as a 3-vector."""
    return np.array([shell.combat_affinity, shell.extraction_affinity, shell.support_affinity])


def _affinity_score(runner: Runner, shell_name: str) -> float:
    raw = runner.shell_affinities.get(shell_name, 0.0)
    return float(np.clip(max(AFFINITY_FLOOR, raw), 0.0, AFFINITY_CAP))


def extraction_success_rate(runner: Runner) -> float:
    if runner.extraction_attempts == 0:
        return 0.0
    return runner.extraction_successes / runner.extraction_attempts


# ---------------------------------------------------------------------------
# CAPABILITY
# ---------------------------------------------------------------------------
def effective_capability(runner: Runner, shell: Shell) -> tuple[float, float, float]:
    """Return (combat, extraction, support) effective capability for this week.

    Formula per axis X:
        effective_X = (runner.X * RUNNER_WEIGHT + shell.X_affinity * SHELL_WEIGHT)
                      * shell_affinity_score
    """
    eff = (_runner_attrs(runner) * RUNNER_WEIGHT + _shell_affinity_vec(shell) * SHELL_WEIGHT) * _affinity_score(runner, shell.name)
    return float(eff[0]), float(eff[1]), float(eff[2])


# ---------------------------------------------------------------------------
# SHELL SELECTION (RUNNER AI)
# ---------------------------------------------------------------------------
def choose_best_shell(runner: Runner, shells: list[Shell]) -> Shell:
    """Greedy: pick the shell maximizing attribute-weighted effective capability.

    Each axis is weighted by how much of that attribute the runner has, so a
    combat-heavy runner cares more about combat capability than support. The
    sum across all three axes would be invariant to shell choice (both runner
    attributes and shell affinities sum to 1.0), so the attribute weighting is
    what makes alignment matter.

    Stickiness comes from `shell_affinities`: a runner who has spent time in a
    shell has a higher affinity score there, making that shell more attractive
    until alignment elsewhere is decisively better.
    """
    attrs = _runner_attrs(runner)
    def weighted_capability(shell: Shell) -> float:
        inner_axes = attrs * (attrs * RUNNER_WEIGHT + _shell_affinity_vec(shell) * SHELL_WEIGHT)
        return float(inner_axes.sum() * _affinity_score(runner, shell.name))
    return max(shells, key=weighted_capability)


def switch_shell(runner: Runner, new_shell_name: str) -> bool:
    """Move the runner into a new shell. Returns True if shell actually changed."""
    if new_shell_name == runner.current_shell:
        return False
    runner.current_shell = new_shell_name
    return True


# ---------------------------------------------------------------------------
# DRIFT & AFFINITY
# ---------------------------------------------------------------------------
def gain_affinity(runner: Runner, shell_name: str, base_amount: float = AFFINITY_PER_WEEK) -> None:
    raw = runner.shell_affinities.get(shell_name, 0.0) + base_amount
    runner.shell_affinities[shell_name] = float(np.clip(raw, 0.0, AFFINITY_CAP))


def drift_attributes(runner: Runner, shell: Shell, rate: float = ATTRIBUTE_DRIFT_RATE) -> None:
    """EMA step: each surviving week, runner attributes move slightly toward the shell's
    affinity vector. Sum stays at 1.0 because both the current attributes and the shell
    affinities sum to 1.0, so the per-axis deltas sum to 0.

        new = old + rate * (target - old)
    """
    new = _runner_attrs(runner) + rate * (_shell_affinity_vec(shell) - _runner_attrs(runner))
    runner.combat, runner.extraction, runner.support = float(new[0]), float(new[1]), float(new[2])
