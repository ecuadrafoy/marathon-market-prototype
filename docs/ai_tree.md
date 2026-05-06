# AI Behaviour Trees

This document describes the behaviour-tree system that drives per-doctrine
extraction and engagement decisions for runner squads. It covers the runtime
engine, the publish gate, the editor, and how to extend the system with new
leaves or tree kinds.

For day-to-day workflow commands, see the "AI behaviour trees" section in
`CLAUDE.md`. For "what can I change in JSON vs what needs a code edit?",
see `docs/ai_tree_authoring.md`. This document is the deeper design
reference.

---

## Why behaviour trees

The original AI lived as inlined `if doctrine == X` branches inside
`should_extract` (extraction_ai.py) and `should_engage` (encounter_ai.py).
Each branch was a small expression like:

```python
if doctrine == Doctrine.CAUTIOUS:
    return loot.has_uncommon() or (perception.had_encounter and loot.items)
```

That format had three problems:

1. **The structure was implicit.** Reading the code you had to mentally build a
   tree from a chain of `or`/`and` operators.
2. **Tuning required a code change.** Changing CAUTIOUS's threshold from
   "Uncommon" to "Rare" meant editing Python, restarting the simulator, and
   trusting that no other test broke.
3. **The shape was fixed across doctrines.** All four doctrines had to fit the
   same boolean expression template, even if some doctrines might want a
   fundamentally different decision shape (e.g., evaluate combat odds first,
   then fall through to loot considerations).

Behaviour trees solve all three: structure becomes data; tuning happens in a
visual editor; each doctrine can have its own tree shape.

---

## Architecture overview

Five layers, each owned by exactly one module.

```
   Layer 1: REGISTRATION  (Python source — single source of truth)
   ────────────────────────────────────────────────────────────────
   @bt_condition(name="HasUncommonLoot", category="Loot")
   def has_uncommon_loot(ctx) -> bool: ...
                         │
                         ▼
   Layer 2: CATALOG  (JSON, served by the editor server)
   ────────────────────────────────────────────────────────────────
   GET /catalog → list of every registered leaf with metadata
                         │
                         ▼
   Layer 3: VISUAL AUTHORING  (browser — LiteGraph.js)
   ────────────────────────────────────────────────────────────────
   ai_trees/drafts/<name>.json    ← visually authored, work in progress
                         │
                         ▼
   Layer 4: PUBLISH GATE  (lint + snapshot regression check)
   ────────────────────────────────────────────────────────────────
   POST /trees/<name>/publish
   → if PASS: copy draft → published/, update manifest
   → if FAIL: print diagnostics, refuse to publish
                         │
                         ▼
   Layer 5: RUNTIME  (Python walker, ~120 LOC)
   ────────────────────────────────────────────────────────────────
   tree = load_tree("ai_trees/published/<name>.json")
   decision = tree.tick(ctx)
```

### Module map

| Layer | Module | Purpose |
|---|---|---|
| 1 | `ai_tree/registry.py` | `@bt_condition` / `@bt_action` decorators; global `REGISTRY` dict |
| 1 | `runner_sim/zone_sim/ai_conditions.py` | The 11 game-specific leaves (extracted from the legacy code) |
| 2 | `ai_tree/server.py` | HTTP endpoints exposing the registry to the editor |
| 3 | `ai_tree_editor/` | Static HTML + LiteGraph.js editor |
| 4 | `ai_tree/publisher.py` | Lint, grid evaluation, snapshot diff, manifest |
| 5 | `ai_tree/composites.py` | `Sequence`, `Selector`, `Inverter`, `Leaf` node classes |
| 5 | `ai_tree/runtime.py` | JSON loader and `Tree.tick()` |
| 5 | `ai_tree/context.py` | The `Context` dict-with-attrs container leaves read from |

The boundaries are strict: `ai_tree/` is engine code that knows nothing about
the runner sim. Game-specific concerns (Doctrine, SquadLoot, Tier) live in
`runner_sim/zone_sim/ai_conditions.py`. This means the engine could host a
completely different game's BT without modification.

---

## Tree JSON schema

