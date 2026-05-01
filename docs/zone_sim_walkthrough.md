# Zone Simulation — File-by-File Walkthrough

How one full run of `uv run python -m runner_sim.zone_sim.harness --seed 42` executes,
traced through every file that touches it.

---

## Entry point — `runner_sim/zone_sim/harness.py`

`main()` is the top of the call stack. Nothing runs until it fires.

**Step 1 — Load items**
```
load_items()  →  data/items.csv  →  list[Item]
```
Hands back 8 `Item` objects, one per CSV row.

**Step 2 — Build runners and form squads**
```
create_runner_pool(27)  →  list[Runner]          (from runner_sim/harness.py)
form_squads(runners)    →  list[list[Runner]]    (from runner_sim/encounters.py)
```
27 runners shuffle into 9 raw squads of 3.

**Step 3 — Wrap raw squads in zone Squad objects**
```
make_squad(name, members)  →  Squad              (from zone_sim/sim.py)
```
Each Squad gets a name (Alpha, Bravo, …) and a derived Doctrine.

**Step 4 — Distribute squads across zones**
```
_distribute_squads_to_zones(squads, 3)  →  [[squad, …], [squad, …], [squad, …]]
```
Shuffles the 9 squads then round-robins them into 3 bins — 3 squads per zone.

**Step 5 — Run each zone**
```
run_zone(zone, zone_squads, items)  →  ZoneRunResult
```
The heavy lifting. See `sim.py` below.

**Step 6 — Print**
`_print_match_log` and `_print_final_summary` consume the results. Nothing here changes state.

---

## Item catalog — `data/items.csv`

Plain CSV. One row = one item type. Columns:

| Column | Purpose |
|---|---|
| `name` | Display name |
| `tier` | Integer 1–4 (maps to Tier enum) |
| `credit_value` | Flat credit conversion |
| `sector_7_weight` | Relative drop weight in Sector 7 |
| `deep_reach_weight` | Relative drop weight in Deep Reach |
| `the_shelf_weight` | Relative drop weight in The Shelf |

A weight of `0.0` means the item never spawns in that zone. Weights are relative to each
other within a zone — they do not need to sum to 1.

---

## Item loader — `runner_sim/zone_sim/items.py`

`load_items(path)` opens the CSV, reads each row, and builds an `Item` dataclass using
the zone weight columns it finds. The column→zone mapping is driven by `Zone.csv_column`
from `zones.py` — so adding a new zone only requires adding it to `ZONES` and adding a
matching column to the CSV.

Returns `list[Item]` to the harness, which passes it unchanged into `run_zone`.

---

## Zone definitions — `runner_sim/zone_sim/zones.py`

Defines the `Zone` dataclass and the `ZONES` constant:

```
Sector 7    difficulty=0.1  pool_size=12   (easy, abundant)
Deep Reach  difficulty=0.3  pool_size=8    (medium)
The Shelf   difficulty=0.5  pool_size=5    (hard, scarce but high-value)
```

`difficulty` feeds the exploration roll in `sim.py`: higher difficulty → lower discovery
probability per tick even for well-equipped squads.

`pool_size` is the number of items drawn when the zone opens. Once the pool hits 0,
no more exploration finds are possible.

`csv_column` is a computed property: `"Sector 7" → "sector_7_weight"`. This is the
bridge between zone identity and the CSV column name.

---

## Data types — `runner_sim/zone_sim/extraction_ai.py`

Defines the core value objects used across the whole simulation. Nothing in this file
*does* anything until it is called; it is pure data + two decision functions.

**Types**

| Type | What it holds |
|---|---|
| `Tier` | Enum: COMMON=1, UNCOMMON=2, RARE=3, EPIC=4 |
| `Item` | name, tier, credit_value, zone_weights dict |
| `ZoneState` | Ground-truth pool/squad counts (owned by the engine, never given to AI) |
| `SquadPerception` | What a squad can actually sense: ticks dry, had encounter, took damage, tick/max_ticks |
| `SquadLoot` | Items the squad is currently carrying + helpers (best_tier, total_credits) |
| `Doctrine` | Enum: GREEDY, CAUTIOUS, BALANCED, SUPPORT |

