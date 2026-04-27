# Marathon Market Simulator

A console-based market simulation set in the Marathon universe. Players trade stocks in
runner companies — bio-synthetic operatives deployed across zones of varying difficulty.
Stock prices move on weekly runner performance across all zones, but players can only
monitor one zone directly, creating an information asymmetry at the heart of the game.

## Setup

Requires [uv](https://github.com/astral-sh/uv).

```bash
uv sync
```

---

## Scripts

### `marathon_market.py` — Main game

The interactive market simulator. Trade shares across four companies, observe your
monitored zone each week, and try to beat the market over multiple weeks.

```bash
uv run python marathon_market.py
```

| Flag | Description |
|------|-------------|
| `--debug` | Reveals all hidden zone results after each week. Useful for understanding how hidden zone performance drives price swings. |

---

### `charts.py` — Economy analysis charts

Generates four analysis charts and saves them as `success_rate_chart.png`:
- Success rate by runner skill and zone
- Yield on success by runner skill and zone
- Expected value with congestion bands
- Congestion factor decay curve

```bash
uv run python charts.py
```

No flags. Output is saved to `success_rate_chart.png` in the project root.

---

### `runner_sim` — Runner ecosystem harness

A standalone simulation of the runner lifecycle — squad formation, combat resolution,
extraction, shell affinity, and attribute drift over a multi-week career. Used to
validate that veteran runners outperform novices and that specialization emerges from
shell exposure.

```bash
uv run python -m runner_sim
```

| Flag | Default | Description |
|------|---------|-------------|
| `--weeks N` | `25` | Number of weeks to simulate. |
| `--pool N` | `30` | Number of runners in the persistent pool. |
| `--seed N` | *(none)* | Random seed for reproducible runs. |
| `--quiet` | off | Suppress per-week encounter logs. Shows only the final leaderboard and summary stats. |
| `--print-pool` | off | Print the full initial runner roster (attributes, shell, alignment score) before week 1. |
| `--print-history` | off | Print each runner's per-week shell timeline as a single-letter code string after the leaderboard. |

**Example — reproducible quiet run with history:**
```bash
uv run python -m runner_sim --weeks 40 --pool 30 --seed 42 --quiet --print-history
```

---

### `headless_calibration()` — BASE_EXPECTATION recalibration

Runs a headless 1000-week simulation and returns the average performance score used to
set `BASE_EXPECTATION` in `marathon_market.py`. Re-run this whenever `TOTAL_RUNNERS`,
zone count, `RUNNER_SKILL_MEAN/SD`, `YIELD_STEEPNESS`, or `CONGESTION_K` change.

```bash
uv run python -c "from marathon_market import headless_calibration; print(headless_calibration())"
```

Update `BASE_EXPECTATION` in `marathon_market.py` with the printed value.

---

## Documentation

Design notes and analysis live in [`docs/`](docs/):

| File | Contents |
|------|----------|
| [`marathon_market_prototype_spec.md`](docs/marathon_market_prototype_spec.md) | Full prototype specification |
| [`yield_design.md`](docs/yield_design.md) | EV analysis behind the yield formula and zone balance |
| [`runner_design.md`](docs/runner_design.md) | Runner attribute system and shell affinity design |
| [`outcomes_design.md`](docs/outcomes_design.md) | Outcome calculation: drift, kills, success, yield |
| [`combat_ideas.md`](docs/combat_ideas.md) | Potential improvements to the combat resolution system |
