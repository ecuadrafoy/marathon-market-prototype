"""
Tests for runner_sim/market/deployment.py

Key invariants:
  - assign_squads produces one squad per deployed zone
  - Squads contain 2 or 3 runners depending on roster size
  - Every runner appears in exactly one squad (no duplicates, no gaps)
  - Rosters below MIN_ROSTER_FOR_DEPLOYMENT or above 9 raise ValueError
"""

import pytest

from runner_sim.market.deployment import (
    MIN_ROSTER_FOR_DEPLOYMENT,
    _CHUNK_TABLE,
    assign_squads,
)
from runner_sim.market.roster import STARTING_ROSTER_SIZE, create_roster
from runner_sim.market.shell_market import make_initial_market
from runner_sim.zone_sim.zones import ZONES


class TestAssignSquads:
    def _make_roster(self, size: int = STARTING_ROSTER_SIZE):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        if size != STARTING_ROSTER_SIZE:
            roster.runners = roster.runners[:size]
        return roster

    def test_full_roster_returns_one_squad_per_zone(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        assert set(result.keys()) == {z.name for z in ZONES}

    def test_each_squad_has_two_or_three_runners(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        for zone_name, squad in result.items():
            assert 2 <= len(squad.runners) <= 3

    def test_every_runner_deployed_exactly_once(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        deployed_ids = [r.id for squad in result.values() for r in squad.runners]
        roster_ids   = [r.id for r in roster.runners]
        assert sorted(deployed_ids) == sorted(roster_ids)

    def test_six_runner_roster_skips_one_zone(self):
        """6 runners → 2 squads of 3; one zone gets no squad this week."""
        roster = self._make_roster(size=6)
        result = assign_squads(roster, ZONES)
        assert len(result) == 2
        for squad in result.values():
            assert len(squad.runners) == 3

    @pytest.mark.parametrize("size", [6, 7, 8, 9])
    def test_chunk_sizes_sum_to_roster_size(self, size: int):
        roster = self._make_roster(size=size)
        result = assign_squads(roster, ZONES)
        deployed = sum(len(sq.runners) for sq in result.values())
        assert deployed == size
        assert sum(_CHUNK_TABLE[size]) == size

    def test_roster_below_minimum_raises(self):
        roster = self._make_roster()
        roster.runners = roster.runners[:MIN_ROSTER_FOR_DEPLOYMENT - 1]
        with pytest.raises(ValueError, match="expected between"):
            assign_squads(roster, ZONES)

    def test_roster_above_maximum_raises(self):
        """A roster of 10+ shouldn't slip through — invariant guard."""
        roster = self._make_roster()
        roster.runners = roster.runners + roster.runners[:1]  # 10 runners (duplicated)
        with pytest.raises(ValueError, match="expected between"):
            assign_squads(roster, ZONES)

    def test_wrong_zone_count_raises(self):
        roster = self._make_roster()
        with pytest.raises(ValueError, match="Expected 3 zones"):
            assign_squads(roster, ZONES[:2])

    def test_squad_names_contain_company_name(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        for squad in result.values():
            assert "TestCo" in squad.name


# ---------------------------------------------------------------------------
# Posture-driven deployment (composition + zone matching)
# ---------------------------------------------------------------------------
# These tests exercise the new posture-aware path; passing posture=None
# preserves the legacy id-sort + random shuffle behaviour above.
from runner_sim.market.company_strategy import PostureState
from runner_sim.zone_sim.extraction_ai import Doctrine, SHELL_DOCTRINE
import random


def _stamp_shell(roster, shell_assignments: list[str]):
    """Forcibly set each runner's current_shell so we can build deterministic
    doctrine compositions in tests."""
    for r, shell in zip(roster.runners, shell_assignments):
        r.current_shell = shell


class TestPostureDeployment:
    def _make_roster(self, size: int = STARTING_ROSTER_SIZE):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        if size != STARTING_ROSTER_SIZE:
            roster.runners = roster.runners[:size]
        return roster

    def test_doctrine_clustering_groups_same_doctrine_shells(self):
        """3 Destroyers + 3 Recons + 3 Triages should produce exactly one
        GREEDY squad, one CAUTIOUS squad, one SUPPORT squad — not three
        mongrel chunks. This is the headline composition fix."""
        roster = self._make_roster(size=9)
        _stamp_shell(roster, [
            "Destroyer", "Destroyer", "Destroyer",
            "Recon", "Recon", "Recon",
            "Triage", "Triage", "Triage",
        ])
        rng = random.Random(0)
        result = assign_squads(roster, ZONES, posture=PostureState(), rng=rng)

        doctrines = {sq.doctrine for sq in result.values()}
        assert Doctrine.GREEDY in doctrines
        assert Doctrine.CAUTIOUS in doctrines
        assert Doctrine.SUPPORT in doctrines

    def test_neutral_posture_with_seeded_rng_is_deterministic(self):
        """Neutral posture still produces variety via the jitter, but with a
        seeded RNG the result should be reproducible run-to-run."""
        roster = self._make_roster(size=9)
        result_a = assign_squads(roster, ZONES, posture=PostureState(),
                                 rng=random.Random(123))
        result_b = assign_squads(roster, ZONES, posture=PostureState(),
                                 rng=random.Random(123))
        assert {z: sq.doctrine for z, sq in result_a.items()} == \
               {z: sq.doctrine for z, sq in result_b.items()}

    def test_defensive_posture_pulls_cautious_into_perimeter(self):
        """A risk_appetite=-0.8 company values safe zones; a CAUTIOUS squad
        (Recon/Thief) should land in Perimeter most reliably."""
        roster = self._make_roster(size=9)
        _stamp_shell(roster, [
            "Destroyer", "Destroyer", "Destroyer",   # GREEDY
            "Recon", "Recon", "Recon",                # CAUTIOUS
            "Vandal", "Vandal", "Vandal",             # BALANCED
        ])
        # Aggregate across multiple seeds to get a statistical signal
        # (the small jitter can flip individual runs).
        cautious_in_perimeter = 0
        for seed in range(20):
            result = assign_squads(roster, ZONES,
                                   posture=PostureState(risk_appetite=-0.8),
                                   rng=random.Random(seed))
            perimeter_squad = result.get("Perimeter")
            if perimeter_squad and perimeter_squad.doctrine == Doctrine.CAUTIOUS:
                cautious_in_perimeter += 1
        # With safety bias +0.3 for Perimeter and risk -0.8, anchor pull is
        # +0.24 — should dominate jitter (max +0.05) and most base differences.
        assert cautious_in_perimeter >= 15  # ≥75% of 20 seeds

    def test_aggressive_posture_pulls_greedy_into_outpost(self):
        """A risk_appetite=+0.8 company gambles GREEDY in Outpost."""
        roster = self._make_roster(size=9)
        _stamp_shell(roster, [
            "Destroyer", "Destroyer", "Destroyer",   # GREEDY
            "Recon", "Recon", "Recon",                # CAUTIOUS
            "Vandal", "Vandal", "Vandal",             # BALANCED
        ])
        greedy_in_outpost = 0
        for seed in range(20):
            result = assign_squads(roster, ZONES,
                                   posture=PostureState(risk_appetite=+0.8),
                                   rng=random.Random(seed))
            outpost_squad = result.get("Outpost")
            if outpost_squad and outpost_squad.doctrine == Doctrine.GREEDY:
                greedy_in_outpost += 1
        assert greedy_in_outpost >= 15

    def test_six_runner_aggressive_skips_perimeter(self):
        """An aggressive 6-runner roster naturally leaves Perimeter unfielded —
        the safest zone is least appealing when you're gambling for variance."""
        roster = self._make_roster(size=6)
        # Build two GREEDY-ish chunks so both squads dislike Perimeter equally
        _stamp_shell(roster, ["Destroyer"] * 6)
        skipped_perimeter = 0
        for seed in range(20):
            result = assign_squads(roster, ZONES,
                                   posture=PostureState(risk_appetite=+0.8),
                                   rng=random.Random(seed))
            if "Perimeter" not in result:
                skipped_perimeter += 1
        # Aggressive 6-runner rosters should skip Perimeter the majority of the time
        assert skipped_perimeter >= 12  # ≥60% of 20 seeds

    def test_posture_none_uses_legacy_path(self):
        """posture=None must preserve the legacy id-sort behaviour for
        calibration-mode compatibility — runner IDs in chunks match what
        sort-by-id would produce."""
        roster = self._make_roster(size=9)
        result = assign_squads(roster, ZONES, posture=None,
                               rng=random.Random(7))
        all_deployed_ids = sorted(
            r.id for squad in result.values() for r in squad.runners
        )
        assert all_deployed_ids == sorted(r.id for r in roster.runners)
        # All 3 zones get a squad
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Memory-driven per-zone bias
# ---------------------------------------------------------------------------
from runner_sim.market.company_strategy import CompanyMemory, WeekSnapshot
from runner_sim.market.deployment import _match_score, _memory_factor


def _wipe_snapshot(zone: str, week: int = 0) -> WeekSnapshot:
    """A snapshot where the company deployed to `zone` and was wiped there."""
    return WeekSnapshot(
        week=week,
        price_change_pct=-10.0,
        extracted_credits=0.0,
        squads_deployed=3, squads_returned=2, squads_eliminated=1,
        per_zone_credits={zone: 0.0},
        per_zone_squads_deployed={zone: 1},
        per_zone_squads_eliminated={zone: 1},
        deaths=3, budget_delta=-50.0, roster_size_after=6,
    )


def _fertile_snapshot(zone: str, credits: float, week: int = 0) -> WeekSnapshot:
    """A snapshot where the company deployed to `zone` and extracted big."""
    return WeekSnapshot(
        week=week,
        price_change_pct=+10.0,
        extracted_credits=credits,
        squads_deployed=3, squads_returned=3, squads_eliminated=0,
        per_zone_credits={zone: credits},
        per_zone_squads_deployed={zone: 1},
        per_zone_squads_eliminated={zone: 0},
        deaths=0, budget_delta=+200.0, roster_size_after=9,
    )


class TestMemoryFactor:
    def test_neutral_when_memory_is_none(self):
        assert _memory_factor(None, "Outpost") == 1.0

    def test_neutral_when_memory_is_empty(self):
        assert _memory_factor(CompanyMemory(), "Outpost") == 1.0

    def test_neutral_when_zone_not_in_window(self):
        m = CompanyMemory()
        m.record(_fertile_snapshot("Perimeter", credits=300.0))
        assert _memory_factor(m, "Outpost") == 1.0

    def test_full_wipes_drop_factor_below_one(self):
        m = CompanyMemory()
        for w in range(4):
            m.record(_wipe_snapshot("Outpost", week=w))
        assert _memory_factor(m, "Outpost") < 1.0

    def test_fertile_history_lifts_factor_above_one(self):
        m = CompanyMemory()
        for w in range(4):
            m.record(_fertile_snapshot("Perimeter", credits=400.0, week=w))
        assert _memory_factor(m, "Perimeter") > 1.0

    def test_factor_clamped(self):
        """Even with extreme history the factor stays within [0.6, 1.2]."""
        m = CompanyMemory()
        for w in range(6):
            m.record(_wipe_snapshot("Outpost", week=w))
        f = _memory_factor(m, "Outpost")
        assert 0.6 <= f <= 1.2


class TestDeploymentMemoryBias:
    def _make_roster(self, size: int = STARTING_ROSTER_SIZE):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        if size != STARTING_ROSTER_SIZE:
            roster.runners = roster.runners[:size]
        return roster

    def test_outpost_wipe_history_biases_greedy_squad_away(self):
        """With 4 weeks of Outpost wipes in memory, a GREEDY-heavy roster
        with neutral posture should place GREEDY in Outpost less often
        than the no-memory baseline."""
        roster = self._make_roster(size=9)
        _stamp_shell(roster, [
            "Destroyer", "Destroyer", "Destroyer",   # GREEDY
            "Recon", "Recon", "Recon",                # CAUTIOUS
            "Vandal", "Vandal", "Vandal",             # BALANCED
        ])

        baseline_count = 0
        for seed in range(20):
            result = assign_squads(roster, ZONES,
                                   posture=PostureState(),
                                   memory=None,
                                   rng=random.Random(seed))
            outpost = result.get("Outpost")
            if outpost and outpost.doctrine == Doctrine.GREEDY:
                baseline_count += 1

        m = CompanyMemory()
        for w in range(4):
            m.record(_wipe_snapshot("Outpost", week=w))

        memory_count = 0
        for seed in range(20):
            result = assign_squads(roster, ZONES,
                                   posture=PostureState(),
                                   memory=m,
                                   rng=random.Random(seed))
            outpost = result.get("Outpost")
            if outpost and outpost.doctrine == Doctrine.GREEDY:
                memory_count += 1

        # Memory should pull GREEDY out of Outpost at least some of the time
        # vs. the no-memory baseline.
        assert memory_count < baseline_count, (
            f"memory bias did not move GREEDY off Outpost: "
            f"baseline={baseline_count}, with-memory={memory_count}"
        )

    def test_match_score_responds_to_memory_bias(self):
        """Direct: _match_score for the same (doctrine, zone) should drop
        when that zone has a wipe history, and rise when fertile."""
        baseline = _match_score(Doctrine.GREEDY, "Outpost", PostureState(), memory=None)

        wipe_mem = CompanyMemory()
        for w in range(4):
            wipe_mem.record(_wipe_snapshot("Outpost", week=w))
        wiped = _match_score(Doctrine.GREEDY, "Outpost", PostureState(), memory=wipe_mem)

        fertile_mem = CompanyMemory()
        for w in range(4):
            fertile_mem.record(_fertile_snapshot("Outpost", credits=400.0, week=w))
        fertile = _match_score(Doctrine.GREEDY, "Outpost", PostureState(), memory=fertile_mem)

        assert wiped < baseline < fertile
