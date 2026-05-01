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


def should_engage(
    doctrine: Doctrine,
    own_combat: float,
    opponent_combat_estimate: float,
    own_loot: SquadLoot,
) -> bool:
    """Return True if the squad chooses to engage in combat.

    Each doctrine weighs combat odds and carried loot differently. The combat
    ratio (own / opponent) is the primary input — values above 1.0 favour the
    squad, below 1.0 favour the opponent.

    A squad carrying high-tier loot (Rare or above) has a much higher disengage
    threshold across all doctrines except GREEDY — they don't want to risk
    losing what they already have.
    """
    # Avoid divide-by-zero in pathological cases (opponent has all dead runners)
    if opponent_combat_estimate <= 0.001:
        return True   # opponent is helpless, anyone engages

    combat_ratio = own_combat / opponent_combat_estimate
    best = own_loot.best_tier()
    carrying_high_value = best is not None and best >= Tier.RARE

    if doctrine == Doctrine.GREEDY:
        # Destroyer/Assassin squads are combat-confident. They engage unless heavily outgunned,
        # and they don't care much about protecting loot — they trust their guns.
        return combat_ratio >= 0.5

    elif doctrine == Doctrine.CAUTIOUS:
        # Thief/Recon squads avoid fights. They only engage when clearly favored,
        # and become even more cautious when carrying valuable loot.
        threshold = 1.5 if carrying_high_value else 1.3
        return combat_ratio >= threshold

    elif doctrine == Doctrine.BALANCED:
        # Vandal/Rook squads engage on roughly even or favorable odds.
        # Carrying high-value loot raises the threshold — protect what you have.
        threshold = 1.2 if carrying_high_value else 0.9
        return combat_ratio >= threshold

    elif doctrine == Doctrine.SUPPORT:
        # Triage squads are weakest in combat. They engage only when heavily favored,
        # and become very risk-averse when carrying anything at all.
        threshold = 1.8 if own_loot.items else 1.5
        return combat_ratio >= threshold

    return False   # fallback: disengage
