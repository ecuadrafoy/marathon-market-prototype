# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the simulator (Textual TUI)
uv run python marathon_market.py

# Run in console mode — plain text, no TUI; useful for debugging game logic
uv run python marathon_market.py --console

# Run with debug mode (reveals all hidden zones in results)
uv run python marathon_market.py --debug
uv run python marathon_market.py --console --debug

# Run with AI tracing — every BT extract/engage decision is printed
uv run python marathon_market.py --trace-ai
uv run python -m runner_sim.zone_sim.harness --seed 42 --trace-ai

# Generate EV/yield/success analysis charts (saves success_rate_chart.png)
uv run python charts.py

# Run the test suite
uv run pytest

# Behaviour-tree workflow (see "AI behaviour trees" section below)
uv run python -m ai_tree.server                         # launch the visual editor at http://localhost:8765/
uv run python scripts/publish_tree.py <tree_name>       # validate + publish a draft tree (CLI alternative)
```

Tests live in `tests/`. Run `uv run pytest` before every commit. Manual simulator
runs validate feel/equilibrium that unit tests can't capture.

## Architecture

`marathon_market.py` is the entry point — Textual TUI (`marathon_market_tui.py`)
or console (`marathon_market_console.py`). It owns the player-facing layer only:
`GameEngine`, the `Company`/`Portfolio`/`GameState` dataclasses, the buy/sell
actions, and the quarterly valuation report. All *simulation* logic lives under
`runner_sim/`. `charts.py` imports from `runner_sim` for analysis only.

> **Economy reference:** `docs/economy.md` is the single living document for
> every price/value system and the maths behind it (stock price, operating
> budget, valuation, shell market, runner wallets, loot credits, and how they
> interlink). Treat it as the source of truth for anything economy-related,
> and update it in the same commit as any economy change.

**Week pipeline** — `GameEngine.advance_week` (`marathon_market.py`) calls
`simulate_week` (`runner_sim/market/week.py`), which orchestrates one week:

1. **Deploy** — `assign_squads` (`market/deployment.py`) chunks each company's
   `CompanyRoster` into 2–3 squads and assigns one per zone. Rosters below
   `MIN_ROSTER_FOR_DEPLOYMENT` (6) sit the week out.
2. **Run zones** — `run_zone` (`zone_sim/sim.py`) per zone: a tick loop where
   all squads share one finite loot pool. Per tick — explore → encounter →
   engage/disengage → combat → kill-loot → extraction decision. Per-doctrine
   extract/engage choices are driven by behaviour trees (see "AI behaviour
   trees" below).
3. **Per-runner state** — `_update_runners_for_squad` / `apply_zone_outcome`
   mutate each `Runner` in place: career stats, `credit_balance`, shell
   affinity, attribute drift, and the death sentinel.
4. **Aggregate** — `_build_company_result` rolls each company's squads into a
   `CompanyWeekResult`, computing the price delta via `compute_baseline` +
   `compute_price_change_pct` (`market/pricing.py`).
5. **Route the dead** — eliminated runners go to the closed free-agent pool
   (AI mode) or are replaced with fresh hires (calibration mode).
6. **Company-AI cycle** — only when `companies`/`free_agents`/`id_supplier` are
   passed. Runs *after* deployment so decisions react to the week's actual
   outcome: income → payroll → voluntary drops → free-agent ageing → bidding
   draft → re-equip. All in `market/company_strategy.py`.
7. **Shell market** — `reequip_survivors` then `update_prices`
   (`market/shell_market.py`) recompute shell prices from the new roster
   composition.

`advance_week` then writes `price_after` back onto each `Company.price`,
accrues per-event valuation counter scores, and fires the quarterly valuation
report every `QUARTERLY_REPORT_WEEKS` (12).

**Runners are persistent identities.** A `Runner` lives on a `CompanyRoster`
across weeks, accumulating career stats, shell affinity, and a `credit_balance`
wallet. Squads are an ephemeral weekly grouping, not an identity — there is no
"runner assignment mode". `assign_squads` is the single deployment override
point (player-controlled deployment is a future hook).

**Information asymmetry is core to the design.** The player sees only the
monitored zone (Perimeter — `zone.monitored`). Stock prices move on
`total_credits_extracted` across *all three zones*, so the visible zone is an
intentionally weak signal — price surprises come from hidden zones.
`CompanyWeekResult` carries both `monitored_*` fields (player intel) and
aggregate fields (the actual price driver) to keep this split explicit.

**Credits originate as loot.** Squads extract `Item`s (each with a flat
`credit_value`) from finite per-zone pools defined in `data/items.csv` and
`data/zones.csv`; a squad's take is `Squad.loot.total_credits()`. Everything
downstream — stock price, budgets, wallets, valuation — is a transformation of
that weekly credit stream. The stock-price formula is
`delta = total_credits_extracted − (BASE_EXPECTATION × squads_deployed)`,
normalized by `EXPECTED_DELTA_RANGE`; `squads_deployed` is passed as
`len(zones)` (3), so an under-strength roster is punished automatically. See
`docs/economy.md` §3 for the loot model and §4 for the pricing maths.

**Tunable constants** are no longer in one place — each subsystem owns its
constants (`pricing.py`, `shell_market.py`, `company_strategy.py`,
`deployment.py`, `runners.py`), plus a player/valuation block at the top of
`marathon_market.py`. `docs/economy.md` §11 is the consolidated reference.
`BASE_EXPECTATION` (408.83) and `EXPECTED_DELTA_RANGE` in `pricing.py` are
empirically derived by `market/calibration.py:headless_calibration` — re-run it
if zone count, roster size, the item catalog, or the credit path changes
significantly.

## Key design constraints

- The **loot table system** is live: discrete `Item`s with tiered `credit_value`s drawn from finite per-zone pools (`data/items.csv`, `data/zones.csv`). It replaced the old continuous yield curve — there is no `resolve_runner`/`yield_value` path anymore. Item generation and the rarity bands are still being tuned; the data files are the tuning surface.
- A `Runner`'s capability is a three-axis composition — `combat`, `extraction`, `support` (career attributes that sum to 1.0) — blended with shell affinity into `effective_capability`. The old single composite `skill` float is gone; formulas operate on the per-axis vector.
- Non-traded factions (MIDA, Arachne, UESC) and multi-zone monitoring are out of scope for this prototype.

## AI behaviour trees

Per-doctrine extraction and engagement decisions are driven by **behaviour trees**
stored as JSON and validated through a publish gate before the simulator can load
them. The system has four components:

- **Engine:** `ai_tree/` — registry, composites (Sequence/Selector/Inverter), JSON
  loader, and the publish gate. Engine code is game-agnostic.
- **Game leaves:** `runner_sim/zone_sim/ai_conditions.py` — `@bt_condition`-decorated
  Python functions like `HasUncommonLoot`, `CombatRatioAbove`. Each is a pure check
  over a `Context` object. Adding a leaf = writing a function with the decorator;
  it auto-registers on import.
- **Trees:** `ai_trees/drafts/<name>.json` (work-in-progress) and
  `ai_trees/published/<name>.json` (validated, runtime-loadable). One tree per
  doctrine, per decision kind (`extraction_<doctrine>` / `encounter_<doctrine>`).
- **Manifest:** `ai_trees/manifest.json` records each published tree's SHA256.
  The runtime refuses to load anything missing or with a checksum mismatch — so
  drafts can never silently leak into a sim run.

### Tree JSON schema (nested)

```json
{
  "name": "EncounterBalanced",
  "root": {
    "type": "selector",            // sequence | selector | inverter | leaf
    "label": "BalancedEngageDecision",
    "children": [                  // composites
      {"type": "leaf", "id": "OpponentHelpless"},
      {"type": "sequence", "children": [...]},
      {"type": "inverter", "child": {...}},
      {"type": "leaf", "id": "CombatRatioAbove", "params": {"threshold": 1.2}}
    ]
  },
  "_layout": {...}                 // optional editor sidecar; runtime ignores
}
```

### Authoring workflow

```
1. uv run python -m ai_tree.server
   - Opens an HTTP server at http://localhost:8765/ serving the
     visual editor (vanilla HTML + LiteGraph.js).
   - Pick a tree from the dropdown, click Load, edit visually,
     click Save Draft and then Publish. Diagnostics appear in
     the right sidebar inline. Stop the server with Ctrl+C.
   - "New Tree" button → modal with kind dropdown (extraction |
     encounter) + doctrine dropdown. Both are strict — they're
     populated from the runtime's actual vocabulary, so you can
     only author trees the dispatcher can actually run. Creates
     an empty Selector seed in ai_trees/drafts/<name>.json.
   - "New Leaf" button → form for authoring a new @bt_condition.
     Generates a Python file under runner_sim/zone_sim/user_leaves/
     and reloads the registry without restarting the server. The
     palette refreshes automatically; new leaves appear under the
     category you chose.
   - "Refresh palette" reloads /catalog manually — useful if you
     added a leaf by hand-editing user_leaves/ rather than via the
     New Leaf form.

