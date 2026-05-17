"""
Tests for CompanyMemory — rolling buffer of WeekSnapshots that feeds
update_posture (smoothing) and deployment._memory_factor (per-zone bias).

Pure unit tests; no zone sim involved.
"""

from __future__ import annotations

import pytest

from runner_sim.market.company_strategy import (
    CompanyMemory,
    MEMORY_WINDOW_WEEKS,
    WeekSnapshot,
)


def _snap(
    week: int = 0,
    per_zone_credits: dict | None = None,
    per_zone_squads_deployed: dict | None = None,
    per_zone_squads_eliminated: dict | None = None,
) -> WeekSnapshot:
    return WeekSnapshot(
        week=week,
        price_change_pct=0.0,
        extracted_credits=0.0,
        squads_deployed=3,
        squads_returned=3,
        squads_eliminated=0,
        per_zone_credits=per_zone_credits or {},
        per_zone_squads_deployed=per_zone_squads_deployed or {},
        per_zone_squads_eliminated=per_zone_squads_eliminated or {},
        deaths=0,
        budget_delta=0.0,
        roster_size_after=9,
    )


class TestCompanyMemoryWindow:
    def test_empty_memory_returns_zero_signals(self):
        m = CompanyMemory()
        assert m.per_zone_elimination_rate("Outpost") == 0.0
        assert m.per_zone_avg_credits("Outpost") == 0.0

    def test_record_appends_within_window(self):
        m = CompanyMemory()
        for w in range(3):
            m.record(_snap(week=w))
        assert len(m.snapshots) == 3
        assert [s.week for s in m.snapshots] == [0, 1, 2]

    def test_window_caps_at_max(self):
        """Recording more than MEMORY_WINDOW_WEEKS drops the oldest entries."""
        m = CompanyMemory()
        for w in range(MEMORY_WINDOW_WEEKS + 4):
            m.record(_snap(week=w))
        assert len(m.snapshots) == MEMORY_WINDOW_WEEKS
        # The 4 oldest entries were dropped.
        assert m.snapshots[0].week == 4
        assert m.snapshots[-1].week == MEMORY_WINDOW_WEEKS + 3


class TestPerZoneEliminationRate:
    def test_zero_when_zone_never_visited(self):
        m = CompanyMemory()
        m.record(_snap(per_zone_squads_deployed={"Perimeter": 1}))
        assert m.per_zone_elimination_rate("Outpost") == 0.0

    def test_zero_when_all_squads_returned(self):
        m = CompanyMemory()
        for _ in range(3):
            m.record(_snap(
                per_zone_squads_deployed={"Outpost": 1},
                per_zone_squads_eliminated={"Outpost": 0},
            ))
        assert m.per_zone_elimination_rate("Outpost") == 0.0

    def test_one_when_all_squads_eliminated(self):
        m = CompanyMemory()
        for _ in range(3):
            m.record(_snap(
                per_zone_squads_deployed={"Outpost": 1},
                per_zone_squads_eliminated={"Outpost": 1},
            ))
        assert m.per_zone_elimination_rate("Outpost") == pytest.approx(1.0)

    def test_half_under_mixed_history(self):
        m = CompanyMemory()
        for i in range(4):
            eliminated = 1 if i % 2 == 0 else 0
            m.record(_snap(
                per_zone_squads_deployed={"Dire Marsh": 1},
                per_zone_squads_eliminated={"Dire Marsh": eliminated},
            ))
        assert m.per_zone_elimination_rate("Dire Marsh") == pytest.approx(0.5)


class TestPerZoneAvgCredits:
    def test_zero_when_zone_never_visited(self):
        m = CompanyMemory()
        m.record(_snap(
            per_zone_credits={"Perimeter": 200.0},
            per_zone_squads_deployed={"Perimeter": 1},
        ))
        assert m.per_zone_avg_credits("Outpost") == 0.0

    def test_averages_only_weeks_zone_was_deployed_to(self):
        """Weeks where the zone was skipped should NOT pull the average toward 0."""
        m = CompanyMemory()
        # Week 0: deployed to Outpost, got 300 credits.
        m.record(_snap(
            per_zone_credits={"Outpost": 300.0},
            per_zone_squads_deployed={"Outpost": 1},
        ))
        # Week 1: skipped Outpost entirely (under-strength roster).
        m.record(_snap(per_zone_squads_deployed={"Perimeter": 1}))
        # Week 2: deployed to Outpost, got 100 credits.
        m.record(_snap(
            per_zone_credits={"Outpost": 100.0},
            per_zone_squads_deployed={"Outpost": 1},
        ))
        # Average over the 2 weeks Outpost was deployed-to: (300 + 100) / 2 = 200.
        assert m.per_zone_avg_credits("Outpost") == pytest.approx(200.0)
