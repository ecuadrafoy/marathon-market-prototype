"""
Squad assignment — how runners on a company's roster get grouped into
squads and dispatched to zones each week.

Sticky-shell design: shells are chosen at recruitment and never change.
This module does NOT re-run choose_best_shell weekly — runners keep
whatever shell they were hired into.

Strategy (intentionally simple for v1):
  1. Sort 9 runners by id (deterministic).
  2. Chunk into three squads of 3 (squads[0..2]).
  3. Random-shuffle [Sector 7, Deep Reach, The Shelf] and assign one squad per zone.

Player-controlled deployment is a future hook — assign_squads is the
single override point.
"""

from __future__ import annotations
import itertools
import random

from runner_sim.market.roster import CompanyRoster, STARTING_ROSTER_SIZE
from runner_sim.zone_sim.zones import Zone
from runner_sim.zone_sim.sim import Squad, make_squad


# ---------------------------------------------------------------------------
# SQUAD NAMING
# ---------------------------------------------------------------------------
# Two NATO words combined to give 12+ unique squad names per week
# (4 companies × 3 zones = 12 squads). Companies are also encoded so the
# squad name disambiguates which company it belongs to in shared logs.
def _squad_name(company_name: str, zone_name: str) -> str:
    """e.g. 'CyberAcme/S7' — short, unique per (company, zone) pair."""
    abbrev = "".join(w[0] for w in zone_name.split())  # 'Sector 7' -> 'S7'
    return f"{company_name}/{abbrev}"


# ---------------------------------------------------------------------------
# SQUAD ASSIGNMENT
# ---------------------------------------------------------------------------
def assign_squads(roster: CompanyRoster, zones: list[Zone]) -> dict[str, Squad]:
    """Group a company's 9 runners into 3 squads and assign one to each zone.

    Returns: {zone_name: Squad}, exactly 3 entries (one per zone).

    Deterministic chunking: runners sorted by id. Random zone assignment
    so squad-zone pairing varies week-to-week even with stable rosters.

    Precondition: len(roster.runners) == STARTING_ROSTER_SIZE == 9 and
                  len(zones) == 3. The integration is wired around fixed
                  squad size 3 — relax this in a future revision.
    """
    if len(roster.runners) != STARTING_ROSTER_SIZE:
        raise ValueError(
            f"Roster '{roster.company_name}' has {len(roster.runners)} runners; "
            f"expected exactly {STARTING_ROSTER_SIZE}"
        )
    if len(zones) != 3:
        raise ValueError(f"Expected 3 zones, got {len(zones)}")

    sorted_runners = sorted(roster.runners, key=lambda r: r.id)
    # Chunk into three squads of 3
    chunks = [sorted_runners[i*3:(i+1)*3] for i in range(3)]

    # Random zone assignment per company (independent shuffle)
    shuffled_zones = list(zones)
    random.shuffle(shuffled_zones)

    out: dict[str, Squad] = {}
    for zone, runners in zip(shuffled_zones, chunks):
        squad_name = _squad_name(roster.company_name, zone.name)
        out[zone.name] = make_squad(squad_name, runners)
    return out
