# Runner Design Notes

How a runner picks a shell each week, how match results pull their attributes
toward the shell they wear, and how loot is distributed within a squad. The
preference and drift systems live in `runner_sim/runners.py`; the loot
distribution lives in `runner_sim/encounters.py`. Both are exercised by the
test harness in `runner_sim/harness.py`.

---

## Goal

A runner should feel like a *consciousness* that gradually specialises through
the bodies (shells) it inhabits. Two requirements:

1. **Specialisation should emerge, not be assigned.** A runner created with
   random attributes ends up demonstrably specialised after enough surviving
   weeks — not because we labelled them a "combat runner" at creation, but
   because the shell they keep choosing has shaped them.
2. **Capability should be bounded.** No matter how lucky a runner gets, no
   single attribute can grow unbounded. Total combat + extraction + support
   capability has a hard ceiling. This was the failure of the first iteration
   (career-kill snowball produced a 30% loot share for one runner).

---

## Attribute Model

Each runner carries three attributes that always sum to 1.0:

```
runner.combat + runner.extraction + runner.support == 1.0
```

Geometrically this is a point on the 2-simplex (a triangle with vertices at
the three pure axes). At creation each runner is sampled uniformly from this
triangle:

```python
# runner_sim/harness.py
a, b = sorted((random.random(), random.random()))
return a, b - a, 1.0 - b
```

Shells live in the same triangle — each shell's `(combat_affinity,
extraction_affinity, support_affinity)` also sums to 1.0:

| Shell     | (c, e, s)        | Code |
|-----------|------------------|------|
| Destroyer | (0.7, 0.2, 0.1)  | D    |
| Assassin  | (0.6, 0.3, 0.1)  | A    |
| Vandal    | (0.5, 0.4, 0.1)  | V    |
| Thief     | (0.2, 0.7, 0.1)  | T    |
| Recon     | (0.2, 0.3, 0.5)  | R    |
| Triage    | (0.1, 0.1, 0.8)  | G    |
| Rook      | (0.3, 0.5, 0.2)  | K    |

Both the runner's identity and the shell's profile being on the same simplex
is the structural property that the rest of the math relies on.

---

## Effective Capability

Per-axis weekly capability mixes runner attributes with shell affinity, scaled
by accumulated experience with that shell:

```python
eff_X = (runner.X * RUNNER_WEIGHT + shell.X_affinity * SHELL_WEIGHT) * affinity_score
```

Where:

- `RUNNER_WEIGHT = 0.6`, `SHELL_WEIGHT = 0.4` (must sum to 1.0)
- `affinity_score = max(0.2, clamp(runner.shell_affinities[shell.name], 0, 1))`
- `shell_affinities[name]` accumulates 0.05 per surviving week in that shell
  (capped at 1.0); persists across deaths and shell switches.

The `affinity_score` floor of 0.2 is what keeps a brand-new runner from being
useless in a shell they've never worn. The cap of 1.0 means after ~16
surviving weeks in a shell the runner is "fully experienced" and further weeks
in it don't compound capability.

### Why per-axis mixing rather than simple choice

A runner is partially the body and partially the soul. The 0.6/0.4 split
encodes that: the runner's own attributes contribute most of their capability,
the shell biases it. Removing one or the other collapses interesting dynamics
— if `RUNNER_WEIGHT = 1.0` the shell becomes pure flavour; if `SHELL_WEIGHT =
1.0` the runner's identity is irrelevant.

---

## Shell Preference (the Runner AI)

Each surviving week, before squads form, every active runner picks the shell
that maximises **attribute-weighted effective capability**:

```python
def weighted_capability(shell):
    affinity_score = max(0.2, clamp(runner.shell_affinities[shell.name], 0, 1))
    inner = (
        runner.combat     * (runner.combat     * 0.6 + shell.combat_affinity     * 0.4)
      + runner.extraction * (runner.extraction * 0.6 + shell.extraction_affinity * 0.4)
      + runner.support    * (runner.support    * 0.6 + shell.support_affinity    * 0.4)
    )
    return inner * affinity_score

best_shell = argmax(shells, key=weighted_capability)
```

### Why the attribute weighting matters

The naive choice — sum effective capability across all three axes and pick the
max — is **mathematically invariant to shell choice**. With both runner
attributes and shell affinities constrained to sum to 1.0:

```
sum(eff_X across axes)
  = (c * 0.6 + s.c * 0.4) + (e * 0.6 + s.e * 0.4) + (s * 0.6 + s.s * 0.4)
  = 0.6 * (c + e + s) + 0.4 * (s.c + s.e + s.s)
  = 0.6 * 1 + 0.4 * 1
  = 1.0