Trees are stored as nested JSON in `ai_trees/drafts/<name>.json` (work in
progress) and `ai_trees/published/<name>.json` (validated). Example:

```json
{
  "name": "EncounterBalanced",
  "root": {
    "type": "selector",
    "label": "BalancedEngageDecision",
    "children": [
      {"type": "leaf", "id": "OpponentHelpless"},
      {
        "type": "sequence",
        "label": "HighValueGuarded",
        "children": [
          {"type": "leaf", "id": "CarryingHighValue"},
          {"type": "leaf", "id": "CombatRatioAbove", "params": {"threshold": 1.2}}
        ]
      }
    ]
  },
  "_layout": {
    "5": {"x": 100, "y": 50},
    "6": {"x": 100, "y": 200}
  }
}
```

Schema rules:

| Field | Where | Meaning |
|---|---|---|
| `name` | top-level | Display name (informational; runtime ignores) |
| `root` | top-level | The tree's root node (required) |
| `_layout` | top-level | Editor sidecar — node x/y positions; runtime ignores |
| `type` | every node | One of: `sequence`, `selector`, `inverter`, `leaf` |
| `label` | optional | Human-readable name shown in the editor |
| `children` | composites | Non-empty array of child nodes |
| `child` | inverter | Single child node |
| `id` | leaves | Name of a registered leaf in the catalog |
| `params` | leaves, optional | Per-instance parameter values (e.g., `{"threshold": 0.75}`) |

### Composite semantics

- **Sequence** (`type: "sequence"`) — tick children left-to-right; return
  FAILURE on the first failure, otherwise SUCCESS. Reads as "AND".
- **Selector** (`type: "selector"`) — tick children left-to-right; return
  SUCCESS on the first success, otherwise FAILURE. Reads as "OR".
- **Inverter** (`type: "inverter"`) — tick the single child, flip
  SUCCESS ↔ FAILURE.
- **Leaf** (`type: "leaf"`) — call the registered Python function; bool
  result maps to SUCCESS/FAILURE.

The runtime exposes a Status enum (SUCCESS/FAILURE/RUNNING) but no current
node ever returns RUNNING — that case is reserved for future async or
long-running actions.

---

## The publish gate

The gate is the most important piece of design discipline in the system.
Without it, anyone editing a tree could silently change the AI behaviour. The
gate makes "did the change preserve correctness?" provable, not aspirational.

### Four checks

A draft tree is only allowed to enter `published/` after all four pass:

1. **Schema** — JSON parses; structure matches the schema (`type` field
   present, composites have `children`, etc.)
2. **Registry** — every leaf `id` references an existing `@bt_condition`;
   required parameters supplied
3. **Smoke load** — the runtime can construct the full Tree object
4. **Snapshot diff** — evaluating the tree on its kind's input grid produces
   the same boolean output as the saved snapshot

Steps 1–3 are pure structural checks. Step 4 is the regression guard.

### Snapshot grids

Each tree kind has a fixed grid of inputs designed to cover every behavioural
axis. They're enumerated by `extraction_grid()` and `encounter_grid()` in
`ai_tree/publisher.py`:

| Kind | Axes covered | Total inputs |
|---|---|---|
| Extraction | tick stage × dryness × encounter × damage × loot tier | 4 × 2 × 2 × 2 × 5 = **160** |
| Encounter | own combat × opponent combat × loot tier | 3 × 5 × 5 = **75** |

Each input has a stable string ID like `final|dry|encounter|nodamage|empty`.
The snapshot is a JSON dict mapping every input ID to the tree's boolean
output. Stored next to the published tree as `<name>.snapshot.json`.

### Initial migration: bless-from-legacy

When the system was first introduced, the snapshots were generated by calling
the *legacy* `should_extract`/`should_engage` on the input grid (via
`scripts/publish_tree.py --bless-from-legacy`). This made the published trees
**provably equivalent to the original code** at publish time. Any drift since
then is caught by the parity test in `tests/test_ai_tree_parity.py`, which
re-evaluates every published tree on its grid and asserts equality with the
saved snapshot.

### Manifest and the runtime gate

