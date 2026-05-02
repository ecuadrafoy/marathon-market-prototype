"""
Tick-based zone simulation — the core engine.

Each zone run starts with all squads present at tick 0. The pool is finite
and shared. Per tick, in order across all active squads:

  1. Exploration roll      → may draw an item from the pool
  2. Encounter check       → randomly pair active squads, both decide engage/disengage
  3. Combat resolution     → if both engaged, resolve combat + transfer kill-loot
  4. Extraction decision   → each surviving squad may choose to extract
  5. Tick counter advance  → ticks_since_last_find increments for non-finders

The match log accumulates one line per event for human-readable replay.

The pool is a simple list[Item]; depletion is just .pop(). Order within the pool
doesn't matter — items were already weighted at spawn time.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field

import numpy as np

from ..encounters import COMBAT_VARIANCE, _squad_breakdown, _squad_combat
from ..runners import Runner
from .encounter_ai import should_engage
from .extraction_ai import (
    Doctrine,
    Item,
    SquadLoot,
    SquadPerception,
    Tier,
    should_extract,
    squad_doctrine,
)
from .zones import Zone


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
DEFAULT_MAX_TICKS         = 8       # length of a zone run
EXPLORATION_BASE_RATE     = 0.55    # base discovery probability before scaling
EXPLORATION_EXTRACTION_K  = 0.35    # how strongly eff_extraction boosts discovery
ENCOUNTER_BASE_PROB       = 0.45    # baseline pairing probability per pair per tick
OPPONENT_ESTIMATE_NOISE   = 0.15    # gaussian sigma on opponent strength estimate


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class Squad:
    """A squad's full state during a zone run.

    Created fresh per zone run from a list of Runner objects. Doctrine is
    derived once at construction from the dominant shell type.
    """
    name: str                                # human-readable label (e.g. "Alpha")
    runners: list[Runner]
    doctrine: Doctrine
    loot: SquadLoot                          = field(default_factory=SquadLoot)
    ticks_since_last_find: int               = 0
    had_encounter_this_run: bool             = False
    took_damage_this_run: bool               = False
    eliminated: bool                         = False
    extracted: bool                          = False

    @property
    def active(self) -> bool:
        return not self.eliminated and not self.extracted


@dataclass
class CombatEvent:
    """One combat resolution between two squads.

    Recorded for downstream attribution: callers (e.g. the market layer)
    distribute kill credit across the winner's runners proportional to
    their effective combat contribution. Avoids parsing match_log strings.
    """
    tick: int
    winner_squad: str
    loser_squad: str
    loser_runner_count: int


@dataclass
class ZoneRunResult:
    """Outcome of a single zone run."""
    zone_name: str
    squads: list[Squad]                      # final state of all squads
    match_log: list[str]                     # human-readable event sequence
    pool_size_at_start: int
    pool_size_at_end: int
    combat_events: list[CombatEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# FACTORY HELPERS
# ---------------------------------------------------------------------------
SQUAD_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf",
    "Hotel", "India", "Juliet", "Kilo", "Lima", "Mike", "November",
]


def make_squad(name: str, runners: list[Runner]) -> Squad:
    """Build a Squad from a list of runners. Doctrine derived from shells."""
    shell_names = [r.current_shell for r in runners]
    return Squad(name=name, runners=runners, doctrine=squad_doctrine(shell_names))


# ---------------------------------------------------------------------------
# LOG FORMATTING
# ---------------------------------------------------------------------------
def _format_pool_spawn(pool: list[Item]) -> str:
    """Compact one-line summary of the spawned pool, grouped by item name."""
    if not pool:
        return "[T0] Pool spawned: empty."
    counts: dict[str, int] = {}
    for item in pool:
        counts[item.name] = counts.get(item.name, 0) + 1
    parts = [f"{name} x{count}" for name, count in counts.items()]
    return f"[T0] Pool spawned: {', '.join(parts)}."


# ---------------------------------------------------------------------------
# POOL SPAWNER
# ---------------------------------------------------------------------------
def spawn_zone_pool(zone: Zone, item_catalog: list[Item]) -> list[Item]:
    """Draw `zone.pool_size` items weighted by each item's zone weight for this zone.

    Items with weight 0.0 in this zone are excluded from the draw entirely.
    Duplicates are allowed — the same item can appear multiple times in one pool.
    """
    eligible: list[Item] = []
    weights: list[float] = []
    for item in item_catalog:
        w = item.weight_for(zone.name)
        if w > 0:
            eligible.append(item)
            weights.append(w)

    if not eligible:
        return []
    return random.choices(eligible, weights=weights, k=zone.pool_size)


# ---------------------------------------------------------------------------
# PER-SQUAD HELPERS
# ---------------------------------------------------------------------------
def _squad_eff_extraction(squad: Squad) -> float:
    """Sum of effective extraction across all runners in the squad."""
    breakdown = _squad_breakdown(squad.runners)
    return float(breakdown[:, 1].sum())


def _squad_eff_combat(squad: Squad) -> float:
    """Squad combat score (reuses the formula from encounters.py)."""
    return _squad_combat(_squad_breakdown(squad.runners))


def _build_perception(squad: Squad, tick: int, max_ticks: int) -> SquadPerception:
    """Snapshot the squad's experiential signals for the AI."""
    return SquadPerception(
        ticks_since_last_find=squad.ticks_since_last_find,
        had_encounter_this_run=squad.had_encounter_this_run,
        took_damage_this_run=squad.took_damage_this_run,
        tick=tick,
        max_ticks=max_ticks,
    )