```

The total is constant. Shells **redistribute** capability across axes; they
don't change the total. The first version of the AI summed the axes and
collapsed to picking whichever shell happened to have the highest
`shell_affinity_score`, which on week 1 was a tie broken by Python's stable
`max()` — every runner ended up in Destroyer regardless of profile.

Weighting each axis by `runner.X` makes alignment matter: a combat-heavy
runner cares more about `eff_combat` than `eff_support`, so a Destroyer
(combat 0.7) is genuinely preferable to a Triage (combat 0.1) for them. A
support-heavy runner sees the opposite ordering.

### Where stickiness comes from

There's no explicit "switching cost" in the AI. Stickiness emerges naturally
from `shell_affinities`:

- A runner who has spent 10 surviving weeks in Vandal has `affinities["Vandal"]
  ≈ 0.5`, so their `weighted_capability` for Vandal is multiplied by 0.5.
- Their `affinities["Destroyer"]` is still 0.0 (clamped to the 0.2 floor).
- Even if Destroyer would be marginally better-aligned, the alignment gap
  needs to overcome a 0.5 / 0.2 = 2.5× outer-term penalty.

Switching only happens when the alternative shell's alignment advantage is
decisive enough to beat the experience the runner has already built in their
current shell.

---

## Attribute Drift (Calculating the Target)

After a runner survives a week, their attributes step toward the shell's
affinity vector via an **exponential moving average (EMA)**:

```python
new_X = old_X + ATTRIBUTE_DRIFT_RATE * (shell.X_affinity - old_X)
```

With `ATTRIBUTE_DRIFT_RATE = 0.05`, each surviving week the runner moves 5%
of the remaining distance to the target.

### Why this preserves the simplex constraint

Sum the per-axis updates:

```
Δc + Δe + Δs
  = 0.05 * [(s.c - c) + (s.e - e) + (s.s - s)]
  = 0.05 * [(s.c + s.e + s.s) - (c + e + s)]
  = 0.05 * [1 - 1]
  = 0
```

The deltas sum to zero, so `c + e + s` stays at 1.0 forever — no explicit
renormalisation needed. This is the reason both runners and shells are on the
same simplex: the EMA update is a free closed-form operation.

### Half-life and convergence

Expand the recursion: `new_X = 0.95 * old_X + 0.05 * target`. After `n`
surviving weeks, the original attribute's weight is `0.95^n`:

| Surviving weeks | Weight on original | % converged to shell |
|-----------------|-------------------|----------------------|
| 1               | 0.950             | 5%                   |
| 13              | 0.513             | ~49% (half-life)     |
| 25              | 0.277             | ~72%                 |
| 60              | 0.046             | ~95%                 |
| 100             | 0.006             | ~99%                 |

Half-life is `ln(0.5) / ln(0.95) ≈ 13.5 surviving weeks`. Note "surviving" —
weeks where the runner's squad lost combat don't drift the runner because
`apply_outcome` only calls `drift_attributes` when `outcome.survived`. Death
itself doesn't sideline the runner (they respawn next week in a fresh shell),
but the death week is "wasted" from a drift perspective.

### Why drift can never overshoot

`new_X = old_X + 0.05 * (target - old_X)` is a convex combination of `old_X`
and `target` with weights 0.95 and 0.05 respectively (both non-negative,
summing to 1). The result is always between the two values. So:

- A runner can never drift past the shell's affinity in any direction.
- A runner in Destroyer (combat 0.7) approaches 0.7 from whichever side they
  started but stops there.
- The most extreme combat specialist possible has combat = 0.7 (Destroyer's
  ceiling). No 0.95-combat super-soldier can ever exist.

This is the structural property that bounds total runaway. Compare to the
career-kill model in the first iteration where `runner_combat_score` was
`log1p(eliminations) / log1p(50)` — that capped at 1.0 too, but the cap
landed at "kills 50 enemies", which the dominant runner cleared in 17 weeks
and then just stayed there forever, multiplied by the familiarity bonus and
the shell affinity. EMA toward the shell vector is harder to abuse because
the *target itself* is bounded by which shells exist.

---

## Interaction: The Specialisation Feedback Loop

The two mechanisms reinforce each other:

1. **Week 1**: runner has random attributes, picks the shell that best
   weights their current profile.
2. **Drift**: surviving the week pulls their attributes toward that shell's
   affinity vector.
3. **Week 2**: the shell that was best last week is now even more aligned
   (attributes drifted toward it), AND the runner has accumulated a tiny
   bit of `shell_affinity` for it. Both terms in `weighted_capability` push
   them to keep that shell.
4. Repeat until the runner has converged to the shell's vector and capped
   their affinity at 1.0. Steady state.

The 100-week smoke test shows this clearly: every runner shows 0 switches
across the entire run because once they pick a shell in week 1, both drift
and affinity reinforce the choice indefinitely.

The deferred-AI mechanism (week 1 uses the randomly assigned shell, AI
starts week 2) was added so the timeline shows an observable "natural
starting state" before the loop kicks in. Without it, week 1 would already
be the AI's choice — invisible to the user.

---

## Known Property: Middle Shells Are Strictly Dominated

Under attribute-weighted greedy optimisation, the four "middle" shells
(Vandal, Assassin, Recon, Rook) are never picked. The reason is structural:

- For combat-heavy runners (`c` large), Destroyer's `c_aff = 0.7` beats
  Assassin's 0.6, Vandal's 0.5, Rook's 0.3. Destroyer wins the argmax.
- For extraction-heavy runners, Thief's 0.7 beats Vandal's 0.4, Rook's 0.5,
  Recon's 0.3.
- For support-heavy runners, Triage's 0.8 beats Recon's 0.5, Rook's 0.2.

Middle shells have no axis where they're the best — they're a "balanced"
profile, but the AI's per-axis weighting always rewards the runner's
strongest axis decisively, which means the extreme shell on that axis wins.

This is documented behaviour, not a bug. Making the middle shells viable
requires either (a) different scoring (e.g. a quadratic bonus for balance,
which would change the model's character significantly), (b) shell-specific
mechanical roles outside the affinity vector (Recon gives intel, Rook
extracts on combat losses, etc.), or (c) faster attribute drift so runners
who start balanced *stay* balanced and find Vandal/Recon to be their best
fit before the simplex drift pulls them to an extreme.

---

## Loot Distribution and Support's Role

When a squad survives combat (won contested or uncontested), `_distribute_extraction`
in `runner_sim/encounters.py` produces a yield total and divides it across the squad.

### The current formula

```python
base_yield  = BASE_SQUAD_YIELD + EXTRACTION_YIELD_MULTIPLIER * sum(eff_extraction)
squad_yield = base_yield * (1 + SUPPORT_YIELD_AMPLIFIER * sum(eff_support))

