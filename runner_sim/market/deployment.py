"""
Squad assignment — how runners on a company's roster get grouped into
squads and dispatched to zones each week.

Sticky-shell design: shells are chosen at recruitment and never change.
This module does NOT re-run choose_best_shell weekly — runners keep
whatever shell they were hired into.

Two modes:
  • Legacy (posture=None): sort by id, chunk by _CHUNK_TABLE, random zone
    shuffle. Used by calibration mode and any caller without posture data.
  • Posture-driven (posture=PostureState): doctrine-clustering composition
    (group runners by shell→doctrine so intended doctrines actually form),
    then greedy posture-driven zone matching. Defensive companies stack
    safe doctrines into safe zones; aggressive companies gamble GREEDY in
    Outpost. The matching matrix is in _DOCTRINE_ZONE_BASE.

Adaptive chunking — under-sized rosters are the visible cost of poor
financial management by the company AI:
  9 runners → 3+3+3 across all 3 zones (full deployment)
  8         → 3+3+2 across all 3 zones (one weak squad)
  7         → 3+2+2 across all 3 zones (two weak squads)
  6         → 3+3   across 2 zones (one zone skipped — choice is posture-driven)
"""

from __future__ import annotations
import random

from runner_sim.market.roster import CompanyRoster, STARTING_ROSTER_SIZE
from runner_sim.zone_sim.extraction_ai import Doctrine, SHELL_DOCTRINE
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


# Doctrine sort priority used to cluster runners by shell. Same order as
# squad_doctrine's tie-breaker so the resulting Squad's derived doctrine
# matches the cluster intent (a chunk of mostly-GREEDY shells produces
# a GREEDY squad).
_DOCTRINE_CLUSTER_ORDER = [
    Doctrine.GREEDY, Doctrine.BALANCED, Doctrine.CAUTIOUS, Doctrine.SUPPORT,
]
_DOCTRINE_RANK = {d: i for i, d in enumerate(_DOCTRINE_CLUSTER_ORDER)}


# (doctrine, zone) base payoff matrix. Values are RELATIVE; only ordering
# matters. GREEDY in Outpost peaks (high-variance gamble); CAUTIOUS in
# Perimeter peaks (safe extraction); BALANCED is broadly neutral.
_DOCTRINE_ZONE_BASE: dict[Doctrine, dict[str, float]] = {
    Doctrine.GREEDY:   {"Perimeter": 0.6, "Dire Marsh": 0.7, "Outpost": 0.9},
    Doctrine.CAUTIOUS: {"Perimeter": 0.8, "Dire Marsh": 0.6, "Outpost": 0.4},
    Doctrine.BALANCED: {"Perimeter": 0.7, "Dire Marsh": 0.7, "Outpost": 0.6},
    Doctrine.SUPPORT:  {"Perimeter": 0.6, "Dire Marsh": 0.7, "Outpost": 0.5},
}

# Per-zone safety bias. Multiplied by (negative) risk_appetite so defensive
# postures (risk < 0) gain in safe zones and lose in dangerous ones.
_ZONE_SAFETY: dict[str, float] = {
    "Perimeter":  +0.3,
    "Dire Marsh":  0.0,
    "Outpost":    -0.3,
}

# Small tiebreak jitter so neutral postures (0.0/0.0) still get week-to-week
# variety in zone matching — without it, every neutral company picks the
# same pairing every week.
_NEUTRAL_TIEBREAK_JITTER = 0.05


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
def _doctrine_sort_key(runner) -> tuple[int, int]:
    """Cluster runners by doctrine, breaking ties by id for determinism."""
    doctrine = SHELL_DOCTRINE.get(runner.current_shell, Doctrine.BALANCED)
    return (_DOCTRINE_RANK[doctrine], runner.id)


def _match_score(doctrine: Doctrine, zone_name: str, posture) -> float:
    """Score a (doctrine, zone) pair given the company's posture.

    base[doctrine][zone]          — intrinsic fit (e.g. GREEDY peaks in Outpost)
    − risk_appetite × safety[zone] — defensive postures gain in safe zones
    + 0.1 × momentum × safety[zone] × −1
                                  — hot streaks tilt slightly toward gambles too
    """
    base = _DOCTRINE_ZONE_BASE.get(doctrine, {}).get(zone_name, 0.5)
    safety = _ZONE_SAFETY.get(zone_name, 0.0)
    posture_bias = -posture.risk_appetite * safety
    momentum_kicker = 0.1 * posture.momentum * safety * -1
    return base + posture_bias + momentum_kicker


