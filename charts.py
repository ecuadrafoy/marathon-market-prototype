"""
Marathon Market Simulator — Analysis Charts
Run: uv run python charts.py
"""

import numpy as np
import matplotlib.pyplot as plt

from marathon_market import (
    ZONES, RUNNER_SKILL_MEAN, RUNNER_SKILL_SD, YIELD_STEEPNESS, CONGESTION_K,
    _difficulty_label,
)

skills = np.linspace(0, 1, 200)
colors = ["#4caf50", "#ff9800", "#f44336"]  # green / orange / red

fig, (ax1, ax3, ax5, ax7) = plt.subplots(4, 1, figsize=(9, 17))

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
    yields = (50 + skills * 100) * (1 + zone.difficulty ** 2 * YIELD_STEEPNESS)
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
# Chart 3 — EV bands under congestion
# Show how EV shifts at representative zone success counts so crossover
# points can be checked under realistic congestion loads.
# ---------------------------------------------------------------------------
CONGESTION_LEVELS = [0, 3, 6, 10]  # zone successes to sample
band_alphas = [1.0, 0.55, 0.35, 0.18]

for zone, color in zip(ZONES, colors):
    rates = np.maximum(skills - zone.difficulty, 0)
    base_yields = (50 + skills * 100) * (1 + zone.difficulty ** 2 * YIELD_STEEPNESS)
    for n_success, alpha in zip(CONGESTION_LEVELS, band_alphas):
        cf = 1.0 / (1.0 + n_success * CONGESTION_K)
        ev = rates * base_yields * cf
        label = (
            f"{zone.name}  ({_difficulty_label(zone.difficulty)})"
            if n_success == 0 else None
        )
        ax5.plot(skills, ev, color=color, linewidth=1.5, alpha=alpha, label=label)

# Congestion level legend annotation
for n_success, alpha in zip(CONGESTION_LEVELS, band_alphas):
    cf = 1.0 / (1.0 + n_success * CONGESTION_K)
    ax5.annotate(
        f"N={n_success}  (×{cf:.2f})",
        xy=(0.01, 0.01),
        xycoords="axes fraction",
        xytext=(0.62, 0.38 - CONGESTION_LEVELS.index(n_success) * 0.07),
        textcoords="axes fraction",
        fontsize=7.5,
        color="gray",
        alpha=min(alpha + 0.1, 1.0),
    )

ax5.set_xlabel("Runner Skill")
ax5.set_ylabel("Expected Value per Run (cr)")
ax5.set_xlim(0, 1)
ax5.set_ylim(0)
ax5.legend(loc="upper left")
ax5.set_title(
    f"Expected Value by Skill and Zone — congestion bands  "
    f"(N = zone successes,  K={CONGESTION_K})"
)
ax5.grid(True, alpha=0.3)

ax6b = ax5.twinx()
ax6b.fill_between(skills, population, alpha=0.08, color="gray")
ax6b.set_ylabel("Runner population (relative)", color="gray")
ax6b.tick_params(axis="y", labelcolor="gray")
ax6b.set_ylim(0, 3)

# ---------------------------------------------------------------------------
# Chart 4 — Congestion factor curve
# Shows how the yield multiplier decays as more runners succeed in a zone.
# Reference line at 1.0 = no penalty.
# ---------------------------------------------------------------------------
zone_success_range = np.arange(0, 21)
cf_values = 1.0 / (1.0 + zone_success_range * CONGESTION_K)

ax7.plot(zone_success_range, cf_values, color="#2196f3", linewidth=2.5)
ax7.axhline(1.0, color="gray", linestyle="--", linewidth=1, alpha=0.6, label="No penalty")

# Annotate the typical per-zone success range based on TOTAL_RUNNERS across 3 zones
typical_lo, typical_hi = 2, 7
ax7.axvspan(typical_lo, typical_hi, alpha=0.08, color="#2196f3", label=f"Typical range ({typical_lo}–{typical_hi})")
for n in CONGESTION_LEVELS[1:]:
    cf = 1.0 / (1.0 + n * CONGESTION_K)
    ax7.annotate(
        f"N={n} → ×{cf:.2f}",
        xy=(n, cf),
        xytext=(n + 0.5, cf + 0.015),
        fontsize=8,
        color="gray",
        arrowprops=dict(arrowstyle="->", color="gray", lw=0.8),
    )

ax7.set_xlabel("Successful runners in zone this week  (N)")
ax7.set_ylabel(f"Congestion factor  1 / (1 + N × {CONGESTION_K})")
ax7.set_xlim(0, 20)
ax7.set_ylim(0.4, 1.1)
ax7.legend(loc="upper right")
ax7.set_title(f"Congestion Factor Decay  (CONGESTION_K = {CONGESTION_K})")
ax7.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig("success_rate_chart.png", dpi=150)
print("Saved success_rate_chart.png")
plt.show()
