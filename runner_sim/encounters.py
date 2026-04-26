"""
Encounter module — squad formation and weekly resolution.

Takes a flat pool of active runners, organizes them into squads of three
regardless of company affiliation, pairs squads against each other for combat,
then resolves extraction for surviving squads. Returns one outcome per runner.
"""

from __future__ import annotations
import random
from dataclasses import dataclass

import numpy as np

from .runners import Runner, effective_capability
from .shells import SHELL_BY_NAME


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
SQUAD_SIZE                  = 3
SUPPORT_COMBAT_BONUS        = 0.5
COMBAT_VARIANCE             = 0.15   # gaussian sigma applied to each squad's combat roll
BASE_SQUAD_YIELD            = 100.0  # baseline yield for an extraction before scaling by capability
EXTRACTION_YIELD_MULTIPLIER = 200.0  # additional yield scaled by squad's effective extraction
SUPPORT_YIELD_AMPLIFIER     = 0.5    # support multiplies whole-squad yield: yield *= (1 + amp * sum_support)
                                     # Support runners boost team yield but earn personal share only
                                     # in proportion to their own eff_extraction.


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class WeeklyOutcome:
    runner_id: int
    participated: bool
    survived: bool
    extracted: bool
    eliminations_scored: int
    yield_received: float
    combat_contribution: float
    extraction_contribution: float


@dataclass
class EncounterReport:
    """Human-readable description of one week's encounters — used by the harness for printing."""
    squads: list[list[Runner]]
    sit_outs: list[Runner]
    contested_pairs: list[tuple[list[Runner], list[Runner], list[Runner]]]   # (a, b, winner)
    uncontested: list[list[Runner]]


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _squad_breakdown(squad: list[Runner]) -> np.ndarray:
    """Per-runner (combat, extraction, support) effective capability matrix.

    Shape: (len(squad), 3). Row i is the (c, e, s) tuple for squad[i].
    """
    return np.array([effective_capability(r, SHELL_BY_NAME[r.current_shell]) for r in squad])


def _squad_combat(breakdown: np.ndarray) -> float:
    """Squad combat score = sum(eff_combat) + SUPPORT_COMBAT_BONUS * sum(eff_support)."""
    sums = breakdown.sum(axis=0)   # [combat_total, extraction_total, support_total]
    return float(sums[0] + SUPPORT_COMBAT_BONUS * sums[2])


# ---------------------------------------------------------------------------
# SQUAD FORMATION & PAIRING
# ---------------------------------------------------------------------------
def form_squads(active_runners: list[Runner]) -> tuple[list[list[Runner]], list[Runner]]:
    """Shuffle and group into squads of SQUAD_SIZE. Remainders sit out."""
    pool = list(active_runners)
    random.shuffle(pool)
    full_squad_count = len(pool) // SQUAD_SIZE
    squads = [pool[i * SQUAD_SIZE : (i + 1) * SQUAD_SIZE] for i in range(full_squad_count)]
    sit_outs = pool[full_squad_count * SQUAD_SIZE :]
    return squads, sit_outs


def pair_squads(squads: list[list[Runner]]) -> tuple[list[tuple[list[Runner], list[Runner]]], list[list[Runner]]]:
    """Randomly pair squads. Odd squad goes uncontested."""
    pool = list(squads)
    random.shuffle(pool)
    pairs: list[tuple[list[Runner], list[Runner]]] = []
    while len(pool) >= 2:
        a = pool.pop()
        b = pool.pop()
        pairs.append((a, b))
    uncontested = pool   # 0 or 1 squad remaining
    return pairs, uncontested


# ---------------------------------------------------------------------------
# COMBAT & EXTRACTION
# ---------------------------------------------------------------------------
def _resolve_combat(squad_a: list[Runner], squad_b: list[Runner]) -> tuple[list[Runner], list[Runner], np.ndarray, np.ndarray]:
    """Return (winner, loser, winner_breakdown, loser_breakdown)."""
    a_breakdown = _squad_breakdown(squad_a)
    b_breakdown = _squad_breakdown(squad_b)
    a_roll = _squad_combat(a_breakdown) + random.gauss(0.0, COMBAT_VARIANCE)
    b_roll = _squad_combat(b_breakdown) + random.gauss(0.0, COMBAT_VARIANCE)
    if a_roll >= b_roll:
        return squad_a, squad_b, a_breakdown, b_breakdown
    return squad_b, squad_a, b_breakdown, a_breakdown