# ---------------------------------------------------------------------------
# TICK PHASES
# ---------------------------------------------------------------------------
def _phase_explore(
    squad: Squad,
    pool: list[Item],
    zone: Zone,
    log: list[str],
    tick: int,
) -> bool:
    """One squad's exploration roll. Returns True if an item was found."""
    if not pool:
        return False

    eff_ext = _squad_eff_extraction(squad)
    # Discovery probability scales with extraction capability and is suppressed
    # in harder zones (difficulty makes finding things harder).
    p_find = EXPLORATION_BASE_RATE * (1.0 - zone.difficulty) + EXPLORATION_EXTRACTION_K * eff_ext
    p_find = max(0.05, min(p_find, 0.95))

    if random.random() < p_find:
        item = pool.pop(random.randrange(len(pool)))
        squad.loot.items.append(item)
        log.append(
            f"[T{tick}] {squad.name} ({squad.doctrine.value.upper()}): "
            f"found {item.name} ({item.tier.name}, {item.credit_value}cr). "
            f"Pool: {len(pool)} left."
        )
        return True
    return False


def _phase_encounters(
    squads: list[Squad],
    log: list[str],
    tick: int,
) -> list[tuple[Squad, Squad]]:
    """Pair active squads at random, decide engage/disengage. Returns combat pairs."""
    active = [s for s in squads if s.active]
    if len(active) < 2:
        return []

    # Encounter probability scales with squad density — more squads, more crossings.
    # Simple model: shuffle the active list, walk in pairs, each pair rolls.
    random.shuffle(active)
    combat_pairs: list[tuple[Squad, Squad]] = []
    for i in range(0, len(active) - 1, 2):
        a, b = active[i], active[i + 1]
        if random.random() >= ENCOUNTER_BASE_PROB:
            continue   # paths didn't cross this tick

        a.had_encounter_this_run = True
        b.had_encounter_this_run = True

        a_combat = _squad_eff_combat(a)
        b_combat = _squad_eff_combat(b)
        # Each squad gets a noisy estimate of the other's strength
        a_estimate_of_b = max(0.01, b_combat + random.gauss(0.0, OPPONENT_ESTIMATE_NOISE))
        b_estimate_of_a = max(0.01, a_combat + random.gauss(0.0, OPPONENT_ESTIMATE_NOISE))

        a_engages = should_engage(a.doctrine, a_combat, a_estimate_of_b, a.loot)
        b_engages = should_engage(b.doctrine, b_combat, b_estimate_of_a, b.loot)

        if a_engages and b_engages:
            log.append(f"[T{tick}] {a.name} and {b.name} cross paths — both engage.")
            combat_pairs.append((a, b))
        else:
            decliner = a.name if not a_engages else b.name
            log.append(
                f"[T{tick}] {a.name} and {b.name} cross paths — {decliner} disengages."
            )
    return combat_pairs


