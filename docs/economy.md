# The Marathon Market Economy

**Living document.** This is the single reference for every price, value, and
credit flow in the simulation, and how they interlink. Whenever a formula,
constant, or money path changes, update this file in the same commit.

Last verified against the code: `valuation-anchored-pricing` branch (off `ai-companies`).

---

## 1. The five value systems at a glance

The economy has five distinct quantities, each with its own update cadence,
its own driver, and its own "memory" behaviour:

| System | Lives on | Cadence | Driver | Stateful? |
|---|---|---|---|---|
| **Loot credits** | `Squad.loot` | per zone-run | items extracted from zone pools | no (regenerated each week) |
| **Stock price** | `Company.price` | weekly | performance vs. baseline **+ valuation-anchored mean reversion** | **yes** (`price × (1+pct)`) |
| **Operating budget** | `Company.budget` | weekly | 30% of extraction credits − payroll − bids + player buys | **yes** (running balance) |
| **Valuation** | `Company.valuation` | every 12 weeks | accumulated event "counter score" | **yes** (released quarterly) |
| **Shell price** | `ShellMarket.prices` | weekly | adoption share across all rosters | **no** (recomputed from scratch) |
| **Runner wallet** | `Runner.credit_balance` | weekly | extraction share − shell purchases | **yes** (running balance) |

The stateless/stateful split matters: the **shell market self-heals** every
week (no momentum to unwind), while the **stock market, budgets, valuation,
and wallets drift** — they carry their history forward.

**Price ↔ valuation coupling.** Stock price has *two* drivers (introduced in
the `valuation-anchored-pricing` branch): a **performance term** (the hidden
extraction-vs-baseline signal, dominant week-to-week) and an **anchor term**
that pulls the price gently toward a valuation-derived "fair value." Valuation
is the visible, slow-moving gravitational center; performance is the
unpredictable wobble around it. See §4.

---

## 2. The money-flow map

```
  ZONE LOOT POOLS                 data/items.csv  (tier → credit_value)
  Perimeter 12 / Dire Marsh 10 /  data/zones.csv  (difficulty, pool_size, weights)
  Outpost 8 items per week
        │  squads explore + extract items during the tick loop
        ▼
  SQUAD LOOT  =  Σ item.credit_value          (runner_sim/zone_sim — total_credits())
        │
        ├──────────────────────────► STOCK PRICE  ◄────────────────┐
        │   performance vs baseline    Company.price  (pricing.py)  │ anchor
        │                                                            │ pull
        ├── 30% of total ─────────────► OPERATING BUDGET ──┬─► payroll (upkeep)
        │   CREDIT_SHARE_TO_COMPANY        Company.budget   │      └─ unpaid → free-agent pool
        │                                      ▲           └─► bidding draft (signings)
        │                                      │
        │                            player buys shares (full price)
        │
        └── per-runner extraction ───► RUNNER WALLET ──────► buys / upgrades SHELL
            share (∝ eff_extraction)     Runner.credit_balance      │
                                                                    ▼
                                                            SHELL MARKET PRICE
                                                            adoption share (shell_market.py)

  EVENTS  (squad returns/wipes, orphans, signings, player buy/sell)
        │  each posts a small signed counter score
        ▼
  VALUATION   every 12 weeks:  valuation += pending_score × VALUATION_CR_PER_COUNTER
              Company.valuation  (pricing.py constants, accrued in advance_week)
        │
        └── + pending_delta × cr_per_counter ──► fair_value ──► (anchor pull, weekly)
```

---

## 3. Loot credits — where money is born

All credit in the economy originates as **loot items** pulled from zone pools.

- **Item catalog** (`data/items.csv`): each item has a `tier` and a flat
  `credit_value`. Tiers and their value bands:

  | Tier | Name | Credit value range |
  |---|---|---|
  | 1 | Common | 10 |
  | 2 | Uncommon | 10 – 50 |
  | 3 | Rare | 50 – 300 |
  | 4 | Epic | 1000 |

- **Zone pools** (`data/zones.csv`): each week every zone is stocked with a
  finite pool — Perimeter 12, Dire Marsh 10, Outpost 8 items. Which items
  spawn is weighted per-zone (`*_weight` columns in `items.csv`). Higher-
  difficulty zones carry richer pools (Outpost is the only source of several
  Epics).