def _distribute_extraction(breakdown: np.ndarray) -> tuple[np.ndarray, float]:
    """Compute per-runner yield. Return (per_runner_yields, squad_total_yield).

    Squad yield: (BASE + EXTRACTION_MULT * sum_extraction) amplified multiplicatively
    by (1 + SUPPORT_YIELD_AMPLIFIER * sum_support). Support runners boost the entire
    squad's payoff but do not claim a personal share — personal share is proportional
    to eff_extraction only.
    """
    eff_extraction = breakdown[:, 1]
    sum_extraction = eff_extraction.sum()
    sum_support    = breakdown[:, 2].sum()
    base_yield  = BASE_SQUAD_YIELD + EXTRACTION_YIELD_MULTIPLIER * sum_extraction
    squad_yield = base_yield * (1.0 + SUPPORT_YIELD_AMPLIFIER * sum_support)

    if sum_extraction <= 0:
        # Equal split fallback — every runner has zero eff_extraction (extreme corner case).
        return np.full(len(breakdown), squad_yield / len(breakdown)), float(squad_yield)
    return squad_yield * eff_extraction / sum_extraction, float(squad_yield)


def _distribute_eliminations(losers_count: int, winner_breakdown: np.ndarray) -> np.ndarray:
    """Eliminations scored per winning runner, proportional to combat contribution.

    Total eliminations to distribute equals the loser squad size. Leftover from
    integer flooring is distributed by largest fractional remainder.
    """
    combat = winner_breakdown[:, 0]
    if combat.sum() <= 0:
        # Equal split fallback.
        base = losers_count // len(combat)
        floored = np.full(len(combat), base, dtype=int)
        remainder = losers_count - base * len(combat)
        floored[:remainder] += 1
        return floored
    raw = losers_count * combat / combat.sum()
    floored = raw.astype(int)
    leftover = losers_count - int(floored.sum())
    if leftover > 0:
        # Add 1 to the runners with the largest fractional remainders.
        top_indices = np.argsort(floored - raw)[:leftover]   # ascending -> most negative remainder = largest fractional part
        floored[top_indices] += 1
    return floored


# ---------------------------------------------------------------------------
# WEEKLY RESOLUTION
# ---------------------------------------------------------------------------
def resolve_week(active_runners: list[Runner]) -> tuple[dict[int, WeeklyOutcome], EncounterReport]:
    """Resolve one week of encounters. Return (outcomes_by_runner_id, report).

    Produces an outcome for every runner in `active_runners` (sit-outs get a
    'did not participate' outcome).
    """
    squads, sit_outs = form_squads(active_runners)
    pairs, uncontested = pair_squads(squads)

    outcomes: dict[int, WeeklyOutcome] = {}
    contested_records: list[tuple[list[Runner], list[Runner], list[Runner]]] = []

    for squad_a, squad_b in pairs:
        winner, loser, winner_breakdown, _loser_breakdown = _resolve_combat(squad_a, squad_b)
        contested_records.append((squad_a, squad_b, winner))

        # Losing squad: all eliminated, no extraction.
        for runner in loser:
            outcomes[runner.id] = WeeklyOutcome(
                runner_id=runner.id,
                participated=True,
                survived=False,
                extracted=False,
                eliminations_scored=0,
                yield_received=0.0,
                combat_contribution=0.0,
                extraction_contribution=0.0,
            )

        # Winning squad: extracts, scores eliminations.
        kills = _distribute_eliminations(len(loser), winner_breakdown)
        yields, _ = _distribute_extraction(winner_breakdown)
        for idx, runner in enumerate(winner):
            outcomes[runner.id] = WeeklyOutcome(
                runner_id=runner.id,
                participated=True,
                survived=True,
                extracted=True,
                eliminations_scored=int(kills[idx]),
                yield_received=float(yields[idx]),
                combat_contribution=float(winner_breakdown[idx, 0]),
                extraction_contribution=float(winner_breakdown[idx, 1]),
            )

    for squad in uncontested:
        breakdown = _squad_breakdown(squad)
        yields, _ = _distribute_extraction(breakdown)
        for idx, runner in enumerate(squad):
            outcomes[runner.id] = WeeklyOutcome(
                runner_id=runner.id,
                participated=True,
                survived=True,
                extracted=True,
                eliminations_scored=0,
                yield_received=float(yields[idx]),
                combat_contribution=float(breakdown[idx, 0]),
                extraction_contribution=float(breakdown[idx, 1]),
            )

    for runner in sit_outs:
        outcomes[runner.id] = WeeklyOutcome(
            runner_id=runner.id,
            participated=False,
            survived=True,
            extracted=False,
            eliminations_scored=0,
            yield_received=0.0,
            combat_contribution=0.0,
            extraction_contribution=0.0,
        )

    report = EncounterReport(
        squads=squads,
        sit_outs=sit_outs,
        contested_pairs=contested_records,
        uncontested=uncontested,
    )
    return outcomes, report