def assign_squads(
    roster: CompanyRoster,
    zones: list[Zone],
    posture=None,
    rng: random.Random | None = None,
) -> dict[str, Squad]:
    """Group runners into squads and assign one per zone.

    Returns: {zone_name: Squad} — may have 2 or 3 entries depending on roster size.

    Two modes:
      • posture=None — legacy: id-sort + random zone shuffle. Used by
        calibration mode (which has no Company objects).
      • posture=PostureState — doctrine-clustering composition + greedy
        posture-driven zone matching. The intended doctrine actually
        forms; defensive companies hold Perimeter, aggressive ones
        gamble GREEDY in Outpost.

    Precondition: MIN_ROSTER_FOR_DEPLOYMENT <= len(roster.runners) <= 9
                  and len(zones) == 3.
    """
    n = len(roster.runners)
    if not (MIN_ROSTER_FOR_DEPLOYMENT <= n <= STARTING_ROSTER_SIZE):
        raise ValueError(
            f"Roster '{roster.company_name}' has {n} runners; "
            f"expected between {MIN_ROSTER_FOR_DEPLOYMENT} and {STARTING_ROSTER_SIZE}"
        )
    if len(zones) != 3:
        raise ValueError(f"Expected 3 zones, got {len(zones)}")

    rng = rng or random

    if posture is None:
        # --- Legacy path: id-sort + random shuffle ---
        sorted_runners = sorted(roster.runners, key=lambda r: r.id)
    else:
        # --- Posture path: cluster by doctrine so chunks form intended doctrines ---
        sorted_runners = sorted(roster.runners, key=_doctrine_sort_key)

    chunk_sizes = _CHUNK_TABLE[n]
    chunks: list[list] = []
    cursor = 0
    for size in chunk_sizes:
        chunks.append(sorted_runners[cursor:cursor + size])
        cursor += size

    if posture is None:
        # Legacy zone assignment — random shuffle, zip to chunks.
        shuffled_zones = list(zones)
        rng.shuffle(shuffled_zones)
        out: dict[str, Squad] = {}
        for zone, runners in zip(shuffled_zones, chunks):
            squad_name = _squad_name(roster.company_name, zone.name)
            out[zone.name] = make_squad(squad_name, runners)
        return out

    # --- Posture path: compute each chunk's doctrine from its shell mix,
    # then greedily match each chunk to its highest-scoring zone.
    # Only construct Squads at the end, with their final zone-aware names.
    from runner_sim.zone_sim.extraction_ai import squad_doctrine
    chunk_doctrines = [
        squad_doctrine([r.current_shell for r in runners])
        for runners in chunks
    ]

    available_zone_names = [z.name for z in zones]

    # Build a score table: (chunk_idx, zone_name) → score with neutral jitter.
    score_table: dict[tuple[int, str], float] = {}
    for i, doctrine in enumerate(chunk_doctrines):
        for zone_name in available_zone_names:
            score = _match_score(doctrine, zone_name, posture)
            score += rng.uniform(0, _NEUTRAL_TIEBREAK_JITTER)
            score_table[(i, zone_name)] = score

    # Greedy assignment: pick the highest-scoring (chunk, zone), assign,
    # remove both from contention, repeat until all chunks placed.
    # Under-strength rosters (fewer chunks than zones) naturally leave
    # the lowest-fit zone unfielded.
    assigned_chunks: set[int] = set()
    remaining_zones = set(available_zone_names)
    out: dict[str, Squad] = {}
    while len(assigned_chunks) < len(chunks):
        best_pair: tuple[int, str] | None = None
        best_score = float("-inf")
        for i in range(len(chunks)):
            if i in assigned_chunks:
                continue
            for zone_name in remaining_zones:
                s = score_table[(i, zone_name)]
                if s > best_score:
                    best_score = s
                    best_pair = (i, zone_name)
        assert best_pair is not None
        i, zone_name = best_pair
        squad_name = _squad_name(roster.company_name, zone_name)
        out[zone_name] = make_squad(squad_name, chunks[i])
        assigned_chunks.add(i)
        remaining_zones.discard(zone_name)

    return out
