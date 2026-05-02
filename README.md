# Marathon Market Simulator

A console-based market simulation set in the Marathon universe. Players trade stocks in
runner companies — bio-synthetic operatives deployed across zones of varying difficulty.
Stock prices move on weekly runner performance across **all three zones**, but players can
only monitor one zone directly. The gap between what you can see and what moves the market
is where the game lives.

## Setup

Requires [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run python marathon_market.py
```

Add `--debug` to reveal hidden zone results and the full runner roster each week.

---

## How the system works

### Runners and shells

Each of the four companies maintains a **roster of 9 persistent runners**. Runners are
bio-synthetic consciousnesses inhabiting replaceable bodies called **shells**. A runner
carries three attributes — `combat`, `extraction`, `support` — that always sum to 1.0.

There are 7 shell types. Each shell has its own affinity profile across the three axes:

| Shell | Combat | Extraction | Support | Doctrine |
|-------|--------|------------|---------|----------|
| Destroyer | 0.70 | 0.20 | 0.10 | GREEDY |
| Assassin  | 0.60 | 0.30 | 0.10 | GREEDY |
| Vandal    | 0.50 | 0.40 | 0.10 | BALANCED |
| Thief     | 0.20 | 0.70 | 0.10 | CAUTIOUS |
| Recon     | 0.20 | 0.30 | 0.50 | CAUTIOUS |
| Triage    | 0.10 | 0.10 | 0.80 | SUPPORT |
| Rook      | 0.30 | 0.50 | 0.20 | BALANCED |

A runner's **effective capability** is a weighted blend of their own attributes and their
shell's affinities. Surviving weeks in a shell slowly **drifts** the runner's attributes
toward that shell's profile — specialization emerges from career experience over time.

### Zones and squads

Every week each company deploys all 9 runners as **three squads of 3**, one per zone:

| Zone | Difficulty | Loot pool | Visible to player? |
|------|------------|-----------|-------------------|
| Sector 7 | Easy (0.1) | 12 items | Yes — your monitored zone |
| Deep Reach | Medium (0.3) | 8 items | Hidden |
| The Shelf | Hard (0.5) | 5 items | Hidden |

Inside a zone, squads compete for a **shared, finite loot pool** across up to 8 ticks.
Each tick they may find items, encounter other squads, fight, or choose to extract.
A squad's **doctrine** (GREEDY / CAUTIOUS / BALANCED / SUPPORT) governs when it decides
to leave — derived from the dominant shell type among its three runners.

If a squad is **eliminated in combat**, all three runners die and their loot is forfeit
(winners may steal Uncommon+ items). Dead runners are replaced by fresh recruits between
weeks.

### Stock prices

At the end of each week, a company's stock price moves based on how much credit its
runners extracted across **all three zones** compared to a calibrated baseline expectation.
A good week across the hidden zones can push a price up even if Sector 7 looked quiet —
and vice versa.

```
delta      = total_credits_extracted − baseline
normalized = delta / expected_weekly_stddev
price_pct  = (normalized × 10) + random_noise(±2%)
```

### The shell economy

Shells are bought at recruitment with a starting allowance of **250 cr**. Shell prices
are not fixed — they scale with how widely each shell is currently adopted across all
rosters:

```
price[shell] = 200 × (1 + 4 × (adopted_share − 1/7))
```

A shell worn by more than its fair share (1/7 ≈ 14.3%) of runners costs more;
under-adopted shells get cheaper. This creates natural cost pressure: when Destroyer,
Thief, and Triage dominate (which they do early, since they're capability-optimal),
their prices rise until a fresh recruit with 250 cr can no longer afford them and falls
to cheaper middle shells. Equilibrium settles around 58% premium / 42% middle adoption.

### Information asymmetry

You see Sector 7. The market sees everything. This is the core tension:

- A company's squad could look fine in Sector 7 while both hidden squads were wiped,
  sending its price down 15%.
- A company's Sector 7 squad could be eliminated while its hidden squads cleaned out
  The Shelf, sending its price up despite the visible bad news.
- The `[K]` shell market view is the one non-deceptive signal — it tells you which
  companies are fielding aggressive (GREEDY) vs. conservative (CAUTIOUS) doctrines
  based on shell adoption, before you know the results.

---

## What you see each week

### Planning screen

Shown at the start of every week before you commit to Hold or Trade.

```
────────────────────────────────────────────────────
  MARATHON MARKET SIMULATOR — WEEK 3
────────────────────────────────────────────────────

PORTFOLIO
  Credits:     8,600 cr
  CyberAcme   5 shares  (@ 461 cr = 2,305 cr)
  Total value: 10,905 cr

ROSTERS                                   ← roster health at a glance
  CyberAcme    9 runners  avg 2.3 extractions, 3 career deaths
  Sekiguchi    9 runners  avg 1.7 extractions, 12 career deaths   ← high churn signal
  Traxus       9 runners  avg 2.3 extractions, 3 career deaths
  NuCaloric    9 runners  avg 3.0 extractions, 0 career deaths    ← clean run so far

ZONE INTEL — Sector 7  (Easy)            ← preview only; actual squad assigned at run time
  (squad lineup randomized at deploy; preview shows roster sample)
  CyberAcme    [Yara/Thi, Kestrel/Tri, Zephyr/Thi]
  Sekiguchi    [Tessa/Thi, Pike/Thi, Reno/Thi]
  Traxus       [Hex/Tri, Daven/Tri, Tully/Thi]
  NuCaloric    [Quinn/Tri, Juno/Tri, Mire/Des]

MARKET PRICES
  CyberAcme      461.0 cr  (+21.4% last week)
  Sekiguchi      310.5 cr  ( -1.9% last week)
  Traxus         284.9 cr  ( -9.9% last week)
  NuCaloric      272.9 cr  ( +3.2% last week)

[B]uy  [S]ell  [A]ll in  s[K] shells  [H]old / advance week  [Q]uit
```

**ROSTERS** — `avg extractions` counts how many times each active runner has successfully
extracted across their career. `career deaths` is a cumulative company-level count; a
company with high deaths is fielding aggressive squads or getting unlucky in hard zones —
expect more rookie-heavy rosters and shell churn.

**ZONE INTEL** — The three runners shown per company are a stable roster preview, not a
confirmed deployment. The actual squad sent to Sector 7 is only locked in when the week
simulates. Runner names show as `Name/Shl` (first 3 letters of their shell).

### Results screen

Shown after each week simulates.

```
────────────────────────────────────────────────────
  RESULTS
────────────────────────────────────────────────────

YOUR ZONE — Sector 7  (Easy)
  CyberAcme    Squad RETURNED   [Kite/Des, Cinder/Thi, Echo/Thi]  ← you can see this
                   140 cr  ·  0 kills
  Sekiguchi    Squad LOST       [Drift/Roo, Thorne/Ass, Soren/Rec] ← Sekiguchi lost Sector 7...
                 — no extraction —
  Traxus       Squad RETURNED   [Shrike/Des, Wynn/Des, Cipher/Tri]
                   150 cr  ·  6 kills
  NuCaloric    Squad RETURNED   [Quinn/Tri, Juno/Tri, Mire/Des]
                    50 cr  ·  3 kills

MARKET RESPONSE  (all zones)             ← driven by ALL zones, not just Sector 7
  CyberAcme      379.8 →   461.0 cr  ( +21.4%)  [beat expectations]
  Sekiguchi      316.6 →   310.5 cr  (  -1.9%)  [missed expectations]  ← ...but only -1.9%?
  Traxus         316.3 →   284.9 cr  (  -9.9%)  [missed expectations]  ← Traxus looked fine here
  NuCaloric      264.5 →   272.9 cr  (  +3.2%)  [beat expectations]
```

Notice the asymmetry: Sekiguchi lost their Sector 7 squad but barely dropped (-1.9%) —
their hidden zone squads must have done well. Traxus returned from Sector 7 with 6 kills
but still fell -9.9% — both hidden squads likely struggled or were wiped. **Sector 7 is a
weak signal.** The market has information you don't.

### Shell market — `[K]`

Press `K` during the planning phase to inspect the shell economy.

```
────────────────────────────────────────────────────
  SHELL MARKET
────────────────────────────────────────────────────

  Shell          Price      Δ wk       Trend       Adoption
  ──────────  ────────  ────────  ─  ───────  ─────────────
  Triage        263.5cr     -44.4  ▼   ▁▆▆▁█▃   8 (22.2%) ★  ← above fair share, still expensive
  Thief         241.3cr     -22.2  ▼   ▁██▆▃▁   7 (19.4%) ★
  Destroyer     219.0cr       —    ·   █▇▇▇▁▁   6 (16.7%) ★
  Recon         196.8cr     +44.4  ▲   ▄▁▁▅▅█   5 (13.9%)    ← rising adoption, rising price
  Assassin      174.6cr       —    ·   ▁▁▁▁██   4 (11.1%)
  Rook          174.6cr     +22.2  ▲   █▅▅▅▁▅   4 (11.1%)
  Vandal        130.2cr       —    ·   █▁▁▁██   2 ( 5.6%)    ← cheapest; rarely worn

  ★ Premium archetypes (Destroyer/Thief/Triage):  21/36 (58.3%)
    Middle shells (Vandal/Assassin/Recon/Rook):   15/36 (41.7%)
    Fair share (uniform adoption): 14.3% per shell
```

**Trend column** — `▲/▼/·` indicates week-over-week price direction.
**Sparkline** — last 6 weeks of price history at a glance (`▁` = low, `█` = high relative to that shell's own range).
**Adoption** — how many of the 36 total deployed runners are wearing each shell, and what percentage. `★` marks the premium archetypes (capability-optimal when affordable).

High adoption + rising price = companies still value this shell despite the cost. Low adoption + falling price = the market is abandoning this shell and it's getting cheap — a good window for the next wave of recruits.

### Debug mode — `--debug`

`uv run python marathon_market.py --debug` adds three extra sections after each week's results:

**ALL ZONES BREAKDOWN** — every squad across every zone, including the hidden ones:

```
ALL ZONES BREAKDOWN  [debug]

▸ Sector 7  (Easy) ★ monitored  pool 12 → 0
  CyberAcme/S7     CAUTIOUS  extracted     2 items,    90cr, 0 kills
  Sekiguchi/S7     SUPPORT   extracted     1 items,   180cr, 0 kills
  Traxus/S7        GREEDY    extracted     5 items,   220cr, 0 kills

▸ Deep Reach  (Medium) · hidden  pool 8 → 3
  CyberAcme/DR     GREEDY    ELIMINATED   — squad wiped —
  Sekiguchi/DR     CAUTIOUS  extracted     2 items,   310cr, 0 kills
  Traxus/DR        BALANCED  extracted     1 items,    70cr, 2 kills

▸ The Shelf  (Hard) · hidden  pool 5 → 5
  CyberAcme/Sh     SUPPORT   extracted     0 items,     0cr, 0 kills
  Sekiguchi/Sh     GREEDY    ELIMINATED   — squad wiped —
  Traxus/Sh        CAUTIOUS  extracted     0 items,     0cr, 0 kills
```

**SHELL COMPOSITION** — each company's current shell breakdown:

```
SHELL COMPOSITION  [debug]
  CyberAcme    Tri×4  Des×3  Thi×2
  Sekiguchi    Thi×3  Tri×2  Des×1  Roo×1  Ass×1  Rec×1
  Traxus       Des×3  Tri×2  Thi×2  Van×1  Ass×1
  NuCaloric    Tri×5  Thi×2  Des×1  Rec×1
```

**ROSTER DETAIL** — per-runner career stats, sorted by lifetime earnings:

```
ROSTER DETAIL  [debug]

  ─ Traxus ──────────────────────────────────────────────────────
    Name        Shell       c/e/s                 Ext   Net cr  Kills    Bal
    Gale        Triage      0.25/0.31/0.44        1/1      813      0    863   ← top earner
    Daven       Destroyer   0.76/0.09/0.15        1/1      460      1    510
    Mara        Destroyer   0.81/0.05/0.14        1/1      377      2    427
    Fjord       Thief       0.12/0.86/0.01        1/1       24      0     74   ← drifting toward extraction
    ...
    Thorne      Recon       0.09/0.33/0.57        0/0        0      0    164   ← fresh recruit, no runs yet
```

`c/e/s` = combat / extraction / support attributes (always sum to 1.0). Veterans show
visible drift toward their shell's affinity; a `Thief` runner with high extraction is
starting to look like a true specialist. Fresh recruits show their random starting point.
`Bal` = spendable credit balance (earns from extractions; will fund a shell upgrade in a
future version).

---

## Calibration

If you change zone count, roster size, item values, or any core sim parameters, re-derive
the pricing constants with a 1000-week headless run:

```bash
uv run python -c "
from runner_sim.market.calibration import headless_calibration
mean, stdev = headless_calibration(weeks=1000, seed=42)
print(f'BASE_EXPECTATION     = {mean/3:.2f}')
print(f'EXPECTED_DELTA_RANGE = {stdev:.2f}')
"
```

Paste the output into `runner_sim/market/pricing.py`.

---

## Scripts

### `marathon_market.py` — Main game

```bash
uv run python marathon_market.py [--debug]
```

| Flag | Description |
|------|-------------|
| `--debug` | Reveals all hidden zone results, shell composition, and full roster detail after each week. |

### `runner_sim` — Standalone runner harness

Isolated runner ecosystem simulation — useful for validating that veteran runners
outperform novices and that shell affinity drift works as expected, without the full
market loop.

```bash
uv run python -m runner_sim [--weeks N] [--pool N] [--seed N] [--quiet] [--print-history]
```

### `squad_analysis.py` — Squad composition win-rate analysis

Monte Carlo analysis of every possible 3-shell squad composition, ranked by win rate.

```bash
uv run python squad_analysis.py
```

---

## Documentation

Design notes live in [`docs/`](docs/):

| File | Contents |
|------|----------|
| [`runner_design.md`](docs/runner_design.md) | Runner attribute system, shell affinities, and the capability formula |
| [`runner_lifecycle.md`](docs/runner_lifecycle.md) | How runners die, how recruitment works, how squads are assigned to zones |
| [`future_design.md`](docs/future_design.md) | Planned features: adaptive company AI, player board membership, shell market deepening |
| [`zone_sim_walkthrough.md`](docs/zone_sim_walkthrough.md) | Tick-by-tick walkthrough of the zone simulation engine |
| [`outcomes_design.md`](docs/outcomes_design.md) | Per-runner outcome distribution: credit shares, kill attribution, drift |
| [`tuning_levers.md`](docs/tuning_levers.md) | Calibration constants and what to adjust when the market feels off |
| [`marathon_market_prototype_spec.md`](docs/marathon_market_prototype_spec.md) | Original prototype specification |
| [`combat_ideas.md`](docs/combat_ideas.md) | Future combat resolution improvements |
