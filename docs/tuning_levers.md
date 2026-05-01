# Tuning Levers

All numeric constants that shape simulation behaviour, grouped by system.
For each lever: current value, what it controls, and what happens when you move it.

---

## Runner Career Progression (`runner_sim/runners.py`)

These control how fast runners specialize and how much shell experience matters.

| Constant | Value | Effect |
|---|---|---|
| `RUNNER_WEIGHT` | 0.6 | Share of `effective_capability` coming from runner's own stats |
| `SHELL_WEIGHT` | 0.4 | Share coming from the shell's affinity template (must sum with RUNNER_WEIGHT to 1.0) |
| `AFFINITY_FLOOR` | 0.2 | Minimum affinity score — a fresh runner in any shell starts here |
| `AFFINITY_PER_WEEK` | 0.05 | Affinity gained per surviving week in the same shell |
| `AFFINITY_CAP` | 1.0 | Maximum affinity score — fully specialized veteran |
| `ATTRIBUTE_DRIFT_RATE` | 0.05 | EMA step per surviving week: runner stats drift this fraction toward the shell's affinity profile |

### RUNNER_WEIGHT / SHELL_WEIGHT

Controls the nature vs nurture balance.

- **Raise RUNNER_WEIGHT** → runner's innate stats dominate; shell is a minor modifier; swapping shells rarely matters much
- **Lower RUNNER_WEIGHT** → the shell you wear matters more than who you are; new runners in good shells catch up quickly
- Current 60/40 split means a runner's own stats are the primary signal, but a well-matched shell provides a meaningful 40% boost

### AFFINITY_FLOOR

Sets how useful a brand-new runner is relative to a veteran.

- **Raise** → rookies are closer to veterans; specialization matters less; veteran advantage compresses
- **Lower** → fresh runners are nearly useless; high switching cost; new shells feel punishing to try
- At 0.2, a rookie operates at 20% of a veteran's potential — still functional, but clearly outclassed

### AFFINITY_PER_WEEK + AFFINITY_CAP

Controls the speed and ceiling of specialization.

- **Raise AFFINITY_PER_WEEK** → runners max out faster (currently takes 16 surviving weeks to reach 0.8); faster power curve
- **Lower AFFINITY_PER_WEEK** → longer grind to specialize; more variance in the early career
- Weeks to reach cap from floor at current rate: `(1.0 - 0.2) / 0.05 = 16 surviving weeks`

### ATTRIBUTE_DRIFT_RATE

Controls how fast a runner's personality reshapes around their shell.

- **Raise** → runners converge to their shell's profile quickly (~5 weeks to be 75% converged instead of ~14)
- **Lower** → runners retain their starting personality longer; high-skill runners stay versatile
- At 0.05 EMA step, ~72% converged after 25 surviving weeks in the same shell

---

## Squad Combat (`runner_sim/encounters.py`)

These drive the outcome of fights between squads.

| Constant | Value | Effect |
|---|---|---|
| `SQUAD_SIZE` | 3 | Runners per squad |
| `SUPPORT_COMBAT_BONUS` | 0.5 | How much each point of eff_support contributes to squad_combat |
| `COMBAT_VARIANCE` | 0.15 | Gaussian σ applied to each squad's combat roll |
| `BASE_SQUAD_YIELD` | 100.0 | Baseline extraction payout before capability scaling (old harness only) |
| `EXTRACTION_YIELD_MULTIPLIER` | 200.0 | Additional yield per unit of eff_extraction (old harness only) |
| `SUPPORT_YIELD_AMPLIFIER` | 0.5 | Multiplicative yield bonus per unit of eff_support (old harness only) |

### SUPPORT_COMBAT_BONUS

How much support runners contribute to fighting ability.

- **Raise** → support-heavy squads become dangerous fighters; Triage squads are underestimated by the engage AI
- **Lower** → support runners are pure extraction; combat is entirely determined by combat/eff_cbt runners
- At 0.5, a runner with `eff_sup=0.10` adds `0.05` to squad_combat — a small but real contribution

### COMBAT_VARIANCE

The most important balance lever in the combat system. Controls how much luck matters.

- **Raise** → fights become chaotic; a strong squad loses to a weak one regularly; stat differences become nearly irrelevant
- **Lower** → fights become deterministic; the stronger squad almost always wins; no upset potential
- At σ=0.15 against typical base scores of 0.3–0.4, a 10% combat edge (≈0.03) still loses roughly 40% of fights
- Rule of thumb: σ should stay within 30–60% of the typical squad_combat value to keep outcomes interesting

