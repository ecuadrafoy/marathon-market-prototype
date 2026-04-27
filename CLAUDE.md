# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the simulator
uv run python marathon_market.py

# Run with debug mode (reveals all hidden zones in results)
uv run python marathon_market.py --debug

# Generate EV/yield/success analysis charts (saves success_rate_chart.png)
uv run python charts.py
```

No test suite exists yet. Validate changes by running the simulator for several weeks and observing market behavior.

## Architecture

All simulation logic lives in `marathon_market.py`. `charts.py` imports from it for analysis only.

**Data flow each week:**
1. `assign_runners_standard` or `assign_runners_skill_matched` → `list[Runner]` (runners generated with zone + company assignment but not yet resolved)
2. `compute_company_result` calls `resolve_runner` on each runner in place, mutating `Runner.success` and `Runner.yield_value`
3. `_compute_baseline` and `_compute_price_change_pct` translate company performance into a price delta; `Company.price` is mutated directly
4. `planning_loop` / `print_results` handle all player-facing I/O

**Two runner assignment modes:**
- **Standard** — runners distributed randomly across zones, then randomly to companies within each zone
- **Skill-matched** — all skills generated upfront, sorted descending; each runner is weighted toward harder zones based on `zone.difficulty × runner.skill`. This creates a correlation between zone difficulty and runner quality that the player cannot directly observe (only the monitored zone is visible)

**Information asymmetry is core to the design.** The player sees runner headcounts in Sector 7 (monitored) only. Stock prices move on performance across *all three zones*. The visible zone is intentionally a weak signal — price surprises come from hidden zones. `CompanyWeekResult` carries both `monitored_*` fields (player intel) and aggregate fields (actual price driver) to keep this split explicit.

**Market formula pipeline** (`_compute_price_change_pct`):
- `performance_score = success_rate × average_yield` (per company, all zones)
- `baseline = BASE_EXPECTATION × headcount_factor` — market expectation based purely on runner count, which the player can observe
- `delta = performance_score - baseline`, normalized by `MAX_PERF_SCORE`
- `price_change_pct = (normalized_delta × DELTA_MULTIPLIER) + uniform_noise`

**Yield formula** (in `resolve_runner`):
```python
yield_value = (50 + skill * 100) * (1 + difficulty**2 * 8)
```
The quadratic multiplier is intentional — see `docs/yield_design.md` for the EV analysis that motivated it. The `8` coefficient is the primary tuning lever for zone risk/reward balance.

**Tunable constants** are grouped at the top of `marathon_market.py` under `TUNABLE CONSTANTS`. `BASE_EXPECTATION` (34.4) is empirically derived from a 1000-week simulation — recalibrate it if zone count, runner count, or the yield formula changes significantly.

## Key design constraints

- The continuous yield curve is prototype scaffolding for a future **loot table system** (discrete item rarities per zone). The formula interface — `resolve_runner` returning a `yield_value` float — is stable; the internals will be replaced.
- Runner `skill` is a single composite float intentionally. It will expand into multi-stat compositions later; all formulas treat it as a black box.
- Non-traded factions (MIDA, Arachne, UESC) and multi-zone monitoring are out of scope for this prototype.