**Functions**

`squad_doctrine(shell_names)` — tallies each runner's shell → Doctrine and returns the
dominant one. Ties break in priority order GREEDY > BALANCED > CAUTIOUS > SUPPORT.

`should_extract(doctrine, loot, perception)` — called once per squad per tick, after
exploration and combat. Returns `True` if the squad leaves this tick. Two universal
exits fire first regardless of doctrine:
- Final tick → always extract
- Zone feels dry AND carrying nothing → cut losses

Then per-doctrine logic:
- **GREEDY** — stays until zone dries up or time runs low (>75% elapsed)
- **CAUTIOUS** — leaves the moment they hold Uncommon+ loot, or if spooked by an encounter
- **BALANCED** — leaves on Uncommon+ loot, or when zone is dry + previously encountered
- **SUPPORT** — stays longest; only leaves near time limit (>90%) or after taking damage

---

## Encounter AI — `runner_sim/zone_sim/encounter_ai.py`

`should_engage(doctrine, own_combat, opponent_estimate, own_loot)` — returns `True` if the
squad chooses to fight when paths cross.

The primary input is `combat_ratio = own_combat / opponent_estimate`. The estimate is
computed by the tick engine from the opponent's real eff_combat score plus gaussian noise,
simulating that squads can read each other's threat level but not perfectly.

Per-doctrine thresholds:

| Doctrine | Engage if ratio ≥ | Modified when carrying Rare+ |
|---|---|---|
| GREEDY | 0.5 | No change — they trust their guns |
| CAUTIOUS | 1.3 | 1.5 — very risk-averse when loaded |
| BALANCED | 0.9 | 1.2 — protect what you have |
| SUPPORT | 1.5 | 1.8 — strongly avoid fights when carrying anything |

Both squads call this independently. Combat only happens if **both** choose to engage.
If either disengages, the encounter passes without a fight.

---

## Tick engine — `runner_sim/zone_sim/sim.py`

### Key constants

```python
DEFAULT_MAX_TICKS        = 8      # length of a zone run
EXPLORATION_BASE_RATE    = 0.55   # base discovery probability
EXPLORATION_EXTRACTION_K = 0.35   # how strongly eff_extraction boosts discovery
ENCOUNTER_BASE_PROB      = 0.45   # baseline per-pair meeting probability per tick
OPPONENT_ESTIMATE_NOISE  = 0.15   # gaussian sigma on opponent strength estimate
```

### `spawn_zone_pool(zone, item_catalog)`

Filters the item catalog to items with `weight > 0` for this zone, then draws
`zone.pool_size` items using `random.choices(weights=...)`. Duplicates are allowed.
Returns a plain `list[Item]` — the shared pool all squads deplete.

### `run_zone(zone, squads, item_catalog, max_ticks=8)`

The main loop. Calls `spawn_zone_pool`, then runs ticks 1–8.

**Each tick has five phases, executed in order:**

#### Phase 1 — Exploration (per active squad)

```
eff_ext  = sum of runner effective_extraction across squad
p_find   = BASE_RATE × (1 - zone.difficulty) + EXTRACTION_K × eff_ext
p_find   = clamp(p_find, 0.05, 0.95)
```

Roll `random.random() < p_find`. On success: pop a random item from the pool, append
to squad's loot, log the find. On failure: nothing happens this phase.

Harder zones lower `p_find` directly via the `(1 - zone.difficulty)` factor, and their
smaller `pool_size` means the pool empties faster even if discovery rates were equal.

#### Phase 2 — Encounter check (across all active squads)

Active squads are shuffled, then walked in pairs. Each pair rolls against
`ENCOUNTER_BASE_PROB`. If paths cross:
- Both squads are marked `had_encounter_this_run = True`
- Each gets a noisy estimate of the other's combat strength (real value + gauss noise)
- Each calls `should_engage(doctrine, own_combat, opponent_estimate, own_loot)`
- If both engage → added to `combat_pairs` for Phase 3

#### Phase 3 — Combat resolution

