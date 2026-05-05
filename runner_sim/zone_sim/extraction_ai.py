"""
Extraction AI — per-tick decision logic for when a squad chooses to leave the zone.

Each tick, after exploration and any combat, each active squad runs should_extract().
If it returns True the squad leaves and their carried loot is locked in.

Doctrine is derived from the squad's dominant shell type and shapes the thresholds
used in the extraction decision. A squad is not a single runner — all runners in
the squad contribute to the doctrine calculation, but the dominant shell wins.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# ITEM TIERS
# ---------------------------------------------------------------------------
class Tier(int, Enum):
    """Rarity tiers for loot items. Higher value = rarer."""
    COMMON    = 1
    UNCOMMON  = 2
    RARE      = 3
    EPIC      = 4


# ---------------------------------------------------------------------------
# ITEM
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Item:
    """A loot item that can exist in zone pools and runner inventories.

    zone_weights maps zone name → base drop weight for that zone.
    A weight of 0.0 means the item never spawns there naturally.
    Weights are relative, not absolute probabilities — they are normalised
    at spawn time against all items eligible for that zone.
    """
    name: str
    tier: Tier
    credit_value: int                    # flat credit conversion for market layer
    zone_weights: dict[str, float]       # {zone_name: weight}

    def weight_for(self, zone_name: str) -> float:
        """Return the drop weight for the given zone, or 0.0 if not listed."""
        return self.zone_weights.get(zone_name, 0.0)


# ---------------------------------------------------------------------------
# ZONE STATE (ground truth — owned by the simulation engine, never passed to AI)
# ---------------------------------------------------------------------------
@dataclass
class ZoneState:
    """Ground truth state of the zone. Used by the tick loop to resolve events.

    The AI never receives this directly — it would be unrealistic for runners
    to know exact pool counts or hostile headcounts mid-run. Pass SquadPerception
    to should_extract() instead.
    """
    pool_remaining: int          # items still in the zone pool
    active_squads: int           # all squads still in zone (including this one)
    tick: int
    max_ticks: int


# ---------------------------------------------------------------------------
# SQUAD PERCEPTION (what the squad can actually sense — passed to the AI)
# ---------------------------------------------------------------------------
@dataclass
class SquadPerception:
    """Experiential signals available to a squad mid-run.

    No raw counts — only what runners would realistically perceive.
    The tick loop derives this from ZoneState + the squad's own history
    before calling should_extract().
    """
    ticks_since_last_find: int   # how long since they found anything (zone feels rich or thin)
    had_encounter_this_run: bool # true if they have crossed paths with any other squad
    took_damage_this_run: bool   # true if they sustained hits in combat (raises risk awareness)
    tick: int
    max_ticks: int

    def time_pressure(self) -> float:
        """Fraction of the run elapsed. 0.0 = just started, 1.0 = final tick."""
        return self.tick / self.max_ticks if self.max_ticks > 0 else 1.0

    def zone_feels_dry(self, threshold_ticks: int = 3) -> bool:
        """True if the squad hasn't found anything recently — implies thin pool."""
        return self.ticks_since_last_find >= threshold_ticks


# ---------------------------------------------------------------------------
# SQUAD LOOT CONTEXT (what the squad is currently carrying)
# ---------------------------------------------------------------------------
@dataclass
class SquadLoot:
    """Items currently secured by a squad mid-run."""
    items: list[Item] = field(default_factory=list)

    def best_tier(self) -> Tier | None:
        """Highest tier item currently carried. None if carrying nothing."""
        if not self.items:
            return None
        return max(self.items, key=lambda i: i.tier).tier

    def total_credits(self) -> int:
        return sum(i.credit_value for i in self.items)


# ---------------------------------------------------------------------------
# EXTRACTION DOCTRINE
# ---------------------------------------------------------------------------
class Doctrine(str, Enum):
    """Squad extraction personality, derived from dominant shell type."""
    GREEDY    = "greedy"       # Destroyer / Assassin — stays for more, combat-confident
    CAUTIOUS  = "cautious"     # Thief / Recon — extracts early, minimises risk
    BALANCED  = "balanced"     # Vandal / Rook — middle ground
    SUPPORT   = "support"      # Triage — stays to support, extracts last


# Shell → Doctrine mapping
SHELL_DOCTRINE: dict[str, Doctrine] = {
    "Destroyer": Doctrine.GREEDY,
    "Assassin":  Doctrine.GREEDY,
    "Thief":     Doctrine.CAUTIOUS,
    "Recon":     Doctrine.CAUTIOUS,
    "Vandal":    Doctrine.BALANCED,
    "Rook":      Doctrine.BALANCED,
    "Triage":    Doctrine.SUPPORT,
}


def squad_doctrine(shell_names: list[str]) -> Doctrine:
    """Derive doctrine from the most common shell in the squad.

    Ties broken by GREEDY > BALANCED > CAUTIOUS > SUPPORT, reflecting
    that aggressive intent overrides caution in mixed squads.
    """
    tally: dict[Doctrine, int] = {}
    for name in shell_names:
        d = SHELL_DOCTRINE.get(name, Doctrine.BALANCED)
        tally[d] = tally.get(d, 0) + 1
    priority = [Doctrine.GREEDY, Doctrine.BALANCED, Doctrine.CAUTIOUS, Doctrine.SUPPORT]
    top_count = max(tally.values())
    for d in priority:
        if tally.get(d, 0) == top_count:
            return d
    return Doctrine.BALANCED


# ---------------------------------------------------------------------------
# EXTRACTION DECISION
# ---------------------------------------------------------------------------
# Trees are loaded lazily on first call (and cached) so import order is
# tolerant — the registry needs `ai_conditions` imported before any tree loads,
# and lazy loading lets the test suite control that ordering.
_EXTRACTION_TREES: dict[Doctrine, "Tree"] = {}


def _get_extraction_tree(doctrine: Doctrine):
    if doctrine not in _EXTRACTION_TREES:
        from ai_tree.publisher import load_published
        from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves
        _EXTRACTION_TREES[doctrine] = load_published(f"extraction_{doctrine.value}")
    return _EXTRACTION_TREES[doctrine]


def should_extract(
    doctrine: Doctrine,
    loot: SquadLoot,
    perception: SquadPerception,
) -> bool:
    """Return True if the squad should extract this tick.

    Dispatches to the published behaviour tree for the squad's doctrine.
    Trees are authored in the visual editor, validated by the publish gate,
    and loaded here only after their manifest checksum verifies.

    When `Tracer.enable()` has been called (typically via the simulator's
    `--trace-ai` flag), one line per decision is printed to stdout.
    """
    from ai_tree.context import Context
    from ai_tree.trace import Tracer, format_extract
    ctx = Context(loot=loot, perception=perception)
    result = _get_extraction_tree(doctrine).tick(ctx)
    if Tracer.enabled:
        Tracer.emit(format_extract(doctrine.value, result, loot, perception))
    return result


