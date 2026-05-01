"""
Zone definitions for the tick-based simulation.

Standalone from marathon_market.ZONES — kept separate so the runner_sim
zone simulation does not depend on the market layer. Zone names match
marathon_market for cross-reference, but the column names in items.csv
are derived from these names by replacing spaces with underscores and
appending '_weight' (e.g. 'Sector 7' → 'sector_7_weight').

Pool size scales inversely with difficulty — harder zones have less stuff
in them, reinforcing the risk/reward tradeoff. Difficulty itself feeds
the exploration roll in sim.py: harder zones make discovery harder per
tick, so even a long-staying squad finds less in The Shelf than they
would in Sector 7.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    name: str
    difficulty: float    # 0.0 - 1.0; lowers exploration success probability
    pool_size: int       # number of items spawned in this zone per week

    @property
    def csv_column(self) -> str:
        """The items.csv column header that holds this zone's drop weights.

        'Sector 7' → 'sector_7_weight', 'The Shelf' → 'the_shelf_weight'
        """
        return self.name.lower().replace(" ", "_") + "_weight"


ZONES: list[Zone] = [
    Zone(name="Sector 7",   difficulty=0.1, pool_size=12),
    Zone(name="Deep Reach", difficulty=0.3, pool_size=8),
    Zone(name="The Shelf",  difficulty=0.5, pool_size=5),
]


ZONE_BY_NAME: dict[str, Zone] = {z.name: z for z in ZONES}