2. CLI alternative for batch / scripted publishing:
   uv run python scripts/publish_tree.py <name>
   - if outputs unchanged from snapshot → publishes (copies to
     published/, updates manifest.json with the new SHA256)
   - if outputs changed → fails, prints which inputs differ
   - rerun with --update-snapshot to bless the new behaviour

3. uv run pytest && uv run python marathon_market.py
   - tests/test_ai_tree_parity.py guards against snapshot drift in CI.
```

### Adding a new doctrine

Doctrines aren't free-text in the New Tree modal — they're a strict
dropdown sourced from `Doctrine` enum values. That's because the
runtime dispatches by enum value, and the enum itself is downstream
of `SHELL_DOCTRINE`: every doctrine has to be reachable by some
shell, otherwise no squad would ever trigger its tree.

So the authoring order is enforced: **shell taxonomy → SHELL_DOCTRINE
mapping → Doctrine enum → tree files**. Adding a doctrine means:

1. Decide which existing shells map to it (or add a new shell type
   to `runner_sim/shells.py`).
2. Add the enum value to `Doctrine` in `extraction_ai.py`.
3. Update `SHELL_DOCTRINE` so at least one shell points at it.
4. Restart the editor server. The new doctrine appears in the
   New Tree dropdown automatically; author the tree and publish.

Steps 1–3 require code review; step 4 is data-only.

The publish gate runs four checks: schema validity, leaf-ID resolution, smoke
load, and a snapshot-diff over a fixed grid (160 inputs for extraction trees,
75 for encounter trees). Snapshots live next to each published tree as
`<name>.snapshot.json` and are the durable behavioural spec.