`ai_trees/manifest.json` records each published tree's SHA256:

```json
{
  "extraction_balanced": {
    "grid_size": 160,
    "published_at": "2026-05-03T14:17:02+00:00",
    "sha256": "fcb2..."
  }
}
```

The runtime's `load_published(name)` reads the manifest, computes the
file's actual SHA256, and **refuses to load if the manifest is missing the
entry or the checksums disagree**. This is what stops drafts (or hand-edited
published files) from leaking into a sim run.

---

## How leaves are registered

Each leaf is a single Python function decorated with `@bt_condition` (or
`@bt_action`, reserved for future use):

```python
@bt_condition(
    name="TimePressureAbove",
    category="Extraction.Time",
    description="True if the run has elapsed past the given fraction (0.0 to 1.0).",
    requires=["perception"],
    params=[ParamSpec(
        name="threshold", type=float, default=0.75,
        description="Fraction of run elapsed (0.0–1.0).",
    )],
)
def time_pressure_above(ctx, threshold: float = 0.75) -> bool:
    return ctx.perception.time_pressure() > threshold
```

The decorator captures everything the catalog needs:

- `name` — the leaf's ID in tree JSON (`{"type": "leaf", "id": "TimePressureAbove"}`)
- `category` — palette grouping (`Extraction.Loot`, `Encounter.Combat`, etc.)
- `description` — shown in the editor as a tooltip
- `requires` — which Context fields the leaf reads (used for documentation)
- `params` — configurable per-instance values; type-coerced at tree load time

The function itself receives `(ctx, **params)` and must return a bool.

### Adding a new leaf

```python
# 1. In runner_sim/zone_sim/ai_conditions.py:
@bt_condition(name="LowOnRunners", category="Squad.Health",
              description="True if the squad has 2 or fewer surviving runners.",
              requires=["squad"])
def low_on_runners(ctx) -> bool:
    return len([r for r in ctx.squad.runners if r.alive]) <= 2

# 2. In the editor: click "Refresh palette" — the new leaf appears in the
#    "Squad.Health" category.

# 3. In tests/test_ai_conditions.py: add unit tests for both branches.
```

**The wrinkle**: if the new leaf reads a Context field that doesn't currently
exist (`ctx.squad` above), three additional places need updating:

1. `extraction_ai.should_extract` / `encounter_ai.should_engage` — populate
   the new field on the Context they construct.
2. `extraction_grid()` / `encounter_grid()` in `publisher.py` — populate
   the new field on every grid input.
3. The tick-loop call site in `sim.py` — pass the new data through.

The publish gate's grid evaluation will surface gap #2 immediately
(`Context has no attribute 'squad'`), which is a much better failure mode
than discovering it weeks later in a sim run.

---

## The editor

`ai_tree_editor/` is a static-asset bundle (HTML + JS + CSS) served by
`ai_tree/server.py`. It uses LiteGraph.js for the canvas. Three-pane layout:

```
┌────────────────┬───────────────────────────┬──────────────────┐
│   PALETTE      │        CANVAS             │   DIAGNOSTICS    │
│                │   (LiteGraph.js)          │                  │
│  Composites    │                           │   No publish     │
│   Sequence     │     ┌──────────┐          │   run yet.       │
│   Selector     │     │ Selector │          │                  │
│   Inverter     │     └────┬─────┘          │                  │
│                │          │                │                  │
│  Extraction    │     ┌────┴─────┐          │                  │
│   IsFinalTick  │     │ Sequence │          │                  │
│   ZoneFeelsDry │     └──────────┘          │                  │
│   ...          │                           │                  │
│  Encounter     │                           │                  │
│   ...          │                           │                  │
└────────────────┴───────────────────────────┴──────────────────┘
```

### Authoring loop

1. Pick a tree from the dropdown → click **Load**. The graph is reconstructed
   from `drafts/<name>.json` via `GET /trees/<name>`.
2. Drag composites/leaves from the palette to the canvas (double-click).
   Wire them up using LiteGraph's connection slots. Edit parameters in
   LiteGraph's properties panel.
