# Yield Value Design — Decision Log

## Goal

The yield system should reward risk. A skilled runner committing to a harder zone should
earn more per run than a low-skill runner grinding easy zones — and should reach that
higher total faster, not just eventually. Harder zones are not simply "harder" — they are
a genuine strategic choice with a better payoff ceiling for runners who can handle them.

---

## Current Formula (as of 2026-04-23)

```python
# resolve_runner()
p = clamp(skill - difficulty, 0, 1)           # success probability
yield_value = (50 + skill * 100) * (1 + difficulty)   # yield if successful
```

### Zone definitions

| Zone       | Difficulty | Label  |
|------------|-----------|--------|
| Sector 7   | 0.1       | Easy   |
| Deep Reach | 0.3       | Medium |
| The Shelf  | 0.5       | Hard   |

Skill and difficulty share the same 0–1 scale and are subtracted directly.
A runner with skill 0.5 in a difficulty 0.5 zone has 0% success chance.

---

## Problem: Current Formula Fails the Risk/Reward Goal

The linear yield multiplier `(1 + difficulty)` does not compensate for the probability
penalty. Expected value (EV = success_probability × yield) is lower in every harder zone
for every skill level.

### EV per run — current formula

| Zone       | Skill 0.5 | Skill 0.7 | Skill 0.8 |
|------------|-----------|-----------|-----------|
| Sector 7   | 44 cr     | 78 cr     | 100 cr    |
| Deep Reach | 26 cr     | 57 cr     | 85 cr     |
| The Shelf  | 0 cr      | 33 cr     | 59 cr     |

**Result:** Harder zones are strictly worse for every runner, every time.
There is no reason to ever commit to The Shelf.

---

## Design Intent

- **Low-skill runners** (≤ 0.5): grind Sector 7, small consistent gains
- **Mid-skill runners** (~0.6–0.7): Deep Reach is optimal
- **Elite runners** (≥ 0.75): The Shelf should be the best choice
- A low-skill runner grinding Sector 7 can accumulate credits over many weeks,
  but an elite runner in The Shelf should pull ahead in fewer runs

This requires EV to be monotonically increasing with difficulty for elite runners,
with the crossover point into each harder zone sitting around the skill threshold
where success probability becomes meaningful there.

---

## Proposed Fix: Quadratic Yield Multiplier

Replace the linear multiplier with a quadratic one so the yield bonus grows much
faster than the probability penalty:

```python
yield_value = (50 + skill * 100) * (1 + difficulty**2 * 8)
```

### Multipliers under proposed formula

| Zone       | Difficulty | Multiplier |
|------------|-----------|------------|
| Sector 7   | 0.1       | ×1.08      |
| Deep Reach | 0.3       | ×1.72      |
| The Shelf  | 0.5       | ×3.00      |

### EV per run — proposed formula

| Zone       | Skill 0.5 | Skill 0.6 | Skill 0.7 | Skill 0.8 |
|------------|-----------|-----------|-----------|-----------|
| Sector 7   | 43 cr     | 59 cr     | 78 cr     | 98 cr     |
| Deep Reach | 21 cr     | 57 cr     | 83 cr     | 112 cr    |
| The Shelf  | 0 cr      | 33 cr     | 72 cr     | **117 cr**|

**Result:** Elite runners (≥ 0.75) are now best off in The Shelf.
The crossover into The Shelf being optimal sits at ~skill 0.75.

### Status: Implemented 2026-04-23

---

## Prototype Scaffolding Note

The continuous yield curve is intentional prototype scaffolding, not final design.
It exists to give the market simulation a plausible economic shape — harder zones paying
more for skilled runners — so the core trading loop can be validated before the full item
system is built.

In the final game, yield will be replaced by a **loot table system**: each zone has a
table of discrete items with tiered rarities. Runner skill influences both the probability
of a successful extraction and the quality tier of items retrieved. Rare items in hard
zones are the discrete equivalent of what the quadratic multiplier is approximating here
— a categorically higher payoff for runners who can reach them.

The quadratic multiplier (`difficulty**2 * 8`) should be understood as a tuning proxy
for that future tier distribution. When loot tables arrive, the coefficient goes away and
the design intent — harder zones, rarer loot, higher payoff ceiling for elite runners —
carries forward directly into the item tier weights.

---

## EV Chart Insights (2026-04-23)

Visualising EV = success_rate × yield across the full skill range revealed the following:

- **Deep Reach overtakes Sector 7 at skill ~0.56.** Below that threshold Sector 7 is
  the better choice; above it Deep Reach pays more per run.
- **The Shelf overtakes Deep Reach at skill ~0.76.** Only genuinely elite runners
  benefit from committing to the Hard zone.
- **The population hump (mean 0.5, σ 0.15) sits almost entirely in the Sector 7
  optimal range.** Most runners in the pool are not skilled enough for The Shelf to
  be worth attempting — which is correct design. The Shelf should feel like a reward
  for outlier runners, not a default choice.
- **EV curves fan out sharply above skill 0.7.** Skill differentiation matters most
  at the elite end — the gap between a skilled runner in the right zone vs the wrong
  zone is larger than the gap between any two zones at average skill. This reinforces
  that zone selection is a meaningful strategic decision only once runners are above
  average quality.
- These crossover points are directly controllable via zone difficulty values and the
  quadratic coefficient (`8`). Raising a zone's difficulty shifts its crossover point
  rightward (requires more skill to be worth it); raising the coefficient steepens
  the EV curves and widens the gap between zones.

---

## Open Questions

- Is `difficulty**2 * 8` the right curve, or should the coefficient be tuned after
  playtesting? The `8` is a tunable constant — lower values flatten the curve,
  higher values steepen it.
- Should the crossover point be adjustable by changing zone difficulty values alone,
  or does the formula coefficient need to move with them?