- **Extraction**: during the zone tick loop, squads explore, fight, and
  extract. A squad that successfully extracts keeps its `loot`; its value is
  `total_credits() = Σ item.credit_value` (`runner_sim/zone_sim/extraction_ai.py`).
  A wiped squad forfeits everything.

- **Zone difficulty**: `0.1 / 0.3 / 0.5` for Perimeter / Dire Marsh / Outpost.
  Difficulty raises both the loot ceiling and the wipe risk — the core
  risk/reward axis.

Everything downstream — stock prices, budgets, wallets, valuation — is just a
transformation of this one weekly stream of extracted credits.

---

## 4. Stock price — `Company.price`

**File:** `runner_sim/market/pricing.py` · **Cadence:** weekly · **Stateful.**

The weekly price move is the sum of two terms plus noise:

```
price_change_%  = performance_pct        # hidden extraction-vs-baseline (dominant)
                + anchor_pull_pct        # gentle mean-reversion to fair_value (minor)
                + noise(±NOISE_RANGE)
price_after     = max(price_before × (1 + price_change_% / 100), PRICE_FLOOR)
```

The two terms are computed independently and added; the performance term is
**calibrated in isolation** (anchor=0) so the two layers stay cleanly
separable. See `compute_total_price_change_pct` in `pricing.py`.

### 4a. Performance term — the dominant, hidden driver

```
performance     = total_credits_extracted          (all squads, all zones)
baseline        = BASE_EXPECTATION × squads_deployed
delta           = performance − baseline
normalized      = delta / EXPECTED_DELTA_RANGE
performance_pct = normalized × DELTA_MULTIPLIER + uniform_noise(±NOISE_RANGE)
```

- `squads_deployed` is passed as `len(zones)` (= 3), **not** the actual squad
  count. An under-strength roster is still measured against the full 3-squad
  expectation — under-deployment is punished automatically. See
  `_build_company_result` in `week.py`.
- This is the **information-asymmetric** term: it's driven by hidden-zone
  extraction outcomes the player can't fully see, so the weekly move stays
  unpredictable.
- `BASE_EXPECTATION` is the **median** of active company-week credit totals
  (not the mean — loot is right-skewed; see §12 for why).
- `PRICE_FLOOR = 1.0` — price never hits zero.

### 4b. Anchor term — the visible, mean-reverting gravitational center

```
projected       = valuation + pending_valuation_delta × VALUATION_CR_PER_COUNTER
fair_value      = anchor_price × (projected / STARTING_VALUATION)
anchor_pull_pct = ANCHOR_STRENGTH × (fair_value − price_before) / price_before × 100
```

Where `anchor_price` is each company's own week-0 starting price
(`GameState.price_history[name][0]`).

- **Per-company anchor.** Anchoring to each company's own starting price (not
  a global divisor) preserves the deliberate inter-company price spread. At
  week 0, `projected == STARTING_VALUATION` so `fair_value == anchor_price`
  and the pull is exactly zero — the anchor only activates as valuation
  diverges from its starting point.
- **Projected, not last-reported.** The anchor tracks `valuation +
  pending_delta × cr_per_counter`, so it drifts smoothly week-to-week
  instead of jumping only at the quarterly report.
- **Mean-reverting → dampens spirals.** Anchor pull is a negative feedback
  term: it always points *back* toward fair value, never compounds in a
  single direction. This is why the system doesn't need explicit
  death-spiral damping.
- **Pull is bounded by the gap.** With `ANCHOR_STRENGTH = 0.05`, a 20%
  price/fair-value gap produces a ~1% weekly pull. The anchor is *always*
  the minor term in a single week — performance dominates with ±~10%.
- **Graceful degradation.** When the AI loop is disabled (calibration mode),
  no `anchor_input` is passed → anchor term computes to exactly 0.0 → the
  formula collapses to the pure performance signal. Calibration measures
  that pure signal so the constants stay valid regardless of how the anchor
  is tuned.

### Reading a CompanyWeekResult