3. Click **Save Draft** → `PUT /trees/<name>` writes the serialised graph.
4. Click **Publish** → `PUT` then `POST /publish`. Diagnostics render in
   the right sidebar. If the snapshot check fails, it lists which inputs
   differ from the blessed behaviour.

### Graph ↔ Tree serialization

LiteGraph maintains its own internal graph representation. The translation
to our tree JSON happens in `ai_tree_editor/js/serialize.js`:

- **graphToTree**: walks from the root (the only node with no incoming link)
  downward, emitting nested `{type, children, ...}` objects. Validates: single
  root, no cycles, no node has two parents.
- **treeToGraph**: clears the canvas, recursively materialises each tree node
  as a LiteGraph node, restores positions from `_layout` when present.

The `_layout` field is the editor's sidecar — node x/y positions for visual
fidelity across save/load. The runtime ignores it entirely.

---

## Migration history

This section is provided so future readers can understand why certain
decisions were made.

### The Groot detour

The first cut of this system used Groot2 (the BehaviorTree.CPP visual editor)
as the front-end, with trees stored as XML. Groot was free, mature, and gave
us professional polish without any frontend code. But three friction points
accumulated:

1. **Categories were lost.** Groot's palette grouped only by `<Action>` /
   `<Condition>` tag, not by our `Extraction.Loot` / `Encounter.Combat`
   categories. Information about *what* a leaf does got lost in transit.
2. **Descriptions only rendered for parameterised leaves.** Groot displayed
   port descriptions but ignored the BT.CPP-standard `<description>` element.
   We worked around this with a synthetic `description` port — but that meant
   inventing fake parameters in a "type" field that didn't reflect reality.
3. **Every change required a script run.** Adding a new leaf in
   `ai_conditions.py` meant regenerating `models.xml`, then reloading Groot's
   palette manually. The path from Python to UI was never live.

The custom editor solves all three by speaking directly to Python via HTTP:
the catalog endpoint preserves categories and descriptions verbatim, and the
manual "Refresh palette" button is a one-click round-trip.

### XML → JSON

When we abandoned Groot, the XML format became baggage — its structure was
designed for BT.CPP's namespace, not ours. We migrated the 8 published trees
to JSON via a one-shot script (`scripts/migrate_to_json.py`, since deleted),
verified the snapshots were bit-identical, then removed XML support from
the runtime. The migration was correctness-neutral: same logic, same outputs,
cleaner schema.

---

## Out of scope (for now)

- **`assign_squads` / strategy trees.** The dispatch decision (which doctrine
  goes to which zone) is the natural next consumer of this engine. It would
  introduce a new tree kind alongside `extraction_*` and `encounter_*`, with
  its own grid generator and Context shape. The engine is ready; the design
  work isn't done.
- **Live tree monitor.** Watching a tree tick in real-time during a sim run
  would be cool, but the current static editing covers the authoring use case.
- **Action nodes.** The `@bt_action` decorator exists for symmetry but no
  leaves use it yet. Strategy trees will likely be the first consumer.
- **Utility-AI scoring.** Classical BTs are sufficient for the current
  yes/no decisions. A future scoring composite (each child weighted, parent
  picks the highest score) could slot in alongside Sequence/Selector without
  disturbing the rest of the engine.

---

## Key files at a glance

| File | Lines | Role |
|---|---|---|
| `ai_tree/registry.py` | ~120 | Decorator + global REGISTRY |
| `ai_tree/composites.py` | ~110 | Status enum + 4 node classes |
| `ai_tree/context.py` | ~50 | Dict-with-attrs container |
| `ai_tree/runtime.py` | ~170 | JSON loader + `Tree.tick()` |
| `ai_tree/publisher.py` | ~340 | Publish gate, manifest, snapshot diff |
| `ai_tree/server.py` | ~225 | HTTP endpoints + static handler |
| `runner_sim/zone_sim/ai_conditions.py` | ~140 | The 11 game-specific leaves |
| `ai_tree_editor/index.html` + JS | ~700 | Browser editor |
| `tests/test_ai_tree_*.py` | ~900 | 187 tests covering all of the above |
