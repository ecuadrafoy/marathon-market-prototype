# Runner Lifecycle — Death, Recruitment, and Deployment

This document traces the full lifecycle of a runner: from the moment their squad
is wiped in a zone, through replacement recruitment, to how companies organize
and send their rosters into the next extraction.

---

## Part 1 — When a Runner Dies

### What "death" means in this system

Runners don't die in the literal sense — the lore frames them as bio-synthetic
consciousnesses inhabiting replaceable bodies (shells). What actually happens is:

1. A squad is **eliminated** in combat. All three runners in that squad are
   marked dead simultaneously — the squad is the unit of survival, not the
   individual.
2. Each runner in the eliminated squad gets `_died_this_week = True` set as a
   sentinel flag. Their `death_count` stat is incremented (it stays on the record
   forever — a runner can die multiple times across a career).
3. Dead runners receive **no drift, no affinity gain, no credits** for that week.
   The run was a loss — they extracted nothing, they earned nothing.
4. Their `shell_history` gets one final entry (the shell they were wearing when
   they died), then they are removed from the roster.

The runner record itself disappears. There is no "wounded" state, no respawn for
the same runner identity. The slot opens up for a fresh recruit.

### What a runner loses on death

| Item | Lost? | Notes |
|---|---|---|
| Shell | Yes | The body is gone; the shell goes with it |
| `credit_balance` | Yes | Personal spending budget resets; new recruit starts fresh |
| Career stats (`net_loot`, `extraction_successes`, `eliminations`) | Yes | Stats are leaderboard-only, not mechanically reused |
| Shell affinity | Yes | All accumulated drift toward the shell is gone |
| Name | No (sort of) | Name returns to the pool; future recruits may reuse it |

**Note on name reuse:** `_random_runner_name` only avoids names *currently in use
across all active rosters*. Dead runners' names immediately re-enter the pool.
A future "Vega" after the original died is a new person with the same callsign.
See `future_design.md` → "Name reuse cleanup" for options to change this.

---

## Part 2 — Recruitment: Filling the Empty Slot

Replacement happens at the **end of each week**, after all zones have resolved,
inside `replace_dead_runners` (in `runner_sim/market/roster.py`).

### Step-by-step recruitment

#### 1. Count the dead
```
survivors = [r for r in roster.runners if not r._died_this_week]
deaths = len(roster.runners) - len(survivors)
```
The roster shrinks to survivors only. `deaths` tells us how many slots to fill.

#### 2. Generate the new runner's attributes

Each recruit gets a **random simplex triple** — three floats `(combat, extraction,
support)` that are uniformly distributed on the simplex, meaning they always sum
to exactly 1.0. No stat is pre-determined; the new runner is a blank slate.

```python
# Internally: pick two uniform random breakpoints, sort them, take the gaps
a, b = sorted((random.random(), random.random()))
combat, extraction, support = a, b - a, 1.0 - b
```

This is genuinely random — a new recruit might be heavily combat-focused or
heavily extraction-focused. The shell they buy later will start pulling their
attributes in a direction, but at week 0 they're unpredictable.

#### 3. Assign a name

A name is picked from the 48-entry `RUNNER_NAME_POOL` that isn't already in
use across all rosters. If the pool is exhausted (very rare given current
churn rates with 36 active slots), the recruit gets a numbered fallback
name like `Runner-048`.

#### 4. Choose and purchase a shell

This is the most interesting part. The recruit starts with **`RECRUIT_ALLOWANCE =
250.0` credits** as their personal budget. They cannot buy any shell they can't
afford.

`choose_affordable_shell(runner, market.prices, budget)` works like this:

```
affordable = all shells where market.prices[shell] <= 250.0
if nothing is affordable → buy the cheapest shell (no runner goes unshelled)
else → pick the highest-capability shell from the affordable list
```

Capability is scored by `_effective_capability(runner, shell)`:
- It weights each axis (combat/extraction/support) by how strong the runner
  already is on that axis