share_weight_i = eff_extraction_i           # support does NOT add to personal share
yield_i        = squad_yield * share_weight_i / sum(share_weight)
```

Two distinct roles for support:
1. **Squad-wide yield amplification.** Every point of squad support multiplies the
   pre-amplification yield by `1 + 0.5 * point_of_support`. A Triage runner
   makes the entire squad richer.
2. **No personal share for support.** A runner's slice of the squad yield is
   proportional to their own `eff_extraction` only. Triage runners have low
   `eff_extraction` (~0.1 when fully aligned), so they personally earn little
   even though their teammates earn more because of them.

This is "team contribution" framing: support enables, but doesn't take.

### Why the previous formula didn't work

The first iteration treated support symmetrically with extraction:

```python
# old formula
extraction_total = sum(eff_extraction) + 0.5 * sum(eff_support)
squad_yield = BASE + EXT_MULT * extraction_total
share_weight_i = eff_extraction_i + 0.5 * eff_support_i  # support DID add to share
```

Compute share weights per archetype under that model:

| Archetype | eff_combat | eff_extraction | eff_support | Combat contribution<br>(c + 0.5·s) | Loot share weight<br>(e + 0.5·s) |
|---|---|---|---|---|---|
| Destroyer | 0.700 | 0.200 | 0.100 | 0.750 | 0.250 |
| Thief     | 0.200 | 0.700 | 0.100 | 0.250 | **0.750** |
| Triage    | 0.100 | 0.100 | 0.800 | **0.500** | **0.500** |

Triage's "balance budget" is `0.500 + 0.500 = 1.000`. So is Destroyer's
(`0.750 + 0.250`) and Thief's (`0.250 + 0.750`). They all sum to 1.0 — but
Triage spreads it evenly across both axes, while the others specialise. When
*both* axes matter (combat to survive, extraction to earn), spreading evenly
wins: Triage squads survive more often than Thief squads AND extract a
substantial share when they do, dominating total loot.

In a 100-week sim, Triage runners filled the top of the leaderboard. The
intended Destroyer-vs-Thief specialist contrast was suppressed by support's
double-counting.

### The structural change

Lifting support out of the per-runner share weight and into a multiplicative
amplifier on whole-squad yield decouples its two effects:

- Support still helps the squad survive combat (`SUPPORT_COMBAT_BONUS = 0.5`
  in the squad combat sum — unchanged).
- Support still increases total loot extracted (now via the multiplicative
  amplifier).
- Support no longer claims a personal share of that loot.

A Triage runner is now a force multiplier on squadmates rather than a
self-enriching all-rounder. The post-change 100-week sim shows the expected
distribution: Destroyer and Thief specialists fill the top of the leaderboard,
while Triage runners cluster at the bottom — earning less personally but
contributing significantly to whichever squads they join.

### Three viable archetypes

Under the current model, each archetype has a distinct economic profile:

| Archetype | Combat contribution | Personal yield/extraction | Survival rate | Total loot |
|---|---|---|---|---|
| **Destroyer** (combat) | high | moderate | high | high |
| **Thief** (extraction) | low | high (when squad wins) | low | high |
| **Triage** (support) | moderate (via combat bonus) | very low | mid | low (but enables others) |

The three archetypes are economic equals only if Triage's contribution to
*team* outcomes matters externally — e.g. a market layer that rewards faction
or company performance, where being on a high-yielding squad helps even
without a personal yield share. Within the test harness alone, Triage is a
loss-leader role.

---

## Tunable Constants

In `runner_sim/runners.py`:

| Constant                | Default | Effect                                      |
|-------------------------|---------|---------------------------------------------|
| `RUNNER_WEIGHT`         | 0.6     | Identity vs shell balance in capability     |
| `SHELL_WEIGHT`          | 0.4     | Must sum with RUNNER_WEIGHT to 1.0          |
| `ATTRIBUTE_DRIFT_RATE`  | 0.05    | Half-life ~13.5 surviving weeks             |
| `AFFINITY_PER_WEEK`     | 0.05    | Affinity gained per surviving week in shell |
| `AFFINITY_FLOOR`        | 0.2     | Minimum effective affinity (rookie penalty) |
| `AFFINITY_CAP`          | 1.0     | Affinity ceiling (~16 weeks to max)         |

In `runner_sim/encounters.py`:

| Constant                      | Default | Effect                                                                |
|-------------------------------|---------|-----------------------------------------------------------------------|
| `SQUAD_SIZE`                  | 3       | Runners per squad                                                     |
| `SUPPORT_COMBAT_BONUS`        | 0.5     | Each unit of squad support contributes 0.5 to squad combat            |
| `SUPPORT_YIELD_AMPLIFIER`     | 0.5     | Multiplies squad yield: `yield *= (1 + 0.5 * sum_support)`            |
| `COMBAT_VARIANCE`             | 0.15    | Gaussian sigma on each squad's combat roll — controls upset frequency |
| `BASE_SQUAD_YIELD`            | 100.0   | Floor yield for a successful extraction                               |
| `EXTRACTION_YIELD_MULTIPLIER` | 200.0   | Per-unit-of-eff_extraction yield contribution                         |

The drift rate and affinity rate happen to share the same value (0.05) but
are independent — drift moves attributes within the simplex, affinity
accumulates per-shell experience. They could diverge if the model needed
faster specialisation than experience accumulation, or vice versa.

`SUPPORT_COMBAT_BONUS` and `SUPPORT_YIELD_AMPLIFIER` also both default to 0.5
but represent different semantics — one is additive on combat sum, the other
multiplicative on yield. They're tuned independently if support's
combat-vs-yield contribution needs rebalancing.

---

## Prototype Scaffolding Note

This model is iteration #2 of the runner system, after the career-kill
snowball model failed validation (one runner accumulated 30% of total loot
in a 25-week run). It's still scaffolding — the eventual game will likely
move runner attributes from a single triple to a richer set (e.g. multiple
combat sub-attributes, equipment, faction relationships), and shell
selection will probably move from greedy AI to player-influenced or
contract-driven choice.

The structural properties worth preserving across iterations:

- **Bounded total capability** — no unbounded snowball from any single
  career stat.
- **Specialisation as emergence, not assignment** — the runner becomes who
  they are through accumulated choices, not a label set at creation.
- **Same-simplex attributes and shell vectors** — keeps drift math clean
  and bounds attributes structurally.

The `effective_capability` interface (returns `(combat, extraction, support)`
tuple) should remain stable as internals evolve — the encounter layer in
`encounters.py` only depends on those three numbers.

---

## Open Questions

- Should `ATTRIBUTE_DRIFT_RATE` be calibrated empirically (à la
  `BASE_EXPECTATION` in the market layer) or left as a tuning knob?
- Are middle shells worth resurrecting, and via which mechanism (scoring
  change, mechanical roles, slower drift)?
- Should the AI be partially randomised (softmax over `weighted_capability`
  instead of argmax) to introduce shell variety naturally? Tradeoff: less
  deterministic, harder to reason about; pro: middle shells get occasional
  picks and gain affinity, which could make their long-term comparative
  advantage real.
- **Triage runners earn very little under the current loot model.** That's
  by design (support enables, doesn't take), but it means a runner who keeps
  picking Triage will be poor in absolute terms. Should this be addressed
  via a non-loot reward channel (e.g. faction-level rewards distributed to
  contributors) or by accepting that Triage is a niche role for runners
  whose attribute profile pushes them there?
- How does this interact with the eventual market layer? A combat runner's
  yield curve under this model is bounded by Destroyer's profile — does the
  loot-table system replacing the continuous yield need to know about
  shells, or just runner attributes?
