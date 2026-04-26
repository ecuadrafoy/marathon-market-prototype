"""
Encounter module — squad formation and weekly resolution.

Takes a flat pool of active runners, organizes them into squads of three
regardless of company affiliation, pairs squads against each other for combat,
then resolves extraction for surviving squads. Returns one outcome per runner.
"""

from __future__ import annotations
import random
from dataclasses import dataclass

from .runners import Runner, effective_capability
from .shells import SHELL_BY_NAME


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
SQUAD_SIZE                  = 3
SUPPORT_COMBAT_BONUS        = 0.5
SUPPORT_EXTRACTION_BONUS    = 0.5
COMBAT_VARIANCE             = 0.15   # gaussian sigma applied to each squad's combat roll
BASE_SQUAD_YIELD            = 100.0  # baseline yield for an extraction before scaling by capability
EXTRACTION_YIELD_MULTIPLIER = 200.0  # additional yield scaled by squad's effective extraction


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
def _squad_capability(squad: list[Runner]) -> tuple[float, float, list[tuple[float, float, float]]]:
    """Return (combat_total, extraction_total, per_runner_breakdown).

    per_runner_breakdown[i] is (combat, extraction, support) for squad[i].
    Combat total includes SUPPORT_COMBAT_BONUS contribution.
    Extraction total includes SUPPORT_EXTRACTION_BONUS contribution.
    """
    breakdowns: list[tuple[float, float, float]] = []
    sum_combat = 0.0
    sum_extraction = 0.0
    sum_support = 0.0
    for runner in squad:
        shell = SHELL_BY_NAME[runner.current_shell]
        c, e, s = effective_capability(runner, shell)
        breakdowns.append((c, e, s))
        sum_combat += c
        sum_extraction += e
        sum_support += s
    combat_total = sum_combat + SUPPORT_COMBAT_BONUS * sum_support
    extraction_total = sum_extraction + SUPPORT_EXTRACTION_BONUS * sum_support
    return combat_total, extraction_total, breakdowns


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
def _resolve_combat(squad_a: list[Runner], squad_b: list[Runner]) -> tuple[list[Runner], list[Runner], list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Return (winner, loser, winner_breakdowns, loser_breakdowns)."""
    a_combat, _, a_breakdowns = _squad_capability(squad_a)
    b_combat, _, b_breakdowns = _squad_capability(squad_b)
    a_roll = a_combat + random.gauss(0.0, COMBAT_VARIANCE)
    b_roll = b_combat + random.gauss(0.0, COMBAT_VARIANCE)
    if a_roll >= b_roll:
        return squad_a, squad_b, a_breakdowns, b_breakdowns
    return squad_b, squad_a, b_breakdowns, a_breakdowns


def _distribute_extraction(squad: list[Runner], breakdowns: list[tuple[float, float, float]] | None = None) -> tuple[list[float], float]:
    """Compute per-runner yield. Return (per_runner_yields, squad_total_yield)."""
    if breakdowns is None:
        _, _, breakdowns = _squad_capability(squad)
    extraction_total = sum(b[1] for b in breakdowns) + SUPPORT_EXTRACTION_BONUS * sum(b[2] for b in breakdowns)
    squad_yield = BASE_SQUAD_YIELD + EXTRACTION_YIELD_MULTIPLIER * extraction_total

    per_runner_extraction = [b[1] + SUPPORT_EXTRACTION_BONUS * b[2] for b in breakdowns]
    total = sum(per_runner_extraction)
    if total <= 0:
        # equal split fallback
        share = squad_yield / len(squad)
        return [share] * len(squad), squad_yield
    return [squad_yield * (e / total) for e in per_runner_extraction], squad_yield


def _distribute_eliminations(losers: list[Runner], winner_breakdowns: list[tuple[float, float, float]]) -> list[int]:
    """Eliminations scored per winning runner, proportional to their combat contribution.

    Total eliminations to distribute equals the loser squad size.
    """
    combat_contribs = [b[0] for b in winner_breakdowns]
    total_kills = len(losers)
    total = sum(combat_contribs)
    if total <= 0:
        # equal split fallback
        base = total_kills // len(combat_contribs)
        remainder = total_kills - base * len(combat_contribs)
        return [base + (1 if i < remainder else 0) for i in range(len(combat_contribs))]
    raw = [total_kills * (c / total) for c in combat_contribs]
    floored = [int(x) for x in raw]
    leftover = total_kills - sum(floored)
    # distribute leftover by largest fractional remainder
    remainders = sorted(((raw[i] - floored[i], i) for i in range(len(raw))), reverse=True)
    for _, idx in remainders[:leftover]:
        floored[idx] += 1
    return floored


# ---------------------------------------------------------------------------
# WEEKLY RESOLUTION
# ---------------------------------------------------------------------------
def resolve_week(active_runners: list[Runner]) -> tuple[dict[int, WeeklyOutcome], EncounterReport]:
    """Resolve one week of encounters. Return (outcomes_by_runner_id, report).

    Outcomes are produced for every runner in `active_runners` (sit-outs get a
    'did not participate' outcome).
    """
    squads, sit_outs = form_squads(active_runners)
    pairs, uncontested = pair_squads(squads)

    outcomes: dict[int, WeeklyOutcome] = {}

    contested_records: list[tuple[list[Runner], list[Runner], list[Runner]]] = []

    for squad_a, squad_b in pairs:
        winner, loser, winner_breakdowns, _loser_breakdowns = _resolve_combat(squad_a, squad_b)
        contested_records.append((squad_a, squad_b, winner))

        # Losing squad: all eliminated, no extraction
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

        # Winning squad: extracts, scores eliminations
        kills_per_winner = _distribute_eliminations(loser, winner_breakdowns)
        per_runner_yields, _squad_yield = _distribute_extraction(winner, winner_breakdowns)
        for idx, runner in enumerate(winner):
            c, e, s = winner_breakdowns[idx]
            outcomes[runner.id] = WeeklyOutcome(
                runner_id=runner.id,
                participated=True,
                survived=True,
                extracted=True,
                eliminations_scored=kills_per_winner[idx],
                yield_received=per_runner_yields[idx],
                combat_contribution=c,
                extraction_contribution=e + SUPPORT_EXTRACTION_BONUS * s,
            )

    for squad in uncontested:
        per_runner_yields, _squad_yield = _distribute_extraction(squad)
        _, _, breakdowns = _squad_capability(squad)
        for idx, runner in enumerate(squad):
            c, e, s = breakdowns[idx]
            outcomes[runner.id] = WeeklyOutcome(
                runner_id=runner.id,
                participated=True,
                survived=True,
                extracted=True,
                eliminations_scored=0,
                yield_received=per_runner_yields[idx],
                combat_contribution=c,
                extraction_contribution=e + SUPPORT_EXTRACTION_BONUS * s,
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
