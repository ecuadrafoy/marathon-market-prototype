"""
CSV loader for the item catalog.

Reads data/items.csv → list[Item]. The CSV format is column-per-zone, where each
zone's drop weight column is named after the zone (e.g. sector_7_weight). The
loader pivots that wide format into the dict-based zone_weights structure that
the Item dataclass uses.

Default path is resolved relative to the project root (the directory containing
this package). Pass an explicit path to override.
"""

from __future__ import annotations
import csv
from pathlib import Path

from .extraction_ai import Item, Tier
from .zones import ZONES


_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "items.csv"


def load_items(csv_path: str | Path | None = None) -> list[Item]:
    """Parse the items CSV into a list of Item objects.

    Each row produces one Item. Tier integers are mapped to the Tier enum.
    Per-zone weight columns are pivoted into the zone_weights dict, keyed by
    the canonical zone name (not the column name).

    Raises ValueError if any zone defined in zones.csv is missing its weight
    column from items.csv. This catches zone renames and additions early —
    before the sim runs with a silently empty pool.
    """
    path = Path(csv_path) if csv_path is not None else _DEFAULT_PATH
    items: list[Item] = []

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)

        # Validate that every zone has a weight column in this CSV.
        missing = [
            zone.csv_column for zone in ZONES
            if zone.csv_column not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(
                f"items.csv is missing weight columns for zone(s): {missing}\n"
                f"Expected columns derived from zones.csv: "
                f"{[z.csv_column for z in ZONES]}\n"
                f"Found columns: {reader.fieldnames}"
            )

        for row in reader:
            zone_weights = {
                zone.name: float(row[zone.csv_column])
                for zone in ZONES
            }
            items.append(Item(
                name=row["name"],
                tier=Tier(int(row["tier"])),
                credit_value=int(row["credit_value"]),
                zone_weights=zone_weights,
            ))

    return items