---

## Zone Simulation Engine (`runner_sim/zone_sim/sim.py`)

These control what happens inside a zone run tick by tick.

| Constant | Value | Effect |
|---|---|---|
| `DEFAULT_MAX_TICKS` | 8 | How many ticks a zone run lasts |
| `EXPLORATION_BASE_RATE` | 0.55 | Base probability of finding an item per tick, before scaling |
| `EXPLORATION_EXTRACTION_K` | 0.35 | How strongly eff_extraction boosts discovery probability |
| `ENCOUNTER_BASE_PROB` | 0.45 | Probability per squad pair that paths cross in a given tick |
| `OPPONENT_ESTIMATE_NOISE` | 0.15 | Gaussian σ on each squad's perception of the opponent's combat strength |

### DEFAULT_MAX_TICKS

Sets the run length. Every other tick-based constant is relative to this.

- **Raise** → longer runs; pools more likely to fully deplete; extraction AI's time pressure fires later; more encounter chances
- **Lower** → shorter, more chaotic runs; squads with slow extraction profiles miss out; zone feels frantic

### EXPLORATION_BASE_RATE + EXPLORATION_EXTRACTION_K

Together these determine the discovery probability formula:

```
p_find = BASE_RATE × (1 - zone.difficulty)  +  EXTRACTION_K × eff_extraction
p_find = clamp(p_find, 0.05, 0.95)
```

- **Raise BASE_RATE** → all squads find items more easily; pools deplete faster; low-extraction squads improve most
- **Lower BASE_RATE** → item finding becomes rare; extraction-specialized squads have a bigger edge
- **Raise EXTRACTION_K** → eff_extraction matters more; high-extraction squads dominate finding; doctrines diverge further
- **Lower EXTRACTION_K** → extraction stats matter less; all squads find at roughly the same rate

The two constants interact: a high BASE_RATE with low EXTRACTION_K flattens the finding curve (everyone finds equally). A low BASE_RATE with high EXTRACTION_K makes the finding curve steep (specialists dominate).

### ENCOUNTER_BASE_PROB

Controls how often squads run into each other.

- **Raise** → more fights per run; aggressive doctrines benefit; loot transfers more frequently; zone becomes a PvP gauntlet
- **Lower** → squads largely explore independently; cautious doctrines extract safely; loot transfers are rare events
- Note: encounter probability scales with how many active squads are still in the zone — more squads means more crossings even at the same base rate

### OPPONENT_ESTIMATE_NOISE

Controls how accurately a squad can read the enemy before deciding to engage.

- **Raise** → squads misjudge each other more often; CAUTIOUS squads fight when they shouldn't; GREEDY squads sometimes flee weak opponents
- **Lower** → engage/disengage decisions become nearly perfectly informed; doctrine thresholds matter more; fights become more deliberate
- This feeds directly into `should_engage()` — the combat ratio a squad calculates is `own_combat / (opponent_actual + noise)`

---

## Extraction AI Thresholds (`runner_sim/zone_sim/extraction_ai.py`)

These are hardcoded inside `should_extract()` — not named constants, but critical behaviour knobs.

| Doctrine | Trigger | Current threshold |
|---|---|---|
| GREEDY | Time pressure | Extracts when `tick / max_ticks > 0.75` |
| GREEDY | Zone dry | Extracts when `ticks_since_last_find ≥ 3` AND carrying something |
| CAUTIOUS | Has value | Extracts when carrying any `Tier.UNCOMMON` or above |
| CAUTIOUS | Spooked | Extracts when had any encounter AND carrying anything |
| BALANCED | Has value | Extracts when carrying any `Tier.UNCOMMON` or above |
| BALANCED | Dry + encountered | Extracts when zone dry AND had an encounter |
| SUPPORT | Time pressure | Extracts when `tick / max_ticks > 0.90` |
| SUPPORT | Damaged + carrying | Extracts when took damage AND carrying anything |
| Universal | Zone dry + empty | Extracts regardless of doctrine when dry AND carrying nothing |

**`zone_feels_dry` threshold: 3 ticks** — this is the `threshold_ticks` default in `SquadPerception.zone_feels_dry()`.

- **Raise dry threshold** → squads stay longer before concluding the zone is depleted; more overlap between extraction waves
- **Lower dry threshold** → squads bail faster after a cold streak; pools rarely fully drain

