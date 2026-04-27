# Outcome Calculations — Design Reference

This document covers the four outcome systems that drive the simulation:
runner-shell attribute drift, kill count, success rate, and loot yield.
The project has two independent simulation layers — the **market layer**
(`marathon_market.py`) and the **runner ecosystem** (`runner_sim/`) — and
each layer has its own treatment of success and yield. Where they differ,
this document calls that out explicitly.

---

## Architecture Overview

| Layer | Entry point | What it models |
|---|---|---|
| Market layer | `marathon_market.py` | Zone-based runner runs; company stock prices |
| Runner ecosystem | `runner_sim/` | Persistent runner careers; squad combat; personal loot |

The market layer is the playable game loop. The runner ecosystem is a
standalone track (Track 2) that models runner identity and specialisation.
They share vocabulary (runners, shells, zones, yield) but are not yet
wired together. Integration is a future milestone.

---

## 1. Runner-Shell Attribute Drift

**Files:** `runner_sim/runners.py` — `drift_attributes()`, `gain_affinity()`

### Model

Each runner carries a three-attribute vector `(combat, extraction, support)`
that always sums to 1.0. This constraint places every runner on the
2-simplex — a triangle whose vertices are the three pure archetypes.

Shells live on the same simplex. Every shell has a
`(combat_affinity, extraction_affinity, support_affinity)` tuple that also
sums to 1.0.

| Shell     | (c, e, s)       |
|-----------|-----------------|
| Destroyer | (0.7, 0.2, 0.1) |
| Assassin  | (0.6, 0.3, 0.1) |
| Vandal    | (0.5, 0.4, 0.1) |
| Thief     | (0.2, 0.7, 0.1) |
| Recon     | (0.2, 0.3, 0.5) |
| Triage    | (0.1, 0.1, 0.8) |
| Rook      | (0.3, 0.5, 0.2) |

Each week a runner *survives*, their attributes step toward the shell's
affinity vector via an exponential moving average (EMA):

```python
new_X = old_X + ATTRIBUTE_DRIFT_RATE * (shell.X_affinity - old_X)
```

`ATTRIBUTE_DRIFT_RATE = 0.05`. The same step applies to all three axes
simultaneously, so the sum stays at 1.0 without any renormalisation:

```
Δc + Δe + Δs
  = 0.05 * [(s.c - c) + (s.e - e) + (s.s - s)]
  = 0.05 * [(s.c + s.e + s.s) - (c + e + s)]
  = 0.05 * [1 - 1] = 0
```

### Convergence schedule

| Surviving weeks | Original attribute weight | % converged toward shell |
|-----------------|--------------------------|--------------------------|
| 1               | 0.950                    | 5%                       |
| 13              | 0.513                    | ~49% (half-life)         |
| 25              | 0.277                    | ~72%                     |
| 60              | 0.046                    | ~95%                     |
| 100             | 0.006                    | ~99%                     |

