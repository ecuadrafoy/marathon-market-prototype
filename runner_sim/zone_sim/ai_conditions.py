"""Game-specific leaf conditions for the behaviour-tree AI.

Each function is a pure check over fields on the Context object. Importing
this module is what populates the global REGISTRY with the leaves that the
extraction and encounter trees compose. The catalog generator imports it
to build Groot's palette; the runtime imports it before loading any tree.

Context shape (depending on tree kind):
- extraction trees: ctx.loot, ctx.perception
- encounter trees: ctx.loot, ctx.own_combat, ctx.opponent_combat_estimate

The `loot` field is unified across both kinds (a squad only has its own loot
in scope) so the Carrying* / HasUncommonLoot leaves work in either tree.

A leaf only reads what it needs — its `requires` field documents this so the
publish gate can validate that the right context fields are present.
"""

from __future__ import annotations

from ai_tree.registry import ParamSpec, bt_condition

from .extraction_ai import Tier


# ===========================================================================
# EXTRACTION leaves — read ctx.perception and ctx.loot
# ===========================================================================

@bt_condition(
    name="IsFinalTick",
    category="Extraction.Time",
    description="True on the last tick of the run — everyone must extract.",
    requires=["perception"],
)
def is_final_tick(ctx) -> bool:
    return ctx.perception.tick >= ctx.perception.max_ticks


@bt_condition(
    name="ZoneFeelsDry",
    category="Extraction.Perception",
    description="True if the squad hasn't found anything for several ticks.",
    requires=["perception"],
)
def zone_feels_dry(ctx) -> bool:
    return ctx.perception.zone_feels_dry()


@bt_condition(
    name="CarryingNothing",
    category="Extraction.Loot",
    description="True if the squad has no items at all.",
    requires=["loot"],
)
def carrying_nothing(ctx) -> bool:
    return not ctx.loot.items


@bt_condition(
    name="CarryingAnything",
    category="Extraction.Loot",
    description="True if the squad has at least one item of any tier.",
    requires=["loot"],
)
def carrying_anything(ctx) -> bool:
    return bool(ctx.loot.items)


@bt_condition(
    name="HasUncommonLoot",
    category="Extraction.Loot",
    description="True if the squad's best item is Uncommon or higher.",
    requires=["loot"],
)
def has_uncommon_loot(ctx) -> bool:
    best = ctx.loot.best_tier()
    return best is not None and best >= Tier.UNCOMMON


@bt_condition(
    name="HadEncounter",
    category="Extraction.Perception",
    description="True if the squad has crossed paths with another squad this run.",
    requires=["perception"],
)
def had_encounter(ctx) -> bool:
    return ctx.perception.had_encounter_this_run


@bt_condition(
    name="TookDamage",
    category="Extraction.Perception",
    description="True if the squad took hits in combat this run.",
    requires=["perception"],
)
def took_damage(ctx) -> bool:
    return ctx.perception.took_damage_this_run


@bt_condition(
    name="TimePressureAbove",
    category="Extraction.Time",
    description="True if the run has elapsed past the given fraction (0.0 to 1.0).",
    requires=["perception"],
    params=[ParamSpec(
        name="threshold",
        type=float,
        default=0.75,
        description="Fraction of run elapsed (0.0–1.0). Example: 0.75 = past three-quarters.",
    )],
)
def time_pressure_above(ctx, threshold: float = 0.75) -> bool:
    return ctx.perception.time_pressure() > threshold


# ===========================================================================
# ENCOUNTER leaves — read ctx.own_combat, ctx.opponent_combat_estimate, ctx.own_loot
# ===========================================================================

@bt_condition(
    name="OpponentHelpless",
    category="Encounter.Combat",
    description="True if the opponent has effectively no combat strength left.",
    requires=["opponent_combat_estimate"],
)
def opponent_helpless(ctx) -> bool:
    return ctx.opponent_combat_estimate <= 0.001


@bt_condition(
    name="CombatRatioAbove",
    category="Encounter.Combat",
    description=(
        "True if (own_combat / opponent_combat_estimate) is at least the threshold. "
        "Treats a near-zero opponent as infinite ratio (True)."
    ),
    requires=["own_combat", "opponent_combat_estimate"],
    params=[ParamSpec(
        name="threshold",
        type=float,
        default=1.0,
        description="Minimum favourable combat ratio. 1.0 means even odds; values above 1.0 require the squad to outgun the opponent.",
    )],
)
def combat_ratio_above(ctx, threshold: float = 1.0) -> bool:
    if ctx.opponent_combat_estimate <= 0.001:
        return True
    return (ctx.own_combat / ctx.opponent_combat_estimate) >= threshold


@bt_condition(
    name="CarryingHighValue",
    category="Encounter.Loot",
    description="True if the squad's best item is Rare or higher (worth protecting).",
    requires=["loot"],
)
def carrying_high_value(ctx) -> bool:
    best = ctx.loot.best_tier()
    return best is not None and best >= Tier.RARE