`CompanyWeekResult` exposes the decomposition for UI/debug:
`price_change_pct = performance_pct + anchor_pull_pct`, plus `fair_value`
for the current target. This is the contract tests and UI rely on.

---

## 5. Operating budget — `Company.budget`

**File:** `marathon_market.py` (constants), `runner_sim/market/company_strategy.py`
(flows) · **Cadence:** weekly · **Stateful running balance.**

The budget is the company's spendable cash for running the runner operation.

### Inflows

| Source | Amount | Where |
|---|---|---|
| Extraction income | `CREDIT_SHARE_TO_COMPANY` (30%) × the company's total extracted credits | `collect_company_income`, `week.py` step 6a |
| Player share purchase | `PLAYER_BUY_TO_BUDGET_RATIO` (1.0) × purchase price, applied **immediately** | `GameEngine.do_buy` / `do_all_in` |
| Starting balance | `STARTING_COMPANY_BUDGET` = 600 | engine init |

### Outflows

| Use | Amount | Where |
|---|---|---|
| Payroll | each kept runner's `upkeep_cost` (see §7), paid cheapest-first | `settle_payroll`, `week.py` step 6b |
| Free-agent bids | winning bid amount per signed runner | `resolve_bidding`, `week.py` step 6e |
| Player share sale | currently **none** — `PLAYER_SELL_CLAWBACK_RATIO` = 0.0 | `GameEngine.do_sell` |

### Weekly cycle (post-deployment)

The AI cycle runs **after** the zone sim — `deploy → earn → pay → rehire` —
so decisions react to the week's actual outcome (`week.py` step 6):

```
6a. collect income       budget += 0.30 × extracted credits
6b. settle payroll       budget −= Σ upkeep (cheapest first); unpaid → free agents
6c. voluntary drops      struggling companies cut runners > 2× median upkeep
6d. age free-agent pool  retire 8-week idles, spawn rookies below MIN_GLOBAL_POOL
6e. bidding draft        budget −= winning bids; sign from free-agent pool
```

If a company can't make payroll, runners are **orphaned** to the free-agent
pool (keeping their shell affinities) rather than the budget going negative.

---

## 6. Valuation — `Company.valuation`

**File:** `runner_sim/market/pricing.py` (constants `STARTING_VALUATION`,
`VALUATION_CR_PER_COUNTER`), `marathon_market.py` (event accrual + quarterly
tick in `GameEngine.advance_week`) · **Cadence:** every 12 weeks ·
**Stateful, released quarterly.**

`STARTING_VALUATION` and `VALUATION_CR_PER_COUNTER` are owned by `pricing.py`
because the stock-price anchor formula needs them too; `marathon_market.py`
re-imports them. Single source of truth.

Valuation is the company's *enterprise worth* — brand strength, roster pedigree,
market position. Distinct from price (market sentiment) and budget (cash on
hand). It moves on a deliberate quarterly cadence so it ignores weekly noise.

### The counter model

Events post a small signed **counter score** to `Company.pending_valuation_delta`
throughout the quarter. The score is unitless — it converts to credits only at
the report.

`valuation_delta_for_event` in `marathon_market.py` defines the per-event weights:

| Event | Counter | Rationale |
|---|---|---|
| `player_buy` | `+1 × shares` | confidence vote, ownership-weighted |
| `player_sell` | `−1 × shares` | symmetric loss of confidence |
| `squad_returned` | `+1` | routine win |
| `squad_eliminated` | `−3` | asymmetric — wipes hurt more than wins help |
| `runner_orphaned` | `−2` | public signal of financial distress |
| `runner_signed` | `+1` | successful talent acquisition |

### The quarterly report

On weeks that are multiples of `QUARTERLY_REPORT_WEEKS` (12), in
`GameEngine.advance_week`:

```
delta_cr        = pending_valuation_delta × VALUATION_CR_PER_COUNTER   (× 20)
valuation       = max(0, valuation + delta_cr)        ("bankrupt-reputation" floor at 0)
pending_delta   = 0                                   (reset for next quarter)
```

With current weights a quarter (~36 squad outcomes/company) typically prints
**−1200 to +1000 cr** of valuation movement. Two tuning surfaces: the per-event
counter weights, and the single global `VALUATION_CR_PER_COUNTER` scale knob.

