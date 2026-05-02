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

Zone data lives in data/zones.csv. Adding a row there is all that's needed
to introduce a new zone — no code changes required. The only coupling is
that items.csv must have a matching weight column for each zone name
(derived automatically via Zone.csv_column).
"""

from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "zones.csv"


@dataclass(frozen=True)
class Zone:
    name: str
    difficulty: float    # 0.0 - 1.0; lowers exploration success probability
    pool_size: int       # number of items spawned in this zone per week
    monitored: bool = False   # True for the player-visible zone (Sector 7) — provides intel

    @property
    def csv_column(self) -> str:
        """The items.csv column header that holds this zone's drop weights.

        'Sector 7' → 'sector_7_weight', 'The Shelf' → 'the_shelf_weight'
        """
        return self.name.lower().replace(" ", "_") + "_weight"


def load_zones(csv_path: str | Path | None = None) -> list[Zone]:
    """Parse zones.csv → list[Zone].

    Rows are returned in CSV order, which determines zone ordering throughout
    the simulation (distribution, display, pool spawning). Edit zones.csv to
    add, remove, or reorder zones — no code changes required.

    The `monitored` column is optional (defaults to False) for backwards
    compatibility with older zones.csv files.
    """
    path = Path(csv_path) if csv_path is not None else _DEFAULT_PATH
    zones: list[Zone] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            zones.append(Zone(
                name=row["name"],
                difficulty=float(row["difficulty"]),
                pool_size=int(row["pool_size"]),
                monitored=bool(int(row.get("monitored", "0"))),
            ))
    return zones


ZONES: list[Zone] = load_zones()
ZONE_BY_NAME: dict[str, Zone] = {z.name: z for z in ZONES}
