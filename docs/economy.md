# The Marathon Market Economy

**Living document.** This is the single reference for every price, value, and
credit flow in the simulation, and how they interlink. Whenever a formula,
constant, or money path changes, update this file in the same commit.

Last verified against the code: `ai-companies` branch (PR #7).

---

## 1. The five value systems at a glance

The economy has five distinct quantities, each with its own update cadence,
its own driver, and its own "memory" behaviour:

| System | Lives on | Cadence | Driver | Stateful? |
|---|---|---|---|---|
| **Loot credits** | `Squad.loot` | per zone-run | items extracted from zone pools | no (regenerated each week) |
| **Stock price** | `Company.price` | weekly | extraction performance vs. baseline | **yes** (`price × (1+pct)`) |
| **Operating budget** | `Company.budget` | weekly | 30% of extraction credits − payroll − bids + player buys | **yes** (running balance) |
| **Valuation** | `Company.valuation` | every 12 weeks | accumulated event "counter score" | **yes** (released quarterly) |
| **Shell price** | `ShellMarket.prices` | weekly | adoption share across all rosters | **no** (recomputed from scratch) |
| **Runner wallet** | `Runner.credit_balance` | weekly | extraction share − shell purchases | **yes** (running balance) |

The stateless/stateful split matters: the **shell market self-heals** every
week (no momentum to unwind), while the **stock market, budgets, valuation,
and wallets drift** — they carry their history forward.

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
        ├──────────────────────────► STOCK PRICE
        │   performance vs baseline      Company.price  (pricing.py)
        │
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
              Company.valuation  (marathon_market.py)
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

The market scores each company on how its total extraction compares to a
calibrated expectation, then nudges the share price by a bounded percentage.

```
performance     = total_credits_extracted          (all squads, all zones)
baseline        = BASE_EXPECTATION × squads_deployed
delta           = performance − baseline
normalized      = delta / EXPECTED_DELTA_RANGE
price_change_%  = normalized × DELTA_MULTIPLIER + uniform_noise(±NOISE_RANGE)
price_after     = max(price_before × (1 + price_change_% / 100), PRICE_FLOOR)
```

- `squads_deployed` is passed as `len(zones)` (= 3), **not** the actual squad
  count. A company that fields fewer than 3 squads (an under-strength roster)
  is still measured against the full 3-squad expectation — so under-deployment
  is punished automatically. See `_build_company_result` in `week.py`.
- The price is **stateful**: it compounds off `price_before`, so a run of bad
  weeks drags the baseline down with it.
- `PRICE_FLOOR = 1.0` — price never hits zero.

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

**File:** `marathon_market.py` · **Cadence:** every 12 weeks · **Stateful, released quarterly.**

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
- **Events → valuation, on a delay.** Every squad outcome and roster change
  silently accrues a counter score; the consequence only surfaces at the
  quarterly report. A company can have a great quarter "in secret".
- **Player capital is a two-edged lever.** Buying funds operations *now* and
  accrues valuation for *later*; selling damages valuation only. The player is
  a participant in the same economy the AI plays.

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
| `STARTING_VALUATION` | 5 000 | each company's starting enterprise valuation |
| `QUARTERLY_REPORT_WEEKS` | 12 | weeks between valuation reports |
| `VALUATION_CR_PER_COUNTER` | 20.0 | credits per counter-score point at the report |

### `runner_sim/market/pricing.py`
| Constant | Value | Meaning |
|---|---|---|
| `BASE_EXPECTATION` | 408.83 | per-squad expected extraction (calibrated) |
| `EXPECTED_DELTA_RANGE` | 634.06 | typical weekly stddev of per-company credits (calibrated) |
| `DELTA_MULTIPLIER` | 10.0 | stretches normalized delta into a ±% range |
| `NOISE_RANGE` | 2.0 | weekly ±% random noise on price change |
| `PRICE_FLOOR` | 1.0 | minimum stock price |

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
`runner_sim/market/calibration.py:headless_calibration` (1000-week run, fixed
seed). Re-run calibration and update those two constants whenever zone count,
roster size, item catalog, or the credit-generation path changes meaningfully
— otherwise the stock price formula drifts off-centre.

Note: calibration mode runs `simulate_week` **without** the company-AI loop
(no budget, no payroll, no free-agent pool) and keeps the legacy
auto-replace-on-death behaviour, so the calibrated constants are unaffected by
the `ai-companies` systems.

---

## 13. Maintenance checklist

When you touch the economy, update this doc in the same commit:

- [ ] Changed a formula? Update the relevant section (§3–§9).
- [ ] Added/changed a constant? Update §11 and the owning section.
- [ ] Added a new value system or money flow? Add a section, update §1, §2, §10.
- [ ] Added a new valuation event? Update the §6 event table.
- [ ] Re-ran calibration? Update `BASE_EXPECTATION` / `EXPECTED_DELTA_RANGE` in §11.
- [ ] Bump the "Last verified against the code" line at the top.