### Open design question

The sell-side budget coupling is deliberately inert (`PLAYER_SELL_CLAWBACK_RATIO`
= 0.0) — selling damages valuation but not budget. See
`docs/future_design.md` → "Valuation: Sell-Side Economic Coupling".

---

## 7. Runner upkeep — `Runner.upkeep_cost`

**File:** `runner_sim/market/company_strategy.py` · recomputed each payroll.

Upkeep is what a runner costs their company per week. It's pure **earned
value** — no shell-tier bias, so a Destroyer rookie and a Recon rookie cost
the same until they prove themselves:

```
upkeep = BASE_UPKEEP
       + UPKEEP_PER_NET_LOOT            × career net_loot
       + UPKEEP_PER_ELIM               × career eliminations
       + UPKEEP_PER_DEPLOYMENT_SURVIVED × deployments survived
```

Three orthogonal axes of value: extraction record, combat record, and pure
longevity. The longevity axis is what lets a long-serving support runner
(low loot, low kills) still command real upkeep.

Indicative spread:

| Runner | Upkeep |
|---|---|
| Rookie (0 / 0 / 0) | ~15 cr |
| Mid-career extractor (2000 loot / 8 kills / 10 survived) | ~82 cr |
| Long-serving support (500 loot / 1 kill / 35 survived) | ~111 cr |
| Elite veteran (8000 loot / 30 kills / 40 survived) | ~280 cr |

Upkeep also drives the **bidding economy**: a struggling company filters to
free agents with upkeep ≤ 1.1× its roster median; a thriving company bids
1.5× upkeep to outbid rivals for high-affinity veterans.

---

## 8. Runner wallet — `Runner.credit_balance`

**File:** `runner_sim/runners.py` (field), `runner_sim/market/week.py` (flows).

Each runner has a personal credit balance — distinct from the company budget.

- **Inflow:** a share of the squad's extracted credits, distributed in
  proportion to each runner's effective extraction stat
  (`credit_share = total × eff_extraction / Σ eff_extraction`,
  `_update_runners_for_squad` in `week.py`).
- **Outflow:** buying a shell at recruitment, and weekly shell upgrades via
  `reequip_survivors`.
- **Seed:** a fresh recruit starts with `RECRUIT_ALLOWANCE` = 250 cr, of which
  the initial shell purchase is deducted.

This wallet is the link between the loot layer and the shell market — it's
how individual runner success translates into shell demand.

---

## 9. Shell market — `ShellMarket.prices`

**File:** `runner_sim/market/shell_market.py` · **Cadence:** weekly · **Stateless.**

The shell market is the one **stateless** price system: `update_prices` throws
away the old prices and recomputes each from scratch every week, based purely
on **adoption share**:

```
adopted_share[s] = (runners wearing s) / total_runners
fair_share       = 1 / N_SHELLS         (= 1/7 ≈ 14.3%)
price[s] = BASE_SHELL_PRICE × (1 + SHELL_PRICE_SENSITIVITY × (adopted_share[s] − fair_share))
```

- A shell at exactly fair share sits at `BASE_SHELL_PRICE` (200 cr).
- With `k = SHELL_PRICE_SENSITIVITY = 4.0`: a shell at 0% adoption bottoms out
  near **86 cr**; at 50% adoption it reaches **~486 cr**. Prices never go
  negative or zero.

### How prices fall

Because price has **no memory**, a shell gets cheaper the instant its adoption
share dips. That happens when runners switch away (`reequip_survivors`),
runners wearing it die or are orphaned (in `ai-companies`, orphans carry
`current_shell = ""` and drop out of all counts), other shells gain share
(adoption is zero-sum), or new hires pick cheaper alternatives.

### Self-correcting equilibrium

The system is **negative feedback**: a shell gets popular → adoption share ↑ →
price ↑ → new/upgrading runners can't afford it → they pick cheaper shells →
its share plateaus or falls → price stabilises. Equilibrium is where the
marginal recruit is indifferent between an expensive popular shell and a cheap
niche one. No sell-side mechanism is needed.

### `ai-companies` interaction note

