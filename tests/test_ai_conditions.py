"""Tests for runner_sim/zone_sim/ai_conditions.py — game-specific BT leaves.

Each leaf is a pure check over a Context. Verify both branches of every
leaf so that the publish gate's snapshot tests build on solid foundations.
"""

import pytest

from ai_tree.context import Context
from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves
from ai_tree.registry import get
from runner_sim.zone_sim.extraction_ai import (
    SquadLoot,
    SquadPerception,
    Tier,
)
from runner_sim.zone_sim.extraction_ai import Item


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _perception(
    *,
    tick: int = 0,
    max_ticks: int = 10,
    ticks_since_last_find: int = 0,
    had_encounter: bool = False,
    took_damage: bool = False,
) -> SquadPerception:
    return SquadPerception(
        ticks_since_last_find=ticks_since_last_find,
        had_encounter_this_run=had_encounter,
        took_damage_this_run=took_damage,
        tick=tick,
        max_ticks=max_ticks,
    )


def _item(tier: Tier) -> Item:
    return Item(name=f"item_{tier.name}", tier=tier, credit_value=1, zone_weights={})


# ---------------------------------------------------------------------------
# Extraction leaves
# ---------------------------------------------------------------------------
class TestIsFinalTick:
    def test_true_at_max_tick(self):
        ctx = Context(perception=_perception(tick=10, max_ticks=10))
        assert get("IsFinalTick").func(ctx) is True

    def test_false_before_max(self):
        ctx = Context(perception=_perception(tick=5, max_ticks=10))
        assert get("IsFinalTick").func(ctx) is False


class TestZoneFeelsDry:
    def test_dry_when_no_finds_for_threshold(self):
        ctx = Context(perception=_perception(ticks_since_last_find=3))
        assert get("ZoneFeelsDry").func(ctx) is True

    def test_not_dry_recently_found(self):
        ctx = Context(perception=_perception(ticks_since_last_find=1))
        assert get("ZoneFeelsDry").func(ctx) is False


class TestCarryingNothingAndAnything:
    def test_nothing_when_loot_empty(self):
        ctx = Context(loot=SquadLoot(items=[]))
        assert get("CarryingNothing").func(ctx) is True
        assert get("CarryingAnything").func(ctx) is False

    def test_anything_when_at_least_one_item(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.COMMON)]))
        assert get("CarryingNothing").func(ctx) is False
        assert get("CarryingAnything").func(ctx) is True


class TestHasUncommonLoot:
    def test_false_when_only_common(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.COMMON)]))
        assert get("HasUncommonLoot").func(ctx) is False

    def test_true_with_uncommon(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.UNCOMMON)]))
        assert get("HasUncommonLoot").func(ctx) is True

    def test_true_with_higher_tier(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.EPIC)]))
        assert get("HasUncommonLoot").func(ctx) is True

    def test_false_when_empty(self):
        ctx = Context(loot=SquadLoot(items=[]))
        assert get("HasUncommonLoot").func(ctx) is False


class TestEncounterAndDamageFlags:
    def test_had_encounter(self):
        ctx_yes = Context(perception=_perception(had_encounter=True))
        ctx_no = Context(perception=_perception(had_encounter=False))
        assert get("HadEncounter").func(ctx_yes) is True
        assert get("HadEncounter").func(ctx_no) is False

    def test_took_damage(self):
        ctx_yes = Context(perception=_perception(took_damage=True))
        ctx_no = Context(perception=_perception(took_damage=False))
        assert get("TookDamage").func(ctx_yes) is True
        assert get("TookDamage").func(ctx_no) is False


class TestTimePressureAbove:
    def test_strict_inequality(self):
        # tick=8/max=10 → time_pressure = 0.8
        ctx = Context(perception=_perception(tick=8, max_ticks=10))
        func = get("TimePressureAbove").func
        assert func(ctx, threshold=0.75) is True
        # exactly equal must be False (strict >)
        assert func(ctx, threshold=0.8) is False
        assert func(ctx, threshold=0.9) is False

    def test_uses_default_threshold(self):
        ctx = Context(perception=_perception(tick=8, max_ticks=10))
        # default is 0.75, time_pressure = 0.8 → True
        assert get("TimePressureAbove").func(ctx) is True


# ---------------------------------------------------------------------------
# Encounter leaves
# ---------------------------------------------------------------------------
class TestOpponentHelpless:
    def test_true_at_zero(self):
        ctx = Context(opponent_combat_estimate=0.0)
        assert get("OpponentHelpless").func(ctx) is True

    def test_true_just_below_threshold(self):
        ctx = Context(opponent_combat_estimate=0.0005)
        assert get("OpponentHelpless").func(ctx) is True

    def test_false_above_threshold(self):
        ctx = Context(opponent_combat_estimate=0.5)
        assert get("OpponentHelpless").func(ctx) is False


class TestCombatRatioAbove:
    def test_clear_advantage(self):
        ctx = Context(own_combat=10.0, opponent_combat_estimate=2.0)
        assert get("CombatRatioAbove").func(ctx, threshold=2.0) is True

    def test_clear_disadvantage(self):
        ctx = Context(own_combat=2.0, opponent_combat_estimate=10.0)
        assert get("CombatRatioAbove").func(ctx, threshold=1.0) is False

    def test_helpless_opponent_treated_as_infinite_ratio(self):
        ctx = Context(own_combat=1.0, opponent_combat_estimate=0.0)
        assert get("CombatRatioAbove").func(ctx, threshold=99.0) is True


class TestCarryingHighValue:
    def test_false_uncommon_only(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.UNCOMMON)]))
        assert get("CarryingHighValue").func(ctx) is False

    def test_true_rare(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.RARE)]))
        assert get("CarryingHighValue").func(ctx) is True

    def test_true_epic(self):
        ctx = Context(loot=SquadLoot(items=[_item(Tier.EPIC)]))
        assert get("CarryingHighValue").func(ctx) is True

    def test_false_empty(self):
        ctx = Context(loot=SquadLoot(items=[]))
        assert get("CarryingHighValue").func(ctx) is False