- Then multiplies by the runner's current affinity for that shell
- The result: a high-combat runner will rate Destroyer more than Thief even if
  they're the same price, because Destroyer's combat affinity aligns with
  the runner's own strengths

#### 5. Deduct the price, set the shell

```python
shell = choose_affordable_shell(runner, market.prices, runner.credit_balance)
runner.current_shell = shell.name
runner.credit_balance -= market.prices[shell.name]
```

Whatever credit is left after the purchase becomes the runner's starting
balance. A runner who buys a 220cr Destroyer with a 250cr allowance starts
with 30cr in pocket.

### Why recruits end up in middle shells

Premium shells (Destroyer, Thief, Triage) are **capability-optimal** — the AI
always prefers them when they're affordable. But their prices scale with how
many runners are already wearing them.

The pricing formula is:
```
price[shell] = BASE_SHELL_PRICE × (1 + k × (adopted_share - fair_share))
```

With `k = 4.0` and `BASE_SHELL_PRICE = 200.0`:
- If Destroyer is worn by 20.5% of all runners (above the 14.3% fair share),
  its price crosses 250cr — now unaffordable to a new recruit.
- That recruit falls to the next-best affordable shell: Assassin, Vandal, etc.

This is the market pressure mechanism that produces shell diversity. Without it,
every recruit would always pick Destroyer/Thief/Triage.

### Shell is sticky — it never changes

Once set at recruitment, `runner.current_shell` **never changes** for the life
of that runner. The runner's `credit_balance` accumulates weekly from extraction
income but is not spent during a career (shell upgrades are a future feature).
Only a death-and-replacement cycle resets the shell choice.

---

## Part 3 — Deployment: How Companies Send Their Runners Into Zones

At the start of each week, before any zones run, every company must send all
9 of its runners into zones. This happens in `assign_squads` (in
`runner_sim/market/deployment.py`).

### The three zones

| Zone | Difficulty | Pool Size | Monitored? |
|---|---|---|---|
| Sector 7 | 0.1 (Easy) | 12 items | Yes — player can see this |
| Deep Reach | 0.3 (Medium) | 8 items | Hidden |
| The Shelf | 0.5 (Hard) | 5 items | Hidden |

The player only sees what happens in Sector 7. The other two zones drive
market prices but stay invisible to the player unless `--debug` is active.

### Squad formation (v1: simple and deterministic)

1. **Sort all 9 runners by their ID** — this is deterministic. Runners are
   numbered sequentially in hire order; the three lowest-id runners form
   squad 0, the next three form squad 1, and the last three form squad 2.

2. **Shuffle the zone list randomly**, independently per company. Squad 0 might
   go to The Shelf one week and Sector 7 the next. Different companies shuffle
   independently, so there's no correlation between who goes where.

3. **Assign one squad per zone** — exactly 3 runners per zone, no bench.

```python
sorted_runners = sorted(roster.runners, key=lambda r: r.id)
chunks = [sorted_runners[0:3], sorted_runners[3:6], sorted_runners[6:9]]
shuffled_zones = shuffle([Sector 7, Deep Reach, The Shelf])
zone_to_squad = {zone: chunk for zone, chunk in zip(shuffled_zones, chunks)}
```

### Doctrine: what the squad "wants" to do

Once a squad is formed, its **doctrine** is derived from the dominant shell type
among its three runners. Doctrine shapes the squad's extraction behaviour —
when it decides to leave the zone.

| Shell(s) | Doctrine | Behaviour |
|---|---|---|
| Destroyer, Assassin | GREEDY | Stays longer, fights aggressively, accepts more risk for bigger loot |
| Thief, Recon | CAUTIOUS | Extracts early, minimises encounters, prioritises survival |
| Vandal, Rook | BALANCED | Middle ground — extracts when carrying decent loot |
| Triage | SUPPORT | Stays to support, extracts last |

