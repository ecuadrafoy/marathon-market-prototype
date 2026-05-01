"""Zone simulation subpackage — tick-based concurrent zone runs with finite pools.

Standalone from the main runner_sim flow. Run with:
    uv run python -m runner_sim.zone_sim.harness --seed 42
"""

from .extraction_ai import (
    Doctrine,
    Item,
    SquadLoot,
    SquadPerception,
    Tier,
    ZoneState,
    should_extract,
    squad_doctrine,
)
from .zones import Zone, ZONES
from .items import load_items
from .encounter_ai import should_engage
from .sim import Squad, ZoneRunResult, run_zone, spawn_zone_pool

__all__ = [
    "Doctrine",
    "Item",
    "SquadLoot",
    "SquadPerception",
    "Tier",
    "ZoneState",
    "should_extract",
    "squad_doctrine",
    "Zone",
    "ZONES",
    "load_items",
    "should_engage",
    "Squad",
    "ZoneRunResult",
    "run_zone",
    "spawn_zone_pool",
]