Half-life ≈ 13.5 surviving weeks. Dead weeks (runner's squad lost combat)
do not advance drift — the runner respawns next week but the death week
is wasted from a specialisation perspective.

### Why drift can never overshoot

`new_X = old_X + 0.05 * (target - old_X)` is a convex combination of
`old_X` and `target` (weights 0.95 and 0.05, both positive, summing to 1).
The result is always strictly between the two values. A runner in Destroyer
approaches `combat = 0.7` from either direction but can never exceed it.
The most extreme specialist possible is bounded by the shell's affinity
ceiling — no 0.9-combat super-runner can exist.

### Shell affinity (experience score)

Separate from attribute drift, a runner also accumulates a per-shell
experience score `shell_affinities[name]`, gaining `AFFINITY_PER_WEEK = 0.05`
per surviving week in that shell, capped at `AFFINITY_CAP = 1.0`. This score
multiplies effective capability (see §3). It persists across deaths and shell
switches — experience earned in a shell is never lost, even if the runner
temporarily wears something else.

A floor of `AFFINITY_FLOOR = 0.2` prevents a brand-new runner from being
useless in an unfamiliar shell.

### The specialisation feedback loop

1. Week 1: runner picks the shell that best matches their random starting attributes.
2. Surviving that week drifts their attributes toward that shell's vector and adds 0.05 to its affinity score.
3. Week 2: the same shell is now more aligned (attributes moved toward it) **and** has a higher affinity multiplier. Both terms push the runner to stay.
4. Repeat until the runner has converged to the shell's vector and their affinity score has hit the 1.0 cap. Steady state.

The 100-week smoke test shows zero shell switches across the entire run
once week 1 choice is made — a consequence of the loop, not an explicit
lock-in mechanism.

---

## 2. Kill Count

**File:** `runner_sim/encounters.py` — `_distribute_eliminations()`

When a squad wins contested combat, they earn kills equal to the number of
runners in the losing squad. Kills are distributed proportionally to each
winner's effective combat contribution using the largest-remainder method
to guarantee the total is exact after integer flooring.

```python
raw_kills_i = losers_count * (eff_combat_i / sum(eff_combat))
floored_i   = floor(raw_kills_i)
# leftover distributed to runners with largest fractional remainders
```

Kills are tracked as a career stat on the runner (`runner.eliminations`)
but do not feed back into any capability or yield formula. They are a pure
leaderboard metric. This is a deliberate break from the first iteration of
the model, where a `log1p(eliminations)` combat score caused one runner to
accumulate 30% of total loot within 25 weeks — a snowball the bounded
attribute model was designed to replace.

---

## 3. Success Rate

Success rate is calculated differently in each layer.

### Market layer (`marathon_market.py`)

Each runner rolls individually in `_roll_success()`. The success probability
is a direct subtraction of zone difficulty from runner skill, clamped to [0, 1]:

```python
p = clamp(runner.skill - zone.difficulty, 0.0, 1.0)
runner.success = random.random() < p
```

Zone difficulties: Sector 7 = 0.1 (Easy), Deep Reach = 0.3 (Medium),
The Shelf = 0.5 (Hard). A skill-0.5 runner in Sector 7 has a 40% chance;
the same runner in The Shelf has 0%.

All runners resolve simultaneously in Pass 1 before any yields are computed
(Pass 2). This prevents company ordering from affecting congestion counts.

Company success rate for the week:

```python
success_rate = successes / total_runners  # across all zones for this company
```

### Runner ecosystem (`runner_sim/encounters.py`)

There is no per-runner probability roll in the runner ecosystem. "Success"
is squad-level: the squad that wins combat survives and extracts; the losing
squad does not. The outcome is binary per squad, stochastic via a Gaussian
noise term on each squad's combat roll:

```python
a_roll = squad_combat_score(squad_a) + random.gauss(0.0, COMBAT_VARIANCE)
b_roll = squad_combat_score(squad_b) + random.gauss(0.0, COMBAT_VARIANCE)
winner = squad_a if a_roll >= b_roll else squad_b
```

`COMBAT_VARIANCE = 0.15`. A stronger squad can still lose — the variance
term is the upset mechanism. Higher variance increases the frequency of
upsets; lower variance makes the stronger squad win almost deterministically.

Squad combat score sums individual effective combat, with support runners
contributing via a bonus term:

```python
squad_combat = sum(eff_combat) + SUPPORT_COMBAT_BONUS * sum(eff_support)
```

`SUPPORT_COMBAT_BONUS = 0.5`. Support runners contribute half a point of
combat per unit of effective support, making Triage runners meaningful in
contested fights despite having low `eff_combat` directly.

---

## 4. Loot Yield

Yield is also calculated differently in each layer.

### Market layer (`marathon_market.py`)

Yield only applies to successful runners. It is computed in `_apply_yield()`
during Pass 2, after all success rolls are known.

**Base formula:**

```python
yield_value = (50 + skill * 100) * (1 + difficulty**2 * YIELD_STEEPNESS)
```

`YIELD_STEEPNESS = 8`. The quadratic multiplier on difficulty grows the
reward much faster than the probability penalty does as zones get harder.

**Zone multipliers at current settings:**

| Zone       | Difficulty | Multiplier |
|------------|-----------|------------|
| Sector 7   | 0.1       | ×1.08      |
| Deep Reach | 0.3       | ×1.72      |
| The Shelf  | 0.5       | ×3.00      |

**EV crossover points** (expected value = success_rate × yield):

- Deep Reach overtakes Sector 7 at skill ≈ 0.56.
- The Shelf overtakes Deep Reach at skill ≈ 0.76.

The population mean is 0.5 (σ = 0.15), so most runners are below the
Sector 7/Deep Reach crossover. The Shelf is genuinely an elite-runner
reward.

**Congestion factor:**

When many runners succeed in the same zone in the same week, yields are
penalised by:

```python
congestion_factor = 1.0 / (1.0 + zone_successes * CONGESTION_K)
yield_value *= congestion_factor
```

`CONGESTION_K = 0.05`. With 10 successes in one zone the factor is
`1/(1+0.5) ≈ 0.67` — a one-third haircut on all yields in that zone.
This prevents a week where every runner succeeds from inflating company
scores to impossible levels and is an approximation of zone resource
depletion.

**Company performance score** (drives stock prices):

```python
performance_score = success_rate × average_yield   # across all zones
```

This is compared against `baseline` (market expectation from headcount),
and the delta normalized by `MAX_PERF_SCORE = 150` drives the
`price_change_pct`:

```python
delta      = performance_score - baseline
normalized = delta / MAX_PERF_SCORE
price_change_pct = (normalized * DELTA_MULTIPLIER) + uniform_noise
```

`DELTA_MULTIPLIER = 10`, `NOISE_RANGE = ±2%`.

**Recalibration:** `BASE_EXPECTATION = 30.3` is empirically derived from a
1000-week headless simulation (`headless_calibration(weeks=1000, seed=42)`).
It must be recalibrated any time `TOTAL_RUNNERS`, zone count,
`RUNNER_SKILL_MEAN/SD`, `YIELD_STEEPNESS`, or `CONGESTION_K` change.

### Runner ecosystem (`runner_sim/encounters.py`)

Loot is squad-level, computed in `_distribute_extraction()` for squads
that survived combat (both contested winners and uncontested squads).

**Squad total yield:**

```python
base_yield  = BASE_SQUAD_YIELD + EXTRACTION_YIELD_MULTIPLIER * sum(eff_extraction)
squad_yield = base_yield * (1 + SUPPORT_YIELD_AMPLIFIER * sum(eff_support))
```

`BASE_SQUAD_YIELD = 100`, `EXTRACTION_YIELD_MULTIPLIER = 200`,
`SUPPORT_YIELD_AMPLIFIER = 0.5`.

Support runners multiply the squad's total haul but do not claim a personal
share of it. Their role is multiplicative on the whole, not additive to
themselves.

**Per-runner share:**

```python
share_weight_i = eff_extraction_i
yield_i        = squad_yield * eff_extraction_i / sum(eff_extraction)
```

Personal yield is proportional to effective extraction only. A Triage runner
with `eff_extraction ≈ 0.1` earns roughly one-seventh of what a Thief runner
earns in the same squad — even though the Triage runner's presence made the
squad's total yield significantly higher.

**Why support is excluded from the personal share:**

The first iteration included support in both the yield total and the
personal share weight (`eff_extraction + 0.5 * eff_support`). This made
Triage runners "balanced" across both combat and extraction axes with a
combined budget equal to any other archetype's, so they survived more often
than Thief squads and extracted a substantial personal share when they did —
dominating the leaderboard. Lifting support into a multiplicative
squad-wide amplifier decouples the two effects: support still helps the
squad survive and extract more, but the Triage runner no longer claims a
disproportionate personal slice of the result.

**Three-archetype economic profiles:**

| Archetype | Combat contribution | Personal yield | Survival rate |
|-----------|--------------------|--------------:|---------------|
| Destroyer | High               | Moderate      | High          |
| Thief     | Low                | High (if squad wins) | Low  |
| Triage    | Moderate (support bonus) | Very low | Mid      |

Triage runners are a team-enabling role. Their personal earnings are low by
design; the value they create shows up in their teammates' leaderboard
positions, not their own.

---

## Tunable Constants Summary

### Market layer (`marathon_market.py`)

| Constant            | Value  | Effect                                                          |
|---------------------|--------|-----------------------------------------------------------------|
| `YIELD_STEEPNESS`   | 8      | Quadratic difficulty coefficient; raise to widen zone EV gap   |
| `CONGESTION_K`      | 0.05   | Yield penalty per successful runner in the same zone            |
| `BASE_EXPECTATION`  | 30.3   | Empirical market baseline; recalibrate via `headless_calibration()` |
| `MAX_PERF_SCORE`    | 150.0  | Normalization ceiling for price-change formula                  |
| `DELTA_MULTIPLIER`  | 10.0   | Amplification on price delta; controls weekly swing magnitude  |
| `NOISE_RANGE`       | 2.0    | Uniform ±% noise on every price change                        |

### Runner ecosystem (`runner_sim/`)

| Constant                      | Value | Effect                                                            |
|-------------------------------|-------|-------------------------------------------------------------------|
| `ATTRIBUTE_DRIFT_RATE`        | 0.05  | EMA step per surviving week; half-life ~13.5 weeks               |
| `AFFINITY_PER_WEEK`           | 0.05  | Experience gained per surviving week in a shell                  |
| `AFFINITY_FLOOR`              | 0.2   | Minimum effective affinity score (rookie floor)                  |
| `RUNNER_WEIGHT`               | 0.6   | Runner attributes' share in effective capability                 |
| `SHELL_WEIGHT`                | 0.4   | Shell affinity's share in effective capability                   |
| `COMBAT_VARIANCE`             | 0.15  | Gaussian sigma on each squad's combat roll; controls upset rate  |
| `SUPPORT_COMBAT_BONUS`        | 0.5   | Each unit of squad support adds 0.5 to squad combat score       |
| `BASE_SQUAD_YIELD`            | 100.0 | Yield floor for a successful extraction                          |
| `EXTRACTION_YIELD_MULTIPLIER` | 200.0 | Per-unit-of-eff_extraction yield                                |
| `SUPPORT_YIELD_AMPLIFIER`     | 0.5   | Multiplicative support bonus on squad yield                      |
