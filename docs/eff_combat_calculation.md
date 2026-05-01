# Effective Combat Calculation

How a squad's combat roll is derived, from raw runner attributes to the final
dice throw that decides a fight.

---

## The full formula at a glance

```
eff_cbt(runner) = (runner.combat × 0.6  +  shell.combat_affinity × 0.4)  ×  affinity_score

squad_combat    = Σ eff_cbt(runners)  +  0.5 × Σ eff_sup(runners)

combat_roll     = squad_combat  +  gauss(0, 0.15)
```

Three inputs, three files, one roll.

---

## Step 1 — Runner attributes (`runners.py`)

Each runner carries a `(combat, extraction, support)` triple that always sums to 1.0.
These are career stats — they start random and drift slowly toward the shell's affinity
profile each week the runner survives.

```
Slate   C:0.68  E:0.16  S:0.16
```

These are personality stats, not capability numbers yet. A high combat score means the
runner is naturally oriented toward fighting — it doesn't translate directly to power
until it's blended with the shell.

---

## Step 2 — Shell affinity template (`shells.py`)

Each shell has a fixed `(combat_affinity, extraction_affinity, support_affinity)` template,
also summing to 1.0. The shell represents the body's natural role lean.

```
Assassin:  combat:0.6  extraction:0.3  support:0.1
Destroyer: combat:0.7  extraction:0.2  support:0.1
Vandal:    combat:0.5  extraction:0.4  support:0.1
Triage:    combat:0.1  extraction:0.1  support:0.8
```

A runner in an Assassin shell gets a combat lean from the body regardless of their own
attributes. A weak fighter in a Destroyer shell still contributes meaningfully.

---

## Step 3 — Blending runner + shell (`runners.py → effective_capability`)

```python
RUNNER_WEIGHT = 0.6
SHELL_WEIGHT  = 0.4

eff_cbt = (runner.combat × RUNNER_WEIGHT  +  shell.combat_affinity × SHELL_WEIGHT)
          × affinity_score
```

The 60/40 split means the runner's own stats carry more weight than the shell template,
but the shell is never irrelevant. A high-combat shell compensates for a low-combat runner,
and a well-matched pair amplifies both.

### The affinity score multiplier

```python
AFFINITY_FLOOR    = 0.2   # all fresh runners start here
AFFINITY_PER_WEEK = 0.05  # gained per surviving week in this shell
AFFINITY_CAP      = 1.0

affinity_score = max(AFFINITY_FLOOR, runner.shell_affinities[shell_name])
```

This is the **experience multiplier**. A runner who has never worn a shell before
operates at 20% of their potential in it. Survive 16 weeks in the same shell and
the multiplier reaches 1.0 — full potential unlocked.

| Weeks in shell | Raw affinity | Effective score |
|:--------------:|:------------:|:---------------:|
| 0              | 0.00         | 0.20 (floor)    |
| 4              | 0.20         | 0.20            |
| 10             | 0.50         | 0.50            |
| 16             | 0.80         | 0.80            |
| 20             | 1.00         | 1.00 (cap)      |

Death blocks affinity gain for that week. Switching shells resets the active score
to the floor for the new shell (though previously earned affinity in old shells is
retained if the runner switches back).

> **Current zone_sim note:** `gain_affinity` is only called in the multi-week
> `runner_sim` harness after each week's `apply_outcome`. The zone_sim creates fresh
> runners with no career history, so every runner runs at `affinity_score = 0.2`
> for the entire zone run. Wiring affinity persistence into the zone_sim is a
> planned future step.

### Worked example

Slate (C:0.68) in an Assassin shell (combat_affinity: 0.6), fresh runner:

```
eff_cbt = (0.68 × 0.6  +  0.6 × 0.4)  ×  0.2
        = (0.408        +  0.240     )  ×  0.2
        =  0.648  ×  0.2
        =  0.130
```

Same runner after 20 weeks in Assassin (affinity_score = 1.0):

```
eff_cbt = 0.648 × 1.0 = 0.648   ← 5× stronger than a fresh runner
```

---

## Step 4 — Squad combat score (`encounters.py → _squad_combat`)

```python
SUPPORT_COMBAT_BONUS = 0.5

squad_combat = sum(eff_cbt for each runner)
             + SUPPORT_COMBAT_BONUS × sum(eff_sup for each runner)
```

Support runners (Triage shell, high support attribute) contribute a partial combat
bonus — they assist the squad's fighting without being the primary combatants.
The 0.5× multiplier means a pure support runner is worth half a combat runner in a fight.

### Worked example — Hotel squad

```
           eff_cbt   eff_sup
Slate       0.130     0.021
Kestrel     0.064     0.046
Lark        0.094     0.025

squad_combat = (0.130 + 0.064 + 0.094)  +  0.5 × (0.021 + 0.046 + 0.025)
             =  0.288                    +  0.5 × 0.092
             =  0.288 + 0.046
             =  0.334  ≈  0.341  (log value, minor float rounding)
```

---

## Step 5 — The combat roll (`sim.py → _phase_combat`)

```python
COMBAT_VARIANCE = 0.15   # gaussian sigma

combat_roll = squad_combat + gauss(0, COMBAT_VARIANCE)
```

Gaussian noise with σ=0.15 means a squad with a 10% combat edge (~0.03 score difference
on a typical ~0.35 base) still loses roughly 40% of encounters. Fights between closely
matched squads are genuinely unpredictable.

The squad with the higher `combat_roll` wins. The loser is eliminated; Uncommon+ items
from their loot transfer to the winner (Commons are abandoned).

### Why variance this wide?

A σ of 0.15 against base scores around 0.3–0.4 means a ±1σ swing covers roughly
40–50% of a typical squad's base score. This is intentional — it keeps combat
dangerous even for strong squads and prevents the simulation from collapsing into
a pure stat check. The primary tuning lever for outcome predictability is this constant.

---

## Where each piece lives

| Concept | File | Constant / Function |
|---|---|---|
| Runner attributes | `runners.py` | `Runner.combat`, `.extraction`, `.support` |
| Shell template | `shells.py` | `Shell.combat_affinity`, etc. |
| Blend formula | `runners.py` | `effective_capability()` |
| Affinity score | `runners.py` | `_affinity_score()`, `gain_affinity()` |
| Squad breakdown | `encounters.py` | `_squad_breakdown()` |
| Squad combat score | `encounters.py` | `_squad_combat()` |
| Variance + roll | `sim.py` | `_phase_combat()`, `COMBAT_VARIANCE` |