`update_prices` is called with `all_runners(rosters)` — **employed runners
only**, not the free-agent pool. A big wave of orphaning shrinks the
denominator, so every remaining runner's adoption share ticks up — mass
orphaning can briefly *raise* shell prices even though nobody changed shells.

---

## 10. How the systems interlink

A change in one system ripples outward. Some of the load-bearing loops:

- **Performance → price → player behaviour → budget.** Good extraction raises
  the stock price; a higher price attracts player buys; buys fund the budget;
  a fatter budget keeps better runners; better runners extract more. A
  virtuous (or vicious) spiral.
- **Budget → roster size → deployment → performance.** A company that can't
  make payroll orphans runners; an under-strength roster (< 9, down to a
  6-runner minimum) fields fewer/weaker squads; weaker squads extract less and
  die more; less extraction means less income. Death spiral risk.
- **Loot → wallet → shell demand → shell price.** Successful runners earn
  bigger wallets, can afford premium shells, raising those shells' adoption
  and price — which prices the *next* recruits out, pushing them to cheaper
  shells. Cost arbitrage.
- **Events → valuation → price (anchor) → player.** Every squad outcome and
  roster change silently accrues a counter score; the consequence partially
  surfaces immediately (via the anchor's pull on weekly price through
  `pending_valuation_delta`) and fully releases at the quarterly report. The
  player can read the pending score to *anticipate* upcoming price drift
  before the report fires.
- **Player capital is a multi-edged lever.** Buying funds operations *now*
  (budget injection), nudges valuation *later* (counter score), and raises
  the anchor target (fair_value) which pulls future price up. Selling
  damages valuation only — but that damage flows through the anchor to
  weekly price within a quarter. The player is fully entangled in the same
  economy the AI plays.

---

## 10b. Tuning levers — what each knob does

Eight knobs shape the entire economy. Each one is a meaningful design dial;
this section is the reference for *which knob to turn to fix what*. All
values cited are current defaults.

### Price-formula levers (`runner_sim/market/pricing.py`)

| Lever | Default | Raise to … | Lower to … |
|---|---|---|---|
| `ANCHOR_STRENGTH` | **0.05** | tame runaway prices, faster reversion to valuation. Half-life roughly `ln(2)/k` weeks — 0.05 = ~14 weeks, 0.1 = ~7 weeks. | let prices drift further from fundamentals before the anchor catches them. Risk: weak anchor = price ↔ valuation feel disconnected again. |
| `DELTA_MULTIPLIER` | **10.0** | make weekly price moves more dramatic (±1 stdev → ±15%). Good if play feels too slow. | calm the market down. Reducing to 5 caps the ±1-stdev week at ±5%. Direct dial on volatility. |
| `NOISE_RANGE` | **2.0** | inject more randomness into weekly moves. Adds suspense but can drown out the performance signal. | make price moves more "earned" — almost entirely driven by extraction outcomes. |
| `BASE_EXPECTATION` | **120** (median ÷ 3) | raise the bar — companies look weaker, prices drift down on average. | lower the bar — every week looks like a win. **Recalibrate** rather than hand-tune (see §12). |
| `EXPECTED_DELTA_RANGE` | **826** | flatten weekly % moves (delta divides by a bigger denominator). | sharpen weekly moves. Again, **recalibrate** is the right path. |
| `PRICE_FLOOR` | **1.0** | give failing companies a softer floor to recover from. | allow more dramatic collapses. |

### Valuation-formula levers

| Lever | Default | Raise to … | Lower to … |
|---|---|---|---|
| `VALUATION_CR_PER_COUNTER` | **20.0** (`pricing.py`) | make quarterly reports more dramatic — same counter scores translate to bigger cr swings, which then drive bigger anchor pulls in subsequent weeks. | make valuation a quiet background number, almost ignorable. |
| Per-event counter weights (`valuation_delta_for_event` in `marathon_market.py`) | varies | shift which events matter to enterprise value. e.g. raising `squad_eliminated` weight from −3 to −5 makes wipes hurt more. | dampen specific reactions. Currently `squad_eliminated = −3`, `runner_orphaned = −2`, `player_buy = +1×shares`, etc. |

### Quick "I want X" recipe

