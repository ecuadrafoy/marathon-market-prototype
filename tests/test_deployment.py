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
