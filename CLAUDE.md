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

All simulation logic lives in `marathon_market.py`. `charts.py` imports from it for analysis only.

**Data flow each week:**
1. `assign_runners_standard` or `assign_runners_skill_matched` → `list[Runner]` (runners generated with zone + company assignment but not yet resolved)
2. `compute_company_result` calls `resolve_runner` on each runner in place, mutating `Runner.success` and `Runner.yield_value`
3. `_compute_baseline` and `_compute_price_change_pct` translate company performance into a price delta; `Company.price` is mutated directly
4. `planning_loop` / `print_results` handle all player-facing I/O

**Two runner assignment modes:**
- **Standard** — runners distributed randomly across zones, then randomly to companies within each zone
- **Skill-matched** — all skills generated upfront, sorted descending; each runner is weighted toward harder zones based on `zone.difficulty × runner.skill`. This creates a correlation between zone difficulty and runner quality that the player cannot directly observe (only the monitored zone is visible)

**Information asymmetry is core to the design.** The player sees runner headcounts in Perimeter (monitored) only. Stock prices move on performance across *all three zones*. The visible zone is intentionally a weak signal — price surprises come from hidden zones. `CompanyWeekResult` carries both `monitored_*` fields (player intel) and aggregate fields (actual price driver) to keep this split explicit.

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