| Symptom | First knob to try |
|---|---|
| Prices drift to crazy multiples of valuation | raise `ANCHOR_STRENGTH` toward 0.08–0.10 |
| Prices feel static / boring | raise `DELTA_MULTIPLIER` |
| Every week looks the same regardless of performance | lower `NOISE_RANGE` |
| Quarterly reports feel meaningless | raise `VALUATION_CR_PER_COUNTER` |
| Companies collapse too aggressively | raise `BASE_EXPECTATION` (recalibrate with stronger seed clustering, see §12) OR raise `PRICE_FLOOR` |
| Player capital doesn't feel meaningful | raise the `player_buy` weight in `valuation_delta_for_event` from `+1×shares` to `+2×shares` |

**Important:** the levers are *not* fully independent. `BASE_EXPECTATION` and
`EXPECTED_DELTA_RANGE` should always be re-derived together by re-running
calibration (§12); hand-editing one without the other will skew the formula.
Everything else is genuinely one-at-a-time tunable.

---

## 10c. Observed dynamics (cross-seed validation)

These are empirically-observed patterns from 24-week simulations across
multiple seeds. They describe *how the system behaves in play*, not
*how it's implemented* — useful for setting expectations and recognising
whether a change has produced an intended shift.

### Winner-take-most by default

Across 5 cross-seed 24-week runs with a "diversified investor" strategy
(buy all four at week 0, rebalance at 6/12/18, take profits late), the
outcome was:

| Seed | Player return | Runaway winner | Companies collapsed (0 runners) |
|---|---|---|---|
| 2026 | +126% | CyberAcme (+747% price) | (1 mid-run, recovered) |
| 42 | +151% | Sekiguchi (+703%) | 1 |
| 7 | +422% | Traxus (+1463%) | 1 |
| 100 | +26% | none (peak +57%) | 1 |
| 2027 | +748% | NuCaloric (+2407%) | 0 |

Patterns:
- **In 4 of 5 seeds, exactly one company priced +700% to +2400%** — and a
  *different* company each time. Early roster luck snowballs through the
  closed-pool economy: the lucky company hoards veteran free agents,
  funds payroll, keeps extracting. Losers can't compete and decay.
- **3 of 5 seeds had at least one company collapse to 0 runners** by week
  24. Starting price does not correlate with survival — CyberAcme (the
  highest-starting-price company) collapsed in 2 of 4 seeds where it
  didn't win.
- **No company is destined to win.** The winner-take-most pattern is real
  but its *identity* is genuinely path-dependent on early stochastic
  events.

### Player-strategy take-aways

- **Diversified entry at week 0 is robust.** Always returned positive,
  ranged +26% to +748% across seeds. Concentrating in a single company
  has 3-in-4 odds of picking wrong based on these runs.
- **Price-to-valuation gaps signal mean-reversion risk.** Winners ended
  with price/fair_value ratios of 6× to 15× — those positions will
  mean-revert hard on the next bad week as the anchor activates fully.
  Take profits as the gap widens.
- **Valuation drift is much smaller than price drift on winners.** Across
  the runs, winning companies' valuations grew only +14% to +26% while
  their prices grew +700% to +2400%. That ratio *is* the anchor's
  rubber-band tension — it doesn't snap prices back instantly, but it
  guarantees they can't drift forever.

### Health-check criteria for re-balancing tunes

If you change a lever and re-run the cross-seed scenario, watch for:

- **Performance dominance ratio** (mean|performance_pct| / mean|anchor_pct|).
  Should be **≥ 2** week-to-week — performance is the dominant term by
  design. If it drops below 2, the anchor is overwhelming the surprise term.
- **Runaway price ratio** (final price / fair_value of winners). With
  `ANCHOR_STRENGTH = 0.05`, this can land at 5×–15× over 24 weeks. Raising
  the strength to 0.10 should bring it closer to 2×–4×.
- **Collapse rate** (companies at 0 runners by week 24). At current tuning,
  ~60% of seeds produce one collapse — the closed-pool economy genuinely
  bites. If this drops to 0% the economy may be too forgiving; if it climbs
  to 100% it's too punishing.
