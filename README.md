# Marathon Market Simulator

A terminal market simulation set in the Marathon universe. Players trade stocks in
runner companies — bio-synthetic operatives deployed across zones of varying difficulty.
Stock prices move on weekly runner performance across **all three zones**, but players can
only monitor one zone directly. The gap between what you can see and what moves the market
is where the game lives.

## Setup

Requires [uv](https://github.com/astral-sh/uv).

```bash
uv sync

# Textual TUI (default)
uv run python marathon_market.py

# Console fallback — plain text, debug-by-default (all hidden zones visible)
uv run python marathon_market.py --console
```

---

## How the system works

### Three-axis company state

Every company carries three independent values, each on its own clock:

| Value | Field | Cadence | Driver |
|---|---|---|---|
| **Stock price** | `Company.price` | Weekly | Performance vs. baseline + a valuation-anchored mean reversion |
| **Operating budget** | `Company.budget` | Weekly | 30% of extraction credits − payroll − bidding ± player buys ± loans |
| **Valuation** | `Company.valuation` | Every 12 weeks | Accumulated event-counter score released at the quarterly report |

The three are coupled but distinct: price is the market's twitchy opinion, budget is the
cash they can spend right now, and valuation is the slow-moving enterprise worth. The full
economy reference lives at [`docs/economy.md`](docs/economy.md).

### Runners and shells

Each of the four companies starts with a **roster of 9 persistent runners**. Runners are
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

Every week each company tries to deploy three squads — one per zone:

| Zone | Difficulty | Loot pool | Visible to player? |
|------|------------|-----------|-------------------|
| Perimeter | Easy (0.1) | 12 items | Yes — your monitored zone |
| Dire Marsh | Medium (0.3) | 10 items | Hidden |
| Outpost | Hard (0.5) | 8 items | Hidden |

A company can field **6 to 9 runners** depending on what their budget allows. Squad-chunk
sizes adapt: 9 runners deploy as 3+3+3 across all zones, 8 as 3+3+2, 7 as 3+2+2, and 6 as
3+3 across only 2 zones. **Rosters below 6 sit the week out**, earn nothing, and take a
visible reputation hit — financial distress is a public failure signal.

Inside a zone, squads compete for a **shared, finite loot pool** across up to 8 ticks.
Each tick they may find items, encounter other squads, fight, or choose to extract.
A squad's **doctrine** (GREEDY / CAUTIOUS / BALANCED / SUPPORT) governs when it decides
to leave — derived from the dominant shell type among its three runners. Per-doctrine
behaviour is driven by **behaviour trees** (one per `extraction_*` / `encounter_*`
doctrine), validated through a publish gate before runtime can load them.

If a squad is **eliminated in combat**, all three runners die and their loot is forfeit
(winners may steal Uncommon+ items). Dead runners enter a **closed free-agent pool**
that other companies can recruit from — the same consciousness can be reincarnated in
a new body for a new employer, retaining its shell-affinity record.

### Company AI

The four companies are not passive containers — they actively manage their rosters each
week. The flow runs *after* deployment, so decisions react to the week's actual outcome:

1. **Income** — 30% of extraction credits flows into the operating budget.
2. **Payroll** — each runner's weekly upkeep (base + career stats + longevity) is paid
   cheapest-first. Runners the company can't afford are **orphaned** into the free-agent
   pool, keeping their shell affinities for whoever signs them next.
3. **Voluntary cuts** — struggling companies dump high-upkeep runners.
4. **Free-agent draft** — companies bid against each other to refill their rosters, with
   bid amounts and shell preferences scaled by **strategic posture**.
5. **Loans** — companies that can't deploy and have low budget can auto-take an
   emergency 1500 cr loan. Loans must be repaid within a quarter or they accrue a
   compounding valuation penalty.
6. **Posture update** — momentum (fast EMA from this week's outcome) and risk_appetite
   (slow accumulator from accumulated history) advance based on this week's results.

The posture model is **emergent** — every company starts at neutral (0, 0). Companies
that get repeatedly wiped develop conservative risk profiles; companies riding hot
streaks turn aggressive. Posture drives deployment intelligence: an aggressive company
puts GREEDY squads in Outpost (high variance), a defensive company puts CAUTIOUS squads
in Perimeter (safe floor income). Same roster, very different play patterns.

### Stock price formula

The weekly price move has two additive components plus noise:

```
price_change_% = performance_pct           (extraction credits vs baseline, dominant)
               + anchor_pull_pct           (gentle mean-reversion toward fair_value, minor)
               + noise(±2%)

performance_pct  = ((total_credits − baseline) / expected_weekly_stddev) × 10
fair_value       = anchor_price × (projected_valuation / starting_valuation)
anchor_pull_pct  = 0.05 × (fair_value − price_before) / price_before × 100
```

The performance term is **calibrated in isolation** so the two components stay
separable: hidden-zone extraction outcomes drive the dominant weekly wobble, and the
anchor provides a slow gravitational pull toward each company's valuation-derived fair
value. Price can drift far from fair value on a hot streak — but the anchor guarantees
mean reversion eventually.

### The shell economy

Shells are bought at recruitment with a starting allowance of **250 cr**. Shell prices
are not fixed — they scale with how widely each shell is currently adopted across all
rosters:

```
price[shell] = 200 × (1 + 4 × (adopted_share − 1/7))
```

A shell worn by more than its fair share (1/7 ≈ 14.3%) of runners costs more;
under-adopted shells get cheaper. This creates natural cost pressure: when capability-
optimal shells (Destroyer, Thief, Triage) dominate, their prices rise until a fresh
recruit with 250 cr can no longer afford them and falls to cheaper middle shells. The
shell market self-heals every week — no momentum to unwind, just a recompute over the
current census.

### Player capital

Buying shares isn't just a portfolio decision — the purchase price flows directly into
the company's **operating budget**, and the trade itself accrues a positive valuation
counter for the next quarterly report. The player is a participant in the same economy
the AI plays, not a passive observer. Companies the player ignores starve on the 30%
extraction share alone; companies the player backs can recapitalise during downturns.

### Information asymmetry

You see Perimeter. The market sees everything. This is the core tension:

- A company's squad could look fine in Perimeter while both hidden squads were wiped,
  sending its price down 15%.
- A company's Perimeter squad could be eliminated while its hidden squads cleaned out
  Outpost, sending its price up despite the visible bad news.
- The `[K]` shell market view is the one non-deceptive signal — it tells you which
  companies are fielding aggressive (GREEDY) vs. conservative (CAUTIOUS) doctrines
  based on shell adoption, before you know the results.
- The `[N]` news feed and `[R]` runner registry expose the *texture* of each company's
  trajectory — wipes, loans, orphan events, veteran affinities — letting you read who's
  thriving and who's quietly decaying.

---

## Interface

### Textual TUI (default)

The primary interface is a persistent Textual TUI — no scrolling walls of text, all
information visible simultaneously.

![Marathon Market TUI — Week 21, planning phase](marketScreenshot.jpg)

```
┌ MARATHON MARKET  ·  Week 4  ·  PLANNING ─────────────────────────────────────────────┐
│ status bar (turns orange during QUARTERLY REPORT state)                              │
├─ NEWS ───────────────────────────────────────────────────────────────────────────────┤
│ ✖ W 3  CyberAcme lost 1 squad in the field                                           │
│ ◆ W 3  Q-REPORT: NuCaloric valuation 5000 → 5240 (+240 cr)                          │
│ $ W 2  Sekiguchi took emergency loan (1500 cr) to rebuild                            │
├─ CyberAcme ──────┬─ Sekiguchi ──────┬─ Traxus ─────────┬─ NuCaloric ───────────────┤
│ 461.0 cr  +21.4% │ 310.5 cr  -1.9%  │ 284.9 cr  -9.9%  │ 272.9 cr  +3.2%           │
│ budget 803cr · 9 │ budget 482cr · 7 │ budget 1207cr · 6│ budget 612cr · 8          │
│ val 5040cr (+3)  │ val 4900cr        │ val 5160cr        │ val 5240cr (-2)           │
│  (braille line   │  (braille line    │  (braille line    │  (braille chart           │
│   chart, green)  │   chart, cyan)    │   chart, orange)  │   red, last 7 weeks)      │
│ ──────────────── │ ──────────────── │ ──────────────── │ ─────────────────────────  │
│ Des×3★ Thf×2★    │ Thi×3★ Tri×2★    │ Des×3★ Tri×2★    │ Tri×5★ Thi×2★ Des×1★     │
│ +1 signed        │ -1 orphaned      │                  │ +1 signed                  │
├──────────────────┴──────────┬───────┴──────────────────┴──────┬────────────────────┤
│ PORTFOLIO                   │ ZONE INTEL                      │ SHELL MARKET       │
│ Credits:  8,600 cr          │ Perimeter  [Easy]               │ Destroyer  219cr ▲ │
│ CyberAcme  5sh @ 461 = …    │ CyberAcme  [Yara/Thi …]        │ Thief      241cr ▼ │
│ Total:    10,905 cr         │ Sekiguchi  [Tessa/Thi …]       │ Triage     263cr · │
│ FA pool:  17 idle           │ …                               │ …                  │
└─────────────────────────────┴─────────────────────────────────┴────────────────────┘
  [B]uy  [S]ell  [A]ll-in  [K]shells  [R]oster  [N]ews  [H]old/Advance  [Q]uit
```

**Status bar** — phase indicator. Turns **orange** during the quarterly-report state
so the player can't miss when a valuation report fires.

**News ticker** — rolling 4-item feed of recent events (wipes, loans, orphan events,
quarterly reports, player trades), color-coded by company. Press `[N]` for the full
history.

**Company panels** — one per company, border colour matches brand (CyberAcme green,
Sekiguchi cyan, Traxus orange, NuCaloric red). Each shows the current price, last week's
change, budget + roster size, valuation + pending-score preview, a braille line chart of
the last 7 price points with expectation dots, a compact shell composition row, and a
phase-aware event annotation (signed/orphaned during planning, lost during results).

**Portfolio** — credits, open positions, total value, free-agent pool size, and week-
over-week gain/loss after results.

**Zone Intel** — planning phase shows a squad preview for the monitored zone (Perimeter);
results phase shows each company's Perimeter outcome (RETURNED / LOST, credits, kills).

**Shell Market** — live ticker for all 7 shells: current price, week-over-week delta
(coloured), trend arrow, and a 6-week sparkline.

**Key bindings** (single keypress, always visible in the footer):

| Key | Action |
|-----|--------|
| `B` | Buy shares (modal) |
| `S` | Sell shares (modal) |
| `A` | All-in — spread available credits equally across all four companies |
| `K` | Shell market overlay — full price / adoption / sparkline table |
| `R` | Runner registry overlay — every runner grouped by contract, with upkeep, record, top affinity |
| `N` | News history overlay — full rolling feed (up to 50 events) with week separators |
| `H` / `Enter` | Hold (planning) or advance through quarterly → results → planning |
| `Q` | Quit |

### Console mode — `--console`

```bash
uv run python marathon_market.py --console
```

Plain-text fallback — useful for validating game logic without TUI overhead. Debug output
is **on by default** in console mode: after each week it prints all hidden zone outcomes,
the full shell market, and each company's shell composition breakdown. Quarterly reports
appear in an ANSI-orange band. Press `[N]` from the planning screen to open the full
news history.

### Shell market overlay — `K`

Press `K` to open the full shell market view (works during planning and results phases):

```
  Shell          Price      Δ wk       Trend       Adoption
  ──────────  ────────  ────────  ─  ───────  ─────────────
  Triage        263.5cr     -44.4  ▼   ▁▆▆▁█▃   8 (22.2%) ★
  Thief         241.3cr     -22.2  ▼   ▁██▆▃▁   7 (19.4%) ★
  Destroyer     219.0cr       —    ·   █▇▇▇▁▁   6 (16.7%) ★
  Recon         196.8cr     +44.4  ▲   ▄▁▁▅▅█   5 (13.9%)
  Assassin      174.6cr       —    ·   ▁▁▁▁██   4 (11.1%)
  Rook          174.6cr     +22.2  ▲   █▅▅▅▁▅   4 (11.1%)
  Vandal        130.2cr       —    ·   █▁▁▁██   2 ( 5.6%)
```

### Runner registry — `R`

Every runner currently in the world, grouped by contract: company rosters first, then the
free-agent pool. Each row shows the runner's current shell, weekly upkeep, deployments
survived, career kills, career net loot, and top shell affinity. `★` marks veterans with
≥ 0.30 affinity in a premium shell.

### News history — `N`

The full rolling feed of recent events (capped at 50 items). Most recent at top, grouped
by week with thin separators. Read the chronological texture of the simulation — which
company has been wipe-prone, who's been taking loans, who just printed a positive
quarterly report.

---

## Calibration

If you change zone count, roster size, item values, or any core sim parameters, re-derive
the pricing constants with a 1000-week headless run:

```bash
uv run python -c "
from runner_sim.market.calibration import headless_calibration
median, stdev = headless_calibration(weeks=1000, seed=42)
print(f'BASE_EXPECTATION     = {median/3:.2f}')
print(f'EXPECTED_DELTA_RANGE = {stdev:.2f}')
"
```

Paste the output into `runner_sim/market/pricing.py`. Note: calibration uses the
**median** of active company-weeks (not the mean), and runs the company-AI loop ON but
the valuation anchor OFF — see [`docs/economy.md`](docs/economy.md) §12 for the rationale.

---

## Scripts

### `marathon_market.py` — Main game

```bash
uv run python marathon_market.py [--debug]
uv run python marathon_market.py --console [--debug]
```

| Flag | Description |
|------|-------------|
| *(none)* | Launches the Textual TUI. |
| `--console` | Plain-text console mode. Debug output (hidden zones, shell breakdown) is on by default. |
| `--debug` | TUI: enables hidden-zone reveal in the results panel. Console mode ignores it (always debug). |
| `--trace-ai` | Print every behaviour-tree extraction/engagement decision to stdout. |

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
| [`economy.md`](docs/economy.md) | **The economy reference** — every price/value system and its maths, money-flow map, constants table, tuning levers, observed dynamics. Living document. |
| [`runner_design.md`](docs/runner_design.md) | Runner attribute system, shell affinities, and the capability formula |
| [`runner_lifecycle.md`](docs/runner_lifecycle.md) | How runners die, how recruitment works, how squads are assigned to zones |
| [`future_design.md`](docs/future_design.md) | Planned features — AI investor crowd, player board membership, adaptive company AI, shell market deepening, and the deferred valuation sell-side coupling question |
| [`zone_sim_walkthrough.md`](docs/zone_sim_walkthrough.md) | Tick-by-tick walkthrough of the zone simulation engine |
| [`outcomes_design.md`](docs/outcomes_design.md) | Per-runner outcome distribution: credit shares, kill attribution, drift |
| [`ai_tree.md`](docs/ai_tree.md) | Behaviour-tree system: registry, JSON schema, publish gate |
| [`ai_tree_authoring.md`](docs/ai_tree_authoring.md) | Authoring new doctrines + leaf conditions via the visual editor or by hand |
| [`tuning_levers.md`](docs/tuning_levers.md) | Calibration constants quick-reference (now superseded by `economy.md` §11 + §10b for new economy systems) |
| [`marathon_market_prototype_spec.md`](docs/marathon_market_prototype_spec.md) | Original prototype specification |
| [`combat_ideas.md`](docs/combat_ideas.md) | Future combat resolution improvements |
