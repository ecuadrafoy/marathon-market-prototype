"""
Marathon Market Simulator — Analysis Charts
Run: uv run python charts.py
"""

import numpy as np
import matplotlib.pyplot as plt

from marathon_market import ZONES, RUNNER_SKILL_MEAN, RUNNER_SKILL_SD, _difficulty_label

skills = np.linspace(0, 1, 200)
colors = ["#4caf50", "#ff9800", "#f44336"]  # green / orange / red

fig, (ax1, ax3, ax5) = plt.subplots(3, 1, figsize=(9, 13))

# ---------------------------------------------------------------------------
# Chart 1 — Success Rate
# ---------------------------------------------------------------------------
for zone, color in zip(ZONES, colors):
    label = f"{zone.name}  ({_difficulty_label(zone.difficulty)})"
    rates = np.maximum(skills - zone.difficulty, 0)
    ax1.plot(skills, rates, label=label, color=color, linewidth=2)
    ax1.axvline(zone.difficulty, color=color, linestyle=":", alpha=0.5)

ax1.set_xlabel("Runner Skill")
ax1.set_ylabel("Success Rate")
ax1.set_xlim(0, 1)
ax1.set_ylim(0, 1)
ax1.legend(loc="upper left")
ax1.set_title("Success Rate by Skill and Zone")
ax1.grid(True, alpha=0.3)

# Runner population overlay
ax2 = ax1.twinx()
population = np.exp(-0.5 * ((skills - RUNNER_SKILL_MEAN) / RUNNER_SKILL_SD) ** 2)
population /= population.max()
ax2.fill_between(skills, population, alpha=0.08, color="gray")
ax2.set_ylabel("Runner population (relative)", color="gray")
ax2.tick_params(axis="y", labelcolor="gray")
ax2.set_ylim(0, 3)

# ---------------------------------------------------------------------------
# Chart 2 — Yield on Success
# ---------------------------------------------------------------------------
for zone, color in zip(ZONES, colors):
    label = f"{zone.name}  ({_difficulty_label(zone.difficulty)})"
    yields = (50 + skills * 100) * (1 + zone.difficulty ** 2 * 8)
    ax3.plot(skills, yields, label=label, color=color, linewidth=2)
    ax3.axvline(zone.difficulty, color=color, linestyle=":", alpha=0.5)

ax3.set_xlabel("Runner Skill")
ax3.set_ylabel("Yield on Success (cr)")
ax3.set_xlim(0, 1)
ax3.legend(loc="upper left")
ax3.set_title("Yield on Success by Skill and Zone")
ax3.grid(True, alpha=0.3)

# Runner population overlay
ax4 = ax3.twinx()
ax4.fill_between(skills, population, alpha=0.08, color="gray")
ax4.set_ylabel("Runner population (relative)", color="gray")
ax4.tick_params(axis="y", labelcolor="gray")
ax4.set_ylim(0, 3)

# ---------------------------------------------------------------------------
# Chart 3 — Expected Value (EV = success_rate × yield)
# ---------------------------------------------------------------------------
crossover_marked = False
for zone, color in zip(ZONES, colors):
    label = f"{zone.name}  ({_difficulty_label(zone.difficulty)})"
    rates = np.maximum(skills - zone.difficulty, 0)
    yields = (50 + skills * 100) * (1 + zone.difficulty ** 2 * 8)
    ev = rates * yields
    ax5.plot(skills, ev, label=label, color=color, linewidth=2)
    ax5.axvline(zone.difficulty, color=color, linestyle=":", alpha=0.5)

# Mark crossover points where each harder zone overtakes the previous one
evs = []
for zone in ZONES:
    rates = np.maximum(skills - zone.difficulty, 0)
    yields = (50 + skills * 100) * (1 + zone.difficulty ** 2 * 8)
    evs.append(rates * yields)

for i in range(1, len(evs)):
    diff = evs[i] - evs[i - 1]
    crossings = np.where(np.diff(np.sign(diff)))[0]
    for idx in crossings:
        cx = skills[idx]
        cy = evs[i][idx]
        ax5.annotate(
            f"skill {cx:.2f}",
            xy=(cx, cy),
            xytext=(cx + 0.04, cy + 8),
            fontsize=8,
            color="gray",
            arrowprops=dict(arrowstyle="->", color="gray", lw=0.8),
        )

ax5.set_xlabel("Runner Skill")
ax5.set_ylabel("Expected Value per Run (cr)")
ax5.set_xlim(0, 1)
ax5.set_ylim(0)
ax5.legend(loc="upper left")
ax5.set_title("Expected Value by Skill and Zone  (success rate x yield)")
ax5.grid(True, alpha=0.3)

# Runner population overlay
ax6 = ax5.twinx()
ax6.fill_between(skills, population, alpha=0.08, color="gray")
ax6.set_ylabel("Runner population (relative)", color="gray")
ax6.tick_params(axis="y", labelcolor="gray")
ax6.set_ylim(0, 3)

plt.tight_layout()
plt.savefig("success_rate_chart.png", dpi=150)
print("Saved success_rate_chart.png")
plt.show()