- **Player return spread** (min vs max across seeds). +25% to +748% is wide.
  If a tuning change makes returns more uniform across seeds, the system has
  become *less* path-dependent — that's a design choice, not necessarily a
  bug.

---

## 11. Constants reference

Every tunable, its current value, and its home. **Keep this table in sync.**

### `marathon_market.py`
| Constant | Value | Meaning |
|---|---|---|
| `STARTING_CREDITS` | 10 000 | player's starting cash |
| `STARTING_COMPANY_BUDGET` | 600 | each company's starting operating budget |
| `PLAYER_BUY_TO_BUDGET_RATIO` | 1.0 | fraction of a share purchase that funds the company budget |
| `PLAYER_SELL_CLAWBACK_RATIO` | 0.0 | fraction of a share sale clawed back from budget (inert by design) |
| `QUARTERLY_REPORT_WEEKS` | 12 | weeks between valuation reports |

### `runner_sim/market/pricing.py`
| Constant | Value | Meaning |
|---|---|---|
| `BASE_EXPECTATION` | **120.0** | per-squad expected extraction (= median ÷ 3 of active company-weeks; recalibrated under AI loop, see §12). Was 408.83 under the pre-AI fixed-roster model. |
| `EXPECTED_DELTA_RANGE` | **826.0** | weekly stddev of per-company credits (recalibrated). Wider than the old 634.06 — variable rosters introduce more variance. |
| `DELTA_MULTIPLIER` | 10.0 | stretches normalized delta into a ±% range |
| `NOISE_RANGE` | 2.0 | weekly ±% random noise on the performance term |
| `PRICE_FLOOR` | 1.0 | minimum stock price |
| `ANCHOR_STRENGTH` | **0.05** | weekly mean-reversion fraction toward fair_value (~14-week drift-correction half-life) |
| `STARTING_VALUATION` | 5 000 | each company's starting enterprise valuation (single source of truth; re-exported by `marathon_market.py`) |
| `VALUATION_CR_PER_COUNTER` | 20.0 | credits per counter-score point at the quarterly report |

### `runner_sim/market/shell_market.py`
| Constant | Value | Meaning |
|---|---|---|
| `BASE_SHELL_PRICE` | 200.0 | price of a shell at fair-share adoption |
| `SHELL_PRICE_SENSITIVITY` | 4.0 | `k` — steepness of price vs. over/under-adoption |
| `N_SHELLS` | 7 | shell count (fair share = 1/7) |

### `runner_sim/market/company_strategy.py`
| Constant | Value | Meaning |
|---|---|---|
| `BASE_UPKEEP` | 15.0 | flat per-runner weekly cost |
| `UPKEEP_PER_NET_LOOT` | 0.015 | upkeep added per career credit extracted |
| `UPKEEP_PER_ELIM` | 1.5 | upkeep added per career elimination |
| `UPKEEP_PER_DEPLOYMENT_SURVIVED` | 2.5 | upkeep added per week survived |
| `CREDIT_SHARE_TO_COMPANY` | 0.30 | fraction of extraction credits routed to the company budget |
| `ORPHAN_RETIRE_AFTER_WEEKS` | 8 | idle weeks before a free agent retires |
| `MIN_GLOBAL_POOL` | 42 | global runner-count floor; rookies spawn below it |
| `INITIAL_FREE_AGENT_BENCH` | 8 | free agents seeded at bootstrap |
| `STRUGGLE_MA_WINDOW` | 3 | weeks of price history for the health signal |
| `STRUGGLE_THRESHOLD` | 0.97 | price/MA below this → "struggling" |
| `THRIVE_THRESHOLD` | 1.03 | price/MA above this → "thriving" |

### `runner_sim/market/roster.py` / `deployment.py`
| Constant | Value | Meaning |
|---|---|---|
| `STARTING_ROSTER_SIZE` | 9 | full roster (3 squads × 3) |
| `RECRUIT_ALLOWANCE` | 250.0 | fresh recruit's starting wallet |
| `MIN_ROSTER_FOR_DEPLOYMENT` | 6 | below this a company sits the week out |