def _phase_combat(
    combat_pairs: list[tuple[Squad, Squad]],
    log: list[str],
    tick: int,
    combat_events: list[CombatEvent] | None = None,
) -> None:
    """Resolve combat between engaged squad pairs. Apply kill-loot transfer.

    If `combat_events` is supplied, append a structured CombatEvent for each
    resolved fight so callers can attribute kills to specific runners.
    """
    for a, b in combat_pairs:
        # Compute breakdowns once — reused for both the roll and the log.
        a_bd   = _squad_breakdown(a.runners)
        b_bd   = _squad_breakdown(b.runners)
        a_base = _squad_combat(a_bd)
        b_base = _squad_combat(b_bd)
        a_var  = random.gauss(0.0, COMBAT_VARIANCE)
        b_var  = random.gauss(0.0, COMBAT_VARIANCE)
        a_roll = a_base + a_var
        b_roll = b_base + b_var
        winner, loser = (a, b) if a_roll >= b_roll else (b, a)

        winner.took_damage_this_run = True
        loser.eliminated = True

        if combat_events is not None:
            combat_events.append(CombatEvent(
                tick=tick,
                winner_squad=winner.name,
                loser_squad=loser.name,
                loser_runner_count=len(loser.runners),
            ))

        # Per-runner combat contribution strings.
        def _runner_parts(squad: Squad, bd: np.ndarray) -> str:
            return " | ".join(
                f"{r.name}/{r.current_shell[:3]}:{float(row[0]):.3f}"
                for r, row in zip(squad.runners, bd)
            )

        log.append(
            f"[T{tick}] Combat: {a.name} ({a.doctrine.value.upper()}) "
            f"vs {b.name} ({b.doctrine.value.upper()})"
        )
        log.append(
            f"[T{tick}]   {a.name:<8} {_runner_parts(a, a_bd)}"
            f"  →  base:{a_base:.3f}  var:{a_var:+.3f}  final:{a_roll:.3f}"
        )
        log.append(
            f"[T{tick}]   {b.name:<8} {_runner_parts(b, b_bd)}"
            f"  →  base:{b_base:.3f}  var:{b_var:+.3f}  final:{b_roll:.3f}"
        )

        # Kill-loot: Uncommon+ items transfer to winner; Commons abandoned.
        looted   = [item for item in loser.loot.items if item.tier >= Tier.UNCOMMON]
        abandoned = [item for item in loser.loot.items if item.tier < Tier.UNCOMMON]
        winner.loot.items.extend(looted)

        if looted:
            loot_summary = ", ".join(f"{i.name} ({i.tier.name})" for i in looted)
            log.append(f"[T{tick}]   → {winner.name} wins. Kill-loot: {loot_summary}.")
        else:
            log.append(
                f"[T{tick}]   → {winner.name} wins. "
                f"Kill-loot: nothing worth taking ({len(abandoned)} Common abandoned)."
            )


def _phase_extraction(
    squads: list[Squad],
    log: list[str],
    tick: int,
    max_ticks: int,
) -> None:
    """Each surviving non-extracted squad decides whether to extract."""
    for squad in squads:
        if not squad.active:
            continue
        perception = _build_perception(squad, tick, max_ticks)
        if should_extract(squad.doctrine, squad.loot, perception):
            squad.extracted = True
            credits = squad.loot.total_credits()
            log.append(
                f"[T{tick}] {squad.name} extracts with {len(squad.loot.items)} items "
                f"({credits}cr)."
            )


# ---------------------------------------------------------------------------
# MAIN ZONE RUN
# ---------------------------------------------------------------------------
def run_zone(
    zone: Zone,
    squads: list[Squad],
    item_catalog: list[Item],
    max_ticks: int = DEFAULT_MAX_TICKS,
) -> ZoneRunResult:
    """Run one zone for up to max_ticks ticks. Mutates the squads in place."""
    pool = spawn_zone_pool(zone, item_catalog)
    pool_at_start = len(pool)
    log: list[str] = []
    combat_events: list[CombatEvent] = []

    log.append(
        f"=== {zone.name} (difficulty {zone.difficulty}, pool_size {zone.pool_size}) ==="
    )
    log.append(_format_pool_spawn(pool))
    squad_summary = ", ".join(f"{s.name}({s.doctrine.value.upper()})" for s in squads)
    log.append(f"[T0] {len(squads)} squads enter: {squad_summary}")

    for tick in range(1, max_ticks + 1):
        # Phase 1: Exploration
        finders: set[int] = set()
        for squad in [s for s in squads if s.active]:
            if _phase_explore(squad, pool, zone, log, tick):
                finders.add(id(squad))

        # Phase 2: Encounter check
        combat_pairs = _phase_encounters(squads, log, tick)

        # Phase 3: Combat
        _phase_combat(combat_pairs, log, tick, combat_events=combat_events)

        # Phase 4: Extraction decisions
        _phase_extraction(squads, log, tick, max_ticks)

        # Phase 5: Update ticks_since_last_find for non-finders
        for squad in squads:
            if squad.active:
                if id(squad) in finders:
                    squad.ticks_since_last_find = 0
                else:
                    squad.ticks_since_last_find += 1

        # Termination: nobody left active
        if not any(s.active for s in squads):
            break

    # Anyone still active at end-of-run gets a forced extraction.
    for squad in squads:
        if squad.active:
            squad.extracted = True
            log.append(
                f"[T{max_ticks}] {squad.name} extracts at run end with "
                f"{len(squad.loot.items)} items ({squad.loot.total_credits()}cr)."
            )

    return ZoneRunResult(
        zone_name=zone.name,
        squads=squads,
        match_log=log,
        pool_size_at_start=pool_at_start,
        pool_size_at_end=len(pool),
        combat_events=combat_events,
    )