---

## Encounter AI Thresholds (`runner_sim/zone_sim/encounter_ai.py`)

Hardcoded inside `should_engage()`. The combat ratio is `own_combat / opponent_estimate`.

| Doctrine | Base threshold | Threshold when carrying Rare+ |
|---|---|---|
| GREEDY | ratio ≥ 0.5 | No change — fights regardless |
| CAUTIOUS | ratio ≥ 1.3 | ratio ≥ 1.5 |
| BALANCED | ratio ≥ 0.9 | ratio ≥ 1.2 |
| SUPPORT | ratio ≥ 1.5 | ratio ≥ 1.8 |

- **Raise GREEDY threshold** → even aggressive squads become more selective; fewer fights overall
- **Lower CAUTIOUS threshold** → cautious squads fight more; their loot is less safe; doctrine differentiation shrinks
- **Raise SUPPORT threshold** → Triage squads almost never fight; pure pacifist doctrine
- The `carrying_high_value` flag (Rare+) shifts every doctrine's threshold upward — protecting earned loot is universal

---

## Zone Definitions (`runner_sim/zone_sim/zones.py`)

Zone parameters are effectively tuning levers for the risk/reward curve.

| Zone | `difficulty` | `pool_size` | Effect |
|---|---|---|---|
| Sector 7 | 0.1 | 12 | Easy, abundant, low-value items dominate |
| Deep Reach | 0.3 | 8 | Medium risk, medium pool |
| The Shelf | 0.5 | 5 | Hard, scarce, high-value items dominate |

### `difficulty`

Feeds directly into the exploration roll:
```
p_find = EXPLORATION_BASE_RATE × (1 - difficulty) + ...
```

- **Raise** → finding items harder; squads spend more ticks dry; extraction-specialist advantage grows
- **Lower** → items found easily; pool depletes fast in early ticks; combat matters more than extraction skill

### `pool_size`

Total items available per zone run. Shared across all squads.

- **Raise** → pool lasts longer; late-staying squads still find things; less pressure to extract early
- **Lower** → pool empties fast; only the first few ticks are productive; time pressure fires early for everyone

---

## Market Layer (`marathon_market.py`)

These drive stock price movement in the player-facing game loop.

| Constant | Value | Effect |
|---|---|---|
| `TOTAL_RUNNERS` | 30 | Fixed global runner pool per week |
| `BASE_EXPECTATION` | 30.3 | Market's baseline performance expectation; recalibrate after major formula changes |
| `MAX_PERF_SCORE` | 150.0 | Normalization ceiling for the price-change formula |
| `DELTA_MULTIPLIER` | 10.0 | Scales how much a performance delta moves the stock price |
| `NOISE_RANGE` | 2.0 | Uniform ±% noise added to every price change |
| `HEADCOUNT_SCALE` | 0.2 | How much extra runners inflate the market's baseline expectation |
| `YIELD_STEEPNESS` | 8 | Quadratic zone difficulty multiplier in the yield formula; primary lever for zone EV gap |
| `CONGESTION_K` | 0.05 | Yield penalty per additional successful runner sharing a zone |

### BASE_EXPECTATION

The market's prior on what a company "should" produce. Price moves on the delta from this.

- **Raise** → market expects more; same performance looks like a miss; prices trend down on average
- **Lower** → market expects less; same performance looks like a beat; prices trend up on average
- This value is empirically derived from a 1000-week headless simulation. Recalibrate it whenever `TOTAL_RUNNERS`, zone count, or the yield formula changes significantly.

### DELTA_MULTIPLIER

Controls price volatility.

- **Raise** → stocks move more per week; game is more volatile and reactive; easier to profit from good intel
- **Lower** → stocks move less; information advantage matters less; game feels more stable

### YIELD_STEEPNESS

The exponent coefficient in the yield formula:
```
yield = (50 + skill × 100) × (1 + difficulty² × YIELD_STEEPNESS)
```

- **Raise** → harder zones pay dramatically more; risk/reward gap widens; The Shelf becomes much more valuable
- **Lower** → zone difficulty matters less for payout; runners distribute more evenly across zones
- See `docs/yield_design.md` for the full EV analysis that motivated the current value of 8

### CONGESTION_K

Penalty when multiple runners succeed in the same zone.

- **Raise** → concentrating runners in one zone becomes costly; player incentivised to spread picks
- **Lower** → stacking runners in a strong zone is free; optimal play collapses to "always bet the best zone"
