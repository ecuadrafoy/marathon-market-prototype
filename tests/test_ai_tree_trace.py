"""Tests for ai_tree/trace.py and the dispatcher instrumentation.

Two layers of coverage:

- Unit tests on the Tracer class (toggle, emit-when-enabled, no-op when disabled)
  and the formatter functions (presence of expected fields in their output).

- Integration tests calling should_extract / should_engage with the tracer
  enabled, using capsys to confirm the dispatchers actually emit.
"""

from __future__ import annotations

import pytest

from ai_tree.trace import Tracer, format_engage, format_extract
from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves
from runner_sim.zone_sim.encounter_ai import should_engage
from runner_sim.zone_sim.extraction_ai import (
    Doctrine,
    Item,
    SquadLoot,
    SquadPerception,
    Tier,
    should_extract,
)


# ---------------------------------------------------------------------------
# Fixture: ensure each test starts with the tracer disabled and ends the same
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_tracer():
    Tracer.disable()
    yield
    Tracer.disable()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _perception(**kwargs) -> SquadPerception:
    defaults = dict(
        ticks_since_last_find=0,
        had_encounter_this_run=False,
        took_damage_this_run=False,
        tick=5,
        max_ticks=10,
    )
    defaults.update(kwargs)
    return SquadPerception(**defaults)


def _item(tier: Tier) -> Item:
    return Item(name=f"i_{tier.name}", tier=tier, credit_value=1, zone_weights={})


# ---------------------------------------------------------------------------
# Tracer toggle
# ---------------------------------------------------------------------------
class TestTracerToggle:
    def test_disabled_by_default(self):
        assert Tracer.enabled is False

    def test_enable_then_disable(self):
        Tracer.enable()
        assert Tracer.enabled is True
        Tracer.disable()
        assert Tracer.enabled is False

    def test_emit_when_disabled_prints_nothing(self, capsys):
        Tracer.disable()
        Tracer.emit("[bt] should not appear")
        out = capsys.readouterr().out
        assert out == ""

    def test_emit_when_enabled_prints_the_line(self, capsys):
        Tracer.enable()
        Tracer.emit("[bt] hello")
        out = capsys.readouterr().out
        assert "[bt] hello" in out


# ---------------------------------------------------------------------------
# Formatters — verify the expected fields appear in the output
# ---------------------------------------------------------------------------
class TestFormatExtract:
    def test_includes_doctrine_decision_and_tick(self):
        line = format_extract(
            "balanced", True,
            SquadLoot(items=[_item(Tier.RARE)]),
            _perception(tick=7, max_ticks=10),
        )
        assert "extract_balanced" in line
        assert "YES" in line
        assert "T 7/10" in line   # tick formatting

    def test_loot_summary_includes_count_and_best_tier(self):
        line = format_extract(
            "cautious", False,
            SquadLoot(items=[_item(Tier.UNCOMMON), _item(Tier.RARE)]),
            _perception(),
        )
        # 2 items, best is RARE
        assert "loot= 2(RARE" in line
        assert "NO" in line

    def test_empty_loot_renders_dash(self):
        line = format_extract(
            "greedy", False, SquadLoot(items=[]), _perception(),
        )
        assert "loot= 0(—" in line


class TestFormatEngage:
    def test_includes_doctrine_decision_and_ratio(self):
        line = format_engage(
            "balanced", True, 5.0, 4.0, SquadLoot(items=[]),
        )
        assert "engage_balanced" in line
        assert "YES" in line
        assert "ratio=1.25" in line
        assert "loot=—" in line

    def test_high_value_loot_is_named(self):
        line = format_engage(
            "cautious", False, 1.0, 5.0,
            SquadLoot(items=[_item(Tier.RARE)]),
        )
        assert "loot=RARE" in line


# ---------------------------------------------------------------------------
# Integration: dispatchers actually emit when tracer is enabled
# ---------------------------------------------------------------------------
class TestDispatcherIntegration:
    def test_should_extract_emits_when_enabled(self, capsys):
        Tracer.enable()
        should_extract(
            Doctrine.BALANCED,
            SquadLoot(items=[_item(Tier.UNCOMMON)]),
            _perception(tick=3, had_encounter_this_run=True),
        )
        out = capsys.readouterr().out
        assert "extract_balanced" in out
        assert "T 3/10" in out
        # The extraction tree should return YES because HasUncommonLoot fires
        assert "YES" in out

    def test_should_extract_silent_when_disabled(self, capsys):
        # Tracer is disabled by the autouse fixture
        should_extract(
            Doctrine.BALANCED,
            SquadLoot(items=[]),
            _perception(),
        )
        assert capsys.readouterr().out == ""

    def test_should_engage_emits_when_enabled(self, capsys):
        Tracer.enable()
        should_engage(
            Doctrine.GREEDY,
            own_combat=10.0,
            opponent_combat_estimate=5.0,
            own_loot=SquadLoot(items=[]),
        )
        out = capsys.readouterr().out
        assert "engage_greedy" in out
        assert "ratio=2.00" in out
        assert "YES" in out   # GREEDY engages at ratio >= 0.5

    def test_should_engage_silent_when_disabled(self, capsys):
        should_engage(
            Doctrine.CAUTIOUS,
            own_combat=1.0,
            opponent_combat_estimate=5.0,
            own_loot=SquadLoot(items=[]),
        )
        assert capsys.readouterr().out == ""

    def test_dispatch_result_unchanged_by_tracer(self):
        """Enabling the tracer must not change decision outcomes — it's
        purely an output side-effect."""
        loot = SquadLoot(items=[_item(Tier.EPIC)])
        perc = _perception(tick=4, had_encounter_this_run=True)

        Tracer.disable()
        without_trace = should_extract(Doctrine.BALANCED, loot, perc)

        Tracer.enable()
        with_trace = should_extract(Doctrine.BALANCED, loot, perc)

        assert without_trace == with_trace
