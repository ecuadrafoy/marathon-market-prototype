# Strategy Status — Empirical Playtest Findings

**Living document.** This file tracks what the simulation's *current* economy
actually rewards — dominant strategies, known exploits, and the empirical
shape of player returns. Update it whenever a playtest reveals a new
strategic pattern, or whenever a code change is intended to close a loophole
captured here.

Treat this as the operational counterpart to `docs/future_design.md`:
- `future_design.md` describes systems we *intend* to add or change.
- `strategy_status.md` describes what the live game *currently does*, so we
  can tell whether a planned change actually moved the dial.

Last verified against the code: `master` after the company-memory-loop
feature (commit `e112166`).

---

## 1. Dominant strategy: all-in at week 0, hold ~36 weeks, sell

The current dominant player strategy is to spend the full starting
`STARTING_CREDITS` (10 000 cr) across all four companies at week 0 (the
`/all-in` action in the TUI), do nothing for ~36 weeks, then exit.

### 1.1 Measured ROI across 20 seeds

Same starting allocation (5 CyberAcme + 6 Sekiguchi + 8 Traxus + 12 NuCaloric
≈ 9 330 cr invested, 670 cr cash left), no further trades, four holding
horizons:

| Holding period | Mean ROI | Median ROI | Min   | Max     | Win rate (>0) | 2× rate |
|----------------|---------:|-----------:|------:|--------:|--------------:|--------:|
| 24 weeks       |   +102 % |      +74 % |  +9 % |  +323 % |     20 / 20   |  6 / 20 |
| **36 weeks**   | **+231 %** | **+161 %** | **+22 %** | **+1 337 %** | **20 / 20** | **14 / 20** |
| 52 weeks       |    +34 % |      -17 % | -32 % |  +382 % |      7 / 20   |  3 / 20 |
| 78 weeks       |    -59 % |      -60 % | -62 % |   -47 % |      0 / 20   |  0 / 20 |

Run with `random.seed(seed)` for seeds 1–20, `GameEngine.do_all_in()` at
week 0, then `advance_week()` × N. Reproduce with the harness inlined at
the bottom of this doc.

### 1.2 The shape

- **Win rate is 100 % through week 36.** Not "high" — 100 %, across 20 seeds.
- **The mean dwarfs the median by ~1.5 ×.** Every horizon has a runaway
  winner (seed 13 reached +1 337 % at week 36); all-in guarantees exposure
  to whichever company that turns out to be.
- **There's a cliff between weeks 36 and 52.** Hold beyond week 52 and
  median return turns negative. By week 78 every seed has lost money.

So the strategy is not "buy and hold forever" — it's "buy at week 0,
exit before week 52." The sell window is the actual skill.

---

## 2. Why this works — root causes

Three independent mechanics combine to make all-in dominant:

### 2.1 Asymmetric valuation accounting

`do_buy` fires a `+1 / share` event into `pending_valuation_delta`
([marathon_market.py:404-425](../marathon_market.py:404)). Selling fires
`-1 / share`. *Holding* fires nothing. Without a sell action, the buy
events keep echoing into every subsequent quarterly report — the
valuation anchor reads an elevated `projected_valuation` and the price
formula pulls weekly prices toward `fair_value = anchor_price ×
(projected / STARTING_VALUATION)` ([pricing.py:114-141](../runner_sim/market/pricing.py:114)).

The only counter-pressure on a passive position is `loan_overdue` (-5
per quarter per outstanding loan) and `week_inactive` (-1 per quiet
week). For a company that survives operationally, these don't accrue
fast enough to offset the player's positive buy signal.

### 2.2 No rival capital

The player is the entire investor class. No AI buyers fade an
overpriced position; no AI sellers cap an underpriced one. The week-0
buy is the only signal `pending_valuation_delta` ever sees from the
investor side. See `docs/future_design.md` § *AI Investor Crowd* — that
note exists precisely because of this gap.

### 2.3 Winner-take-most concentration

Cross-seed observation: in every seed at least one company ends the
36-week mark at 5 ×–10 × its starting price, while others drift sideways
or down. The all-in allocation gives the player shares in *every*
company, so the runaway winner's contribution dominates portfolio value.
Single-company concentration would 5–10 × returns sometimes but would
also sometimes wipe out — all-in eliminates that wipe risk while keeping
the winner's tailwind.

### 2.4 The cliff

The week-50ish death spiral (1 / 4 companies still deployable at week
50, documented in `future_design.md` § *AI Investor Crowd* §
*The concrete economic gap*) finally hits portfolio value once the
operationally-dead companies' `pending_valuation_delta` exhausts and the
anchor flips sign — fair_value drops below price, and the weekly anchor
pull becomes negative every week.

---

## 3. What would close this

The doc already captures the structural fix in two notes. Either alone
would help; the two together would close the loophole:

- **AI Investor Crowd** (`future_design.md` § *AI Investor Crowd*) —
  rival agents pricing in the player's week-0 signal so it doesn't
  echo unchallenged for 36 weeks.
- **Sell-side / hold-side decay** (`future_design.md` § *Valuation:
  Sell-Side Economic Coupling*) — either route the buy-side budget
  injection through the quarterly counter so holding has a cost, or
  let selling drain `Company.budget` so passive positions face a real
  decay.

Neither has shipped. Until one does, the empirical ROI table in §1.1
is the expected behaviour.

---

## 4. What this doc is *not*

- **Not a verdict that the game is broken.** A pattern this strong is
  useful — it tells us exactly which lever to pull next. Closing the
  loophole is on the roadmap, not a fire drill.
- **Not a balance complaint about specific knobs.** The constants
  (`STARTING_CREDITS`, starting prices, `ANCHOR_STRENGTH`,
  `PLAYER_BUY_TO_BUDGET_RATIO`) are not the issue; the *missing
  systems* are. See `docs/tuning_levers.md` for the constants that are
  intentional.
- **Not a permanent table.** The numbers in §1.1 are valid against
  commit `e112166`. Re-measure whenever pricing, valuation, or AI-investor
  systems change.

---

## 5. Reproduction harness

```python
import time, random, statistics
time.sleep = lambda *a, **k: None     # bypass SIM_PAUSE_SECS

from marathon_market import GameEngine, STARTING_CREDITS

def run_one(seed: int, weeks: int) -> float:
    random.seed(seed)
    eng = GameEngine(debug=False)
    eng.do_all_in()
    for _ in range(weeks):
        eng.advance_week()
    p = eng.state.portfolio
    holdings_value = sum(
        p.holdings.get(c.name, 0) * c.price for c in eng.state.companies
    )
    return 100.0 * (p.credits + holdings_value - STARTING_CREDITS) / STARTING_CREDITS

for weeks in (24, 36, 52, 78):
    rois = [run_one(s, weeks) for s in range(1, 21)]
    print(f"{weeks:3d}wk  mean={statistics.mean(rois):+7.1f}%  "
          f"median={statistics.median(rois):+7.1f}%  "
          f"win={sum(1 for r in rois if r>0)}/{len(rois)}")
```

About 90 s on a laptop. Run via `uv run python -c '...'`.

---

## 6. Change log

| Date | Commit | What changed | Effect on dominant strategy |
|------|--------|--------------|-----------------------------|
| 2026-05-17 | `e112166` | Company memory loop + per-zone deployment bias | Survival-rate at week 30 slightly improved; ROI curve shape unchanged — peak still at ~36 weeks, cliff still at ~52. Memory adjusts *which* companies survive, not whether passive holding pays. |
