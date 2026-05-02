"""
Tests for runner_sim/market/deployment.py

Key invariants:
  - assign_squads produces exactly one squad per zone
  - Each squad has exactly 3 runners
  - Every runner appears in exactly one squad (no duplicates, no gaps)
  - Wrong roster size raises ValueError
"""

import pytest

from runner_sim.market.deployment import assign_squads
from runner_sim.market.roster import STARTING_ROSTER_SIZE, create_roster
from runner_sim.market.shell_market import make_initial_market
from runner_sim.zone_sim.zones import ZONES


class TestAssignSquads:
    def _make_roster(self):
        market = make_initial_market()
        used: set[str] = set()
        return create_roster("TestCo", market, used)

    def test_returns_one_squad_per_zone(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        assert set(result.keys()) == {z.name for z in ZONES}

    def test_each_squad_has_three_runners(self):
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        for zone_name, squad in result.items():
            assert len(squad.runners) == 3, f"Squad for {zone_name} has {len(squad.runners)} runners"

    def test_every_runner_deployed_exactly_once(self):
        """No runner left on bench; no runner deployed to two zones."""
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        deployed_ids = [r.id for squad in result.values() for r in squad.runners]
        roster_ids   = [r.id for r in roster.runners]
        assert sorted(deployed_ids) == sorted(roster_ids)

    def test_wrong_roster_size_raises(self):
        roster = self._make_roster()
        roster.runners = roster.runners[:6]   # only 6 runners — should fail
        with pytest.raises(ValueError, match="expected exactly"):
            assign_squads(roster, ZONES)

    def test_wrong_zone_count_raises(self):
        roster = self._make_roster()
        with pytest.raises(ValueError, match="Expected 3 zones"):
            assign_squads(roster, ZONES[:2])

    def test_squad_names_contain_company_name(self):
        """Squad names encode the company for disambiguation in shared logs."""
        roster = self._make_roster()
        result = assign_squads(roster, ZONES)
        for squad in result.values():
            assert "TestCo" in squad.name