Ties break toward the more aggressive doctrine (GREEDY > BALANCED > CAUTIOUS >
SUPPORT). A squad with one Destroyer and one Assassin and one Thief has two
GREEDY shells and one CAUTIOUS shell → doctrine is GREEDY.

**Key implication:** companies don't consciously choose doctrine. Doctrine
emerges from whatever shells the squad happens to have — which in turn reflects
the market economics of shell pricing and what survivors are still around from
previous weeks. A company that has lost all its Destroyer-wearers to casualties
cannot field a GREEDY squad until a replacement recruit can afford one.

### The zone run itself

Once squads enter, the zone simulation is entirely out of the company's hands.
Each zone runs tick-by-tick (up to 8 ticks by default):

- Squads explore, potentially finding items from the zone's finite pool
- Squads encounter each other and resolve combat
- After each tick, each surviving squad decides whether to extract based on
  its doctrine, what it's carrying, and what it perceives about the zone
- A squad that is eliminated in combat loses all its loot (the winners may
  steal Uncommon+ items from the defeated squad)
- At the final tick, any squad still in the zone extracts with whatever it
  has found

### After the zone run: outcome flow

Once all zones resolve:

1. **Credits are distributed** to each surviving runner proportional to their
   `eff_extraction` score. Eliminated squads get nothing.
2. **Kill credit is distributed** to runners on winning squads proportional to
   their `eff_combat` score.
3. **Surviving runners drift** — `gain_affinity` and `drift_attributes` pull
   their stats toward their shell's affinity profile. This is how long-tenured
   runners specialize.
4. **Dead runners are removed**, new recruits are hired.
5. **Shell market prices update** to reflect the new roster composition.

---

## Summary: The Weekly Loop

```
START OF WEEK
  └── assign_squads()
        Sort runners by ID → 3 squads
        Shuffle zones → 1 squad per zone
        Derive doctrine from shells

ZONE RUNS (all 3 zones simultaneously)
  └── run_zone() × 3
        Tick loop (up to 8 ticks):
          Exploration → may find items
          Encounter → may fight other squads
          Extraction decision → doctrine-based

POST-ZONE
  ├── Distribute credits to survivors (by eff_extraction)
  ├── Distribute kills to winners (by eff_combat)
  ├── Survivors: drift attributes, gain affinity, append shell_history
  ├── Dead runners: mark _died_this_week, increment death_count, no drift
  └── Aggregate into CompanyWeekResult → compute stock price change

RECRUITMENT
  └── replace_dead_runners()
        For each empty slot:
          Random simplex (combat, extraction, support)
          Random name from pool
          Budget: RECRUIT_ALLOWANCE = 250cr
          choose_affordable_shell() → best capability within budget
          Deduct shell price from credit_balance
          Add to roster

SHELL MARKET UPDATE
  └── update_prices()
        Recompute prices based on new roster adoption counts
        High-adoption shells get more expensive
        Low-adoption shells get cheaper
        → Next week's recruits see new prices

END OF WEEK
```

---

## Key Numbers at a Glance

| Constant | Value | Where defined |
|---|---|---|
| Roster size per company | 9 runners | `roster.py: STARTING_ROSTER_SIZE` |
| Squad size | 3 runners | Fixed (1 squad per zone) |
| Recruit starting budget | 250 cr | `roster.py: RECRUIT_ALLOWANCE` |
| Shell base price | 200 cr | `shell_market.py: BASE_SHELL_PRICE` |
| Price sensitivity (k) | 4.0 | `shell_market.py: SHELL_PRICE_SENSITIVITY` |
| Attribute drift rate per week | 5% EMA step | `runners.py: ATTRIBUTE_DRIFT_RATE` |
| Affinity gain per surviving week | 0.05 | `runners.py: AFFINITY_PER_WEEK` |
| Zone ticks per run | 8 | `sim.py: DEFAULT_MAX_TICKS` |
