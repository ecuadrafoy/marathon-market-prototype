"""
Squad composition win-rate analysis — Monte Carlo.

For every combination of SQUAD_SIZE shells drawn from the full SHELL_ROSTER,
simulate TRIALS_PER_COMP matchups against a randomly drawn opponent composition
and record the win rate. Results are printed as a ranked table and saved as
squad_win_rates.png.

Assumptions:
  - Runner attributes are perfectly aligned with their shell (runner.X == shell.X_affinity).
  - Affinity score is fixed at AFFINITY_SCORE for all runners. This scales every squad
    score uniformly, so the relative ranking is stable across affinity values. Swap to
    1.0 to model veterans, leave at AFFINITY_FLOOR for fresh runners.

Run: uv run python squad_analysis.py
"""

from __future__ import annotations
import random
import sys
from itertools import combinations_with_replacement

import matplotlib.pyplot as plt

from runner_sim.shells import SHELL_ROSTER, Shell
from runner_sim.runners import AFFINITY_FLOOR
from runner_sim.encounters import COMBAT_VARIANCE, SQUAD_SIZE, SUPPORT_COMBAT_BONUS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
TRIALS_PER_COMP = 10_000
RANDOM_SEED     = 42
AFFINITY_SCORE  = AFFINITY_FLOOR   # swap to 1.0 to model veteran runners


# ---------------------------------------------------------------------------
# TYPES
# ---------------------------------------------------------------------------
Composition = tuple[Shell, ...]
Result = tuple[float, Composition, float]   # (win_rate, composition, base_score)


# ---------------------------------------------------------------------------
# COMBAT SCORE
# ---------------------------------------------------------------------------
def _base_squad_score(comp: Composition) -> float:
    """Deterministic combat score for a shell composition (no noise).

    With runner.X == shell.X_affinity and a fixed affinity score:
        eff_X = shell.X_affinity * AFFINITY_SCORE

    squad_combat = sum(eff_combat) + SUPPORT_COMBAT_BONUS * sum(eff_support)
    """
    total_combat  = sum(s.combat_affinity  for s in comp) * AFFINITY_SCORE
    total_support = sum(s.support_affinity for s in comp) * AFFINITY_SCORE
    return total_combat + SUPPORT_COMBAT_BONUS * total_support


def _label(comp: Composition) -> str:
    return "+".join(s.code for s in comp)


def _names(comp: Composition) -> str:
    return ", ".join(s.name for s in comp)


# ---------------------------------------------------------------------------
# SIMULATION
# ---------------------------------------------------------------------------
def run_analysis(comps: list[Composition]) -> list[Result]:
    random.seed(RANDOM_SEED)
    base_scores: dict[Composition, float] = {c: _base_squad_score(c) for c in comps}

    results: list[Result] = []
    for comp in comps:
        score_a = base_scores[comp]
        wins = sum(
            score_a + random.gauss(0.0, COMBAT_VARIANCE)
            >= base_scores[random.choice(comps)] + random.gauss(0.0, COMBAT_VARIANCE)
            for _ in range(TRIALS_PER_COMP)
        )
        results.append((wins / TRIALS_PER_COMP, comp, base_scores[comp]))

    results.sort(reverse=True)
    return results


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
def print_table(results: list[Result]) -> None:
    name_width = max(len(_names(comp)) for _, comp, _ in results)
    code_width = SQUAD_SIZE * 2
    header = f"{'Rank':>4}  {'Code':<{code_width}}  {'Composition':<{name_width}}  {'Win Rate':>8}  {'Base Score':>10}"
    print(header)
    print("-" * len(header))
    for rank, (rate, comp, score) in enumerate(results, start=1):
        print(
            f"{rank:>4}  {_label(comp):<{code_width}}  "
            f"{_names(comp):<{name_width}}  "
            f"{rate * 100:>7.1f}%  "
            f"{score:>10.4f}"
        )


def save_chart(results: list[Result]) -> None:
    labels = [_label(comp) for _, comp, _ in results]
    rates  = [rate * 100 for rate, _, _ in results]

    fig, ax = plt.subplots(figsize=(10, max(6, len(results) * 0.32)))

    bar_colors = ["#4caf50" if r >= 50 else "#f44336" for r in rates]
    bars = ax.barh(labels[::-1], rates[::-1], color=bar_colors[::-1], edgecolor="white", linewidth=0.4)

    ax.axvline(50, color="gray", linestyle="--", linewidth=1, alpha=0.7, label="50% (even odds)")
    ax.set_xlabel("Win Rate vs. Random Opponent (%)")
    ax.set_title(
        f"Squad Composition Win Rates — {SQUAD_SIZE}-runner squads  "
        f"({len(results)} compositions)\n"
        f"{TRIALS_PER_COMP:,} trials/composition · affinity={AFFINITY_SCORE:.2f} · seed={RANDOM_SEED}"
    )
    ax.set_xlim(0, 100)
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)

    for bar, rate in zip(bars, rates[::-1]):
        ax.text(
            min(rate + 0.8, 97), bar.get_y() + bar.get_height() / 2,
            f"{rate:.1f}%", va="center", fontsize=7.5,
        )

    plt.tight_layout()
    plt.savefig("squad_win_rates.png", dpi=150)
    print("\nSaved squad_win_rates.png")
    plt.show()


# ---------------------------------------------------------------------------
# ENTRY
# ---------------------------------------------------------------------------
def main() -> None:
    comps = list(combinations_with_replacement(SHELL_ROSTER, SQUAD_SIZE))

    print(f"Shells: {len(SHELL_ROSTER)}  |  Squad size: {SQUAD_SIZE}  |  Compositions: {len(comps)}")
    print(f"Trials: {TRIALS_PER_COMP:,}/composition  |  Affinity: {AFFINITY_SCORE:.2f}  |  Seed: {RANDOM_SEED}\n")

    results = run_analysis(comps)
    print_table(results)
    save_chart(results)


if __name__ == "__main__":
    main()
