"""
Squad assignment — how runners on a company's roster get grouped into
squads and dispatched to zones each week.

Sticky-shell design: shells are chosen at recruitment and never change.
This module does NOT re-run choose_best_shell weekly — runners keep
whatever shell they were hired into.

Strategy (intentionally simple for v1):
  1. Sort runners by id (deterministic).
  2. Chunk into 2-3 squads of 2-3 runners depending on roster size.
  3. Random-shuffle the zones and assign one squad per zone (extras unassigned).

Adaptive chunking — under-sized rosters are the visible cost of poor
financial management by the company AI:
  9 runners → 3+3+3 across all 3 zones (full deployment)
  8         → 3+3+2 across all 3 zones (one weak squad)
  7         → 3+2+2 across all 3 zones (two weak squads)
  6         → 3+3   across 2 zones (one zone skipped)

Player-controlled deployment is a future hook — assign_squads is the
single override point.
"""

from __future__ import annotations
import random

from runner_sim.market.roster import CompanyRoster, STARTING_ROSTER_SIZE
from runner_sim.zone_sim.zones import Zone
from runner_sim.zone_sim.sim import Squad, make_squad


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
MIN_ROSTER_FOR_DEPLOYMENT = 6   # below this, the company cannot field any squads


# Roster size → squad chunk sizes (sum == roster size, len <= 3)
_CHUNK_TABLE: dict[int, tuple[int, ...]] = {
    6: (3, 3),
    7: (3, 2, 2),
    8: (3, 3, 2),
    9: (3, 3, 3),
}


# ---------------------------------------------------------------------------
# SQUAD NAMING
# ---------------------------------------------------------------------------
# Two NATO words combined to give 12+ unique squad names per week
# (4 companies × 3 zones = 12 squads). Companies are also encoded so the
# squad name disambiguates which company it belongs to in shared logs.
def _squad_name(company_name: str, zone_name: str) -> str:
    """e.g. 'CyberAcme/S7' — short, unique per (company, zone) pair."""
    abbrev = "".join(w[0] for w in zone_name.split())  # 'Dire Marsh' -> 'DM'
    return f"{company_name}/{abbrev}"


# ---------------------------------------------------------------------------
# SQUAD ASSIGNMENT
# ---------------------------------------------------------------------------
def assign_squads(roster: CompanyRoster, zones: list[Zone]) -> dict[str, Squad]:
    """Group runners into squads and assign one per zone.

    Returns: {zone_name: Squad} — may have 2 or 3 entries depending on roster size.

    Deterministic chunking: runners sorted by id. Random zone assignment
    so squad-zone pairing varies week-to-week even with stable rosters.

    Precondition: MIN_ROSTER_FOR_DEPLOYMENT <= len(roster.runners) <= 9
                  and len(zones) == 3. Smaller rosters than 6 cannot field
                  any squads — the company sits out the week.
    """
    n = len(roster.runners)
    if not (MIN_ROSTER_FOR_DEPLOYMENT <= n <= STARTING_ROSTER_SIZE):
        raise ValueError(
            f"Roster '{roster.company_name}' has {n} runners; "
            f"expected between {MIN_ROSTER_FOR_DEPLOYMENT} and {STARTING_ROSTER_SIZE}"
        )
    if len(zones) != 3:
        raise ValueError(f"Expected 3 zones, got {len(zones)}")

    sorted_runners = sorted(roster.runners, key=lambda r: r.id)
    chunk_sizes = _CHUNK_TABLE[n]
    chunks: list[list] = []
    cursor = 0
    for size in chunk_sizes:
        chunks.append(sorted_runners[cursor:cursor + size])
        cursor += size

    # Random zone assignment per company (independent shuffle); a roster of 6
    # produces 2 squads → only 2 of the 3 zones get assigned this week.
    shuffled_zones = list(zones)
    random.shuffle(shuffled_zones)

    out: dict[str, Squad] = {}
    for zone, runners in zip(shuffled_zones, chunks):
        squad_name = _squad_name(roster.company_name, zone.name)
        out[zone.name] = make_squad(squad_name, runners)
    return out