For each pair in `combat_pairs`:
```
a_roll = _squad_eff_combat(a) + gauss(0, COMBAT_VARIANCE)
b_roll = _squad_eff_combat(b) + gauss(0, COMBAT_VARIANCE)
```
Higher roll wins. Loser → `eliminated = True`. Both marked `took_damage_this_run = True`.

Kill-loot transfer: only `Tier.UNCOMMON` and above move to the winner's loot.
Common items are abandoned. This is intentional — Commons aren't worth fighting over.

#### Phase 4 — Extraction decision (per active squad)

For each squad still active (not eliminated, not extracted):
- Build a `SquadPerception` from the squad's history + current tick
- Call `should_extract(doctrine, loot, perception)` from `extraction_ai.py`
- If True → `squad.extracted = True`, squad freezes with current loot

#### Phase 5 — Tick counter update

Squads that found nothing this tick have `ticks_since_last_find` incremented.
Squads that found something get it reset to 0. This counter feeds `zone_feels_dry()`
in `SquadPerception`, which is the signal the extraction AI uses to decide the zone
is running thin.

**Loop termination:** if all squads are extracted or eliminated before tick 8, the loop
exits early. Any squad still active at tick 8 is force-extracted.

---

## Runner capability math — `runner_sim/encounters.py`

Two functions from the original encounters module are imported into `sim.py`:

`_squad_breakdown(runners)` — calls `effective_capability(runner, shell)` for each
runner, stacks results into an `(N, 3)` numpy array of `[combat, extraction, support]`
per runner.

`_squad_combat(breakdown)` — `sum(combat_col) + SUPPORT_COMBAT_BONUS × sum(support_col)`.
Support runners contribute a partial combat bonus (0.5×), reflecting that they
assist the squad's fighting rather than leading it.

These are reused from the older encounter system rather than reimplemented — the
underlying capability model is shared.

---

## Runner generation — `runner_sim/harness.py`

`create_runner_pool(size)` creates runners with random `(combat, extraction, support)`
triples drawn from a uniform simplex (they always sum to 1.0). Each runner gets a
random starting shell from `SHELL_ROSTER`. Shells feed `squad_doctrine()` to determine
the Squad's combat/extraction personality.

---

## Dependency graph (import order)

```
harness.py
├── runner_sim/harness.py        → create_runner_pool
│   └── runner_sim/runners.py   → Runner, effective_capability
├── runner_sim/encounters.py     → form_squads
└── zone_sim/
    ├── items.py                 → load_items
    │   └── extraction_ai.py    → Item, Tier
    │   └── zones.py            → ZONES
    ├── sim.py                   → run_zone, Squad, make_squad, SQUAD_NAMES
    │   ├── extraction_ai.py    → Doctrine, SquadLoot, SquadPerception, should_extract, squad_doctrine
    │   ├── encounter_ai.py     → should_engage
    │   ├── zones.py            → Zone
    │   └── runner_sim/encounters.py → _squad_breakdown, _squad_combat, COMBAT_VARIANCE
    └── zones.py                → ZONES
```

No file imports from `harness.py` downward — the harness is purely a consumer. The
`zone_sim` subpackage is fully isolated from `marathon_market.py` and the original
`runner_sim` week-resolution flow.

---

## What one run produces

For each zone, a match log like:

```
=== Sector 7 (difficulty 0.1, pool_size 12) ===
[T0] Pool spawned: Coolant Cell x5, Scrap Chip x6, Calibrator x1.
[T0] 3 squads enter: Foxtrot(GREEDY), Delta(SUPPORT), Echo(BALANCED)
[T1] Delta (SUPPORT): found Scrap Chip (COMMON, 50cr). Pool: 11 left.
[T4] Foxtrot and Echo cross paths — both engage.
[T4] Combat: Foxtrot defeats Echo. Kill-loot: Calibrator (UNCOMMON).
[T7] Foxtrot extracts with 6 items (400cr).
[T8] Delta extracts at run end with 5 items (230cr).
```

Then a final leaderboard sorted by credits, showing doctrine, zone, status, item count,
and total credit value per squad.
