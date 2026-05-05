"""
Encounter AI — per-doctrine decision logic for engaging or disengaging in combat.

When two squads cross paths in the zone, each one independently decides whether
to engage or disengage. If both engage, combat resolves. If either disengages,
the encounter passes without a fight.

The squad assesses combat odds based on its own effective combat strength versus
a perceived estimate of the opponent's strength. The estimate is intentionally
imperfect — runners can read body language and squad composition, but they
don't see exact numbers. The tick loop computes the estimate from the opponent's
actual eff_combat sum with a small noise factor applied.

Carried loot value also factors in: a squad carrying valuable items is more
risk-averse — they have something to lose. A squad carrying nothing has less
to fear from a fight.
"""

from __future__ import annotations

from .extraction_ai import Doctrine, SquadLoot, Tier


_ENCOUNTER_TREES: dict[Doctrine, "Tree"] = {}


def _get_encounter_tree(doctrine: Doctrine):
    if doctrine not in _ENCOUNTER_TREES:
        from ai_tree.publisher import load_published
        from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves
        _ENCOUNTER_TREES[doctrine] = load_published(f"encounter_{doctrine.value}")
    return _ENCOUNTER_TREES[doctrine]


def should_engage(
    doctrine: Doctrine,
    own_combat: float,
    opponent_combat_estimate: float,
    own_loot: SquadLoot,
) -> bool:
    """Return True if the squad chooses to engage in combat.

    Dispatches to the published encounter tree for the squad's doctrine. The
    tree weighs combat odds against carried loot value with doctrine-specific
    thresholds; see `ai_trees/published/encounter_<doctrine>.json` for the
    authoritative logic.

    When `Tracer.enable()` has been called (typically via the simulator's
    `--trace-ai` flag), one line per decision is printed to stdout.
    """
    from ai_tree.context import Context
    from ai_tree.trace import Tracer, format_engage
    ctx = Context(
        own_combat=own_combat,
        opponent_combat_estimate=opponent_combat_estimate,
        loot=own_loot,
    )
    result = _get_encounter_tree(doctrine).tick(ctx)
    if Tracer.enabled:
        Tracer.emit(format_engage(
            doctrine.value, result, own_combat, opponent_combat_estimate, own_loot,
        ))
    return result