### `runner_sim/runners.py`
| Constant | Value | Meaning |
|---|---|---|
| `RUNNER_WEIGHT` / `SHELL_WEIGHT` | 0.6 / 0.4 | blend of runner attrs vs. shell affinity in effective capability |
| `AFFINITY_PER_WEEK` | 0.05 | shell affinity gained per surviving week |
| `AFFINITY_CAP` / `AFFINITY_FLOOR` | 1.0 / 0.2 | affinity-score clamp range |
| `ATTRIBUTE_DRIFT_RATE` | 0.05 | EMA step of runner attrs toward current shell |

### `data/zones.csv` / `data/items.csv`
| Data | Value |
|---|---|
| Zone difficulty | Perimeter 0.1 · Dire Marsh 0.3 · Outpost 0.5 |
| Zone pool size | Perimeter 12 · Dire Marsh 10 · Outpost 8 |
| Item tier values | Common 10 · Uncommon 10–50 · Rare 50–300 · Epic 1000 |

---

## 12. Calibration note

`BASE_EXPECTATION` and `EXPECTED_DELTA_RANGE` are **empirically derived** by
`runner_sim/market/calibration.py:headless_calibration` (1000-week run,
seed=42). Re-run calibration and update those two constants whenever zone
count, roster size, item catalog, or the credit-generation path changes
meaningfully — otherwise the stock price formula drifts off-centre.

### How calibration runs (`valuation-anchored-pricing` branch and later)

The calibration loop runs `simulate_week` **with the company-AI loop ENABLED**
(real variable-roster steady state — budgets, payroll, free-agent pool,
bidding) but **WITHOUT the anchor term** (no `anchor_inputs` passed → anchor
contribution is exactly 0.0). This split is deliberate:

- AI loop on → calibrates against the real distribution the live game sees,
  including death spirals and under-strength rosters.
- Anchor off → measures the *pure performance signal*, so the constants stay
  cleanly separable from however the anchor is tuned. Re-tuning
  `ANCHOR_STRENGTH` never invalidates the calibration.

A local `_CalibCompany` dataclass stands in for `marathon_market.Company` so
calibration has no upward import.

### Median, not mean

`headless_calibration` returns `(median, stdev)` and the caller pastes
`BASE_EXPECTATION = median / 3`. Loot is heavily **right-skewed** — rare
1000cr Epics pull the mean far above the median. If the mean were used, the
typical week's extraction would always sit below the baseline → systematic
negative price drift on every typical week. The median is what makes a
typical week land at *zero* delta, neutral price move.

### Active company-weeks only

Sat-out weeks (`squads_deployed == 0`) are **excluded** from the distribution.
Including them would double-count the under-deployment penalty: a sat-out
company already gets `delta = −baseline` in the live formula (because
`squads_deployed=3` baseline still applies), so factoring "expected zero"
into the calibrated baseline collapses the mean and produces a runaway-loose
formula.

### When to recalibrate

The numbers depend on a feedback loop (constants → AI decisions → roster
dynamics → extraction distribution → constants). Iterate until median
stabilises. Across seeds (42, 7, 100, 2027), median clusters at 350–410
extracted-cr per company-week, with one outlier at 830 — using seed=42's
value of 360 (so BASE_EXPECTATION = 120) is the current canonical setting.

### One-shot recalibration command

```
uv run python -c "from runner_sim.market.calibration import headless_calibration; m,s=headless_calibration(); print('BASE_EXPECTATION', m/3, 'EXPECTED_DELTA_RANGE', s)"
```

---

## 13. Maintenance checklist

When you touch the economy, update this doc in the same commit:

- [ ] Changed a formula? Update the relevant section (§3–§9).
- [ ] Added/changed a constant? Update §11 and the owning section.
- [ ] Added a new value system or money flow? Add a section, update §1, §2, §10.
- [ ] Added a new valuation event? Update the §6 event table and the
      `valuation_delta_for_event` entry in §10b.
- [ ] Tuned a price/anchor/valuation knob? Note the rationale in §10b
      ("Tuning levers"); re-run the cross-seed scenario in §10c and confirm
      the health-check criteria still hold (or update them deliberately).
- [ ] Re-ran calibration? Update `BASE_EXPECTATION` / `EXPECTED_DELTA_RANGE`
      in §11 and the canonical median note in §12.
- [ ] Bump the "Last verified against the code" line at the top.
