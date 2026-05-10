# AI Behaviour Trees — Authoring Guide

A practical reference for **what you can change in JSON alone vs what
needs a Python edit**. For the architectural design and rationale see
[`ai_tree.md`](ai_tree.md); for day-to-day commands see `CLAUDE.md`.

The short version: every behaviour-tree decision the game makes is
controlled by two things, and they live in different places.

| Layer | What it controls | Where it lives |
|---|---|---|
| **Data** | Which checks are used, in what order, with what parameters | JSON files in `ai_trees/` |
| **Code** | What each check actually computes, what data is available, how composites behave | Python in `ai_tree/` and `runner_sim/zone_sim/ai_conditions.py` |

You can do *a lot* in JSON without writing any code. But the **vocabulary
of available checks** is fixed by Python — adding a new kind of check
takes a code change.

---

## The two zones at a glance

| Thing you might want to change | Lives in | Code change? |
|---|---|---|
| Tree shape (what connects to what) | JSON | No |
| Composite ordering (which Selector child fires first) | JSON | No |
| Leaf parameter values (`"threshold": 1.2`) | JSON | No |
| Which leaves appear in which tree | JSON | No |
| Doctrine personality / risk tolerance | JSON | No |
| **The logic of an existing check** (what `OpponentHelpless` actually tests) | Python (`ai_conditions.py`) | Yes |
| **A new kind of check** (e.g., "did we lose a runner?") | Python (`ai_conditions.py`) | Yes — new `@bt_condition` |
| **What data the AI can sense** (e.g., opponent doctrine, market state) | Python (`extraction_ai.py` / `encounter_ai.py`) | Yes — extend Context |
| **What "Sequence" / "Selector" / "Inverter" mean** | Python (`composites.py`) | Yes — engine change |
| **A new control structure** (e.g., Parallel, Repeater, weighted scoring) | Python (`composites.py` + runtime) | Yes — engine change |

---

## What's in the JSON

A tree file like `ai_trees/published/encounter_balanced.json` is pure
data. No Python is read from it. It says:

- The **shape**: a Selector with three children (one Leaf, two Sequences),
  the second Sequence containing an Inverter.
- The **identifiers**: which leaves to invoke (`OpponentHelpless`,
  `CarryingHighValue`, `CombatRatioAbove`).
- The **parameters**: this instance of `CombatRatioAbove` uses
  `threshold: 1.2`; another instance uses `0.9`.

Everything else — what `OpponentHelpless` *does*, what `CombatRatioAbove`
*does*, what a Selector *is* — comes from Python.

### Things you can do without writing code

These are pure JSON edits, done in the editor or directly in the file:

- **Tweak a threshold.** Change a parameterised leaf's value. Example:
  "Make GREEDY extract earlier — at 60% time pressure instead of 75%" →
  flip `"threshold": 0.75` to `0.6` in `extraction_greedy.json`.
  Republish.
- **Reorder children.** Selectors evaluate left-to-right. Putting a
  cheap, common-success check first short-circuits the rest. Putting a
  rare-but-decisive check first changes priority semantics.
- **Add a check to a tree.** As long as the leaf already exists in the
  catalog, you can drop it into any tree by drag-and-drop in the editor.
  Example: "Make BALANCED also engage if it took damage this run" →
  add a Sequence with `TookDamage` + `CombatRatioAbove(0.7)` to
  `encounter_balanced.json`.
- **Remove a check.** Conversely, take a leaf out of a tree if a
  doctrine should stop caring about something.
- **Restructure a doctrine entirely.** A Cautious tree can have a
  fundamentally different shape than a Greedy one — different number of
  children, different nesting depth, different leaves. The trees aren't
  forced into a common template.

### What "publishing" actually does

When you click Publish (or run `publish_tree.py`), the system runs four
checks on the JSON:

1. **Schema** — does it parse correctly?
2. **Registry** — does every leaf `id` reference a real
   `@bt_condition` in Python?
3. **Smoke load** — can the runtime construct a Tree object from it?
4. **Snapshot diff** — does the tree produce the same outputs as the
   saved snapshot file? (Or, if you passed `--update-snapshot`, save
   the new outputs as the new snapshot.)

If all four pass, the file is copied from `drafts/` to `published/`,
its SHA256 is recorded in `manifest.json`, and the runtime will load
it on the next sim.

If any fail, the diagnostics show why — and `published/` is left alone.
The simulator never sees an unvalidated tree.

---

## What's in the Python

### Leaves (the vocabulary of checks)

Every check available to a tree is a Python function with a
`@bt_condition` decorator. Look at the existing leaves in
`runner_sim/zone_sim/ai_conditions.py`:

```python
@bt_condition(
    name="HasUncommonLoot",
    category="Extraction.Loot",
    description="True if the squad's best item is Uncommon or higher.",
    requires=["loot"],
)
def has_uncommon_loot(ctx) -> bool:
    best = ctx.loot.best_tier()
    return best is not None and best >= Tier.UNCOMMON
```

The function body is the *logic*. The decorator is the *registration*
that makes it appear in the editor's palette and resolvable from tree
JSON. Every leaf is one function.

The current catalog has **11 leaves** (9 boolean conditions, 2
parameterised). Every change to AI behaviour either:

- Recombines these 11 in JSON (no code), or
- Adds new leaves to the catalog (Python).

### Domains (how leaves are grouped)

Each leaf has a `category` string with a `<Kind>.<System>` shape:
`Extraction.Loot`, `Encounter.Combat`, etc. The editor uses these to
group the palette, but the convention encodes something stronger than
visual grouping: **a domain is a category whose leaves all read the
same slice of Context**. That binding is what makes the grouping
meaningful — and what makes some new leaves cheap and others expensive.

The current catalog:

| Domain | Reads | Leaves |
|---|---|---|
| `Extraction.Loot` | `ctx.loot` | `CarryingNothing`, `CarryingAnything`, `HasUncommonLoot` |
| `Extraction.Time` | `ctx.perception` (tick / max_ticks) | `IsFinalTick`, `TimePressureAbove` |
| `Extraction.Perception` | `ctx.perception` (run history flags) | `ZoneFeelsDry`, `HadEncounter`, `TookDamage` |
| `Encounter.Combat` | `ctx.own_combat`, `ctx.opponent_combat_estimate` | `OpponentHelpless`, `CombatRatioAbove` |
| `Encounter.Loot` | `ctx.loot` | `CarryingHighValue` |

Two important properties fall out of this:

1. **Adding a leaf to an existing domain is cheap.** A new
   `Extraction.Loot` leaf just reads `ctx.loot`, which is already
   populated everywhere. Authoring it via the editor's New Leaf form
   is a self-contained operation: pick the category, write the body,
   submit. Done.
2. **Opening a new domain is more involved.** A `Squad.Health`
   domain would need `ctx.squad` populated everywhere a tree might
   tick — meaning the dispatcher, the grid generators, and the
   tick-loop call sites all need updates *before* the first leaf in
   that domain can be written. After that one-time cost, every
   subsequent leaf in the domain is back to "cheap".

#### Adding a new domain — worked example

Suppose you want squads to react to runner attrition. There's no
existing leaf for this, and no existing domain whose Context slice
covers it. So:

```
1. Pick the domain name and the Context binding.
   - Name: Squad.Health
   - Binds to: ctx.squad (a new field carrying SquadState)

2. Extend Context in the dispatchers (Python edit, code review).
   - runner_sim/zone_sim/extraction_ai.py:should_extract
       ctx = Context(loot=loot, perception=perception, squad=squad)
   - runner_sim/zone_sim/encounter_ai.py:should_engage  (if relevant)
       ctx = Context(..., squad=squad)

3. Update the publisher's grid generators (Python edit).
   - ai_tree/publisher.py:extraction_grid() — populate `squad` for
     every grid input, with enough variants to cover the leaf's
     branches (e.g. squad with 1/2/3 alive runners).
   - Same for encounter_grid() if encounter trees will use it.

4. Update the tick-loop call sites (Python edit).
   - runner_sim/zone_sim/sim.py — pass the squad through to
     should_extract / should_engage.

5. Author the first leaf via the editor's "New Leaf" modal.
   - Name: LowOnRunners
   - Category: Squad.Health
   - Requires: squad
   - Body: return len([r for r in ctx.squad.runners if r.alive]) <= 2

6. From now on: every Squad.Health leaf is just step 5.
```

Steps 2–4 are the *domain expansion*. Steps 5+ are *catalog growth
within an existing domain*. The publish gate's grid evaluation is
unforgiving in a useful way: if you forget step 3, the next snapshot
run errors with `Context has no attribute 'squad'`, pinning the
mistake to the missing wiring.

The distinction shows up in the editor too. The New Leaf form's
**Requires** field auto-suggests context names that already exist
in the catalog (`loot`, `perception`, `own_combat`,
`opponent_combat_estimate`). Typing a new name like `squad` is
allowed but signals "this is a domain expansion, not a catalog
growth" — and your leaf will fail to publish until the dispatcher
populates that field.

### Composites (the engine)

`ai_tree/composites.py` defines what `Sequence`, `Selector`, and
`Inverter` mean. These three are the entire control-flow vocabulary
trees can use. Adding a fourth (e.g., a `WeightedSelector` that picks
the highest-scoring child) is an engine-level change — new class,
new tests, new JSON `type` token, new editor node.

That's a deliberate choice: keeping the composite set small makes
trees easier to read. We add new composites only when there's a use
case the current ones can't express.

### Context (what the AI can sense)

`runner_sim/zone_sim/extraction_ai.py:should_extract` builds the
Context that gets passed to extraction trees:

```python
ctx = Context(loot=loot, perception=perception)
```

Anything a leaf reads from `ctx` must be populated here (and in the
publisher's grid generators, so the snapshot regression check still
works). If you write a new leaf that reads `ctx.squad`, you need to
populate `squad` here too — the publish gate's grid evaluation will
tell you immediately if you forgot.

---

## Decision tree: which zone is my change in?

```
Q: Do I want a different combination of existing checks,
   different parameter values, or different priorities?
   ─────────────────────────────────────────────────────
                       │
           ┌───────────┴───────────┐
           │                       │
         YES                       NO
           │                       │
           ▼                       │
   JSON only — edit               ▼
   the tree in the          Q: Do I want a new check, but it only
   editor and publish.         reads existing ctx fields (loot,
                               perception, etc.)?
                              ────────────────────────────────────
                                │
                       ┌────────┴────────┐
                       │                 │
                     YES                 NO
                       │                 │
                       ▼                 ▼
              "New Leaf" form      Q: Do I want the AI to react to
              in the editor →         game data ctx doesn't carry
              writes to               yet (squad health, market
              user_leaves/            state, etc.)?
              and reloads             ─────────────────────────────
              the registry.            │
                                ┌──────┴──────┐
                                │             │
                              YES             NO
                                │             │
                                ▼             ▼
                          Open a new      Engine change
                          domain:         (composites.py +
                          extend Context  runtime.py).
                          + grid + first  Rare; review
                          leaf in the     carefully.
                          new category.
                          (See "Domains"
                          worked example.)
```

---

## Workflows

### Pure JSON change (most common)

```
1. uv run python -m ai_tree.server
   open http://localhost:8765/

2. Pick the tree from the dropdown → Load
   Edit visually (drag, wire, set parameters)
   Click Save Draft (writes ai_trees/drafts/<name>.json)

3. Click Publish
   - if outputs match snapshot → published, sim picks it up
   - if outputs changed → fails, diagnostics show which inputs differ
   - rerun with the "update snapshot" option to bless an intentional
     change

4. uv run pytest && uv run python marathon_market.py --trace-ai
   - parity tests confirm published trees haven't drifted from snapshots
   - --trace-ai shows the new behaviour live
```

### Creating a new tree (rare)

The eight existing trees cover every `<kind>_<doctrine>` slot the
runtime can dispatch (extraction × encounter × greedy/cautious/
balanced/support). The "New Tree" button is for two situations:

- **Filling a slot you've deleted** to start over from scratch. Delete
  the JSON file by hand, click New Tree, pick the kind and doctrine,
  the editor seeds an empty Selector and selects it.
- **Authoring trees for a doctrine you've just added** to the
  `Doctrine` enum. The kind/doctrine dropdowns read from the runtime
  vocabulary, so a new enum value appears here automatically after a
  server restart.

The kind dropdown (extraction / encounter) is strict — these are
baked into the publisher's grid generators. The doctrine dropdown is
also strict — it sources from `Doctrine` enum values, since the
runtime dispatches by enum value and the enum itself is bounded by
`SHELL_DOCTRINE` (every doctrine has to be reachable by some shell or
no squad would ever trigger its tree). The editor refuses to write a
tree the dispatcher couldn't actually load.

### Adding a new leaf (a few times per feature)

There are two paths, depending on whether you're growing an existing
domain or opening a new one. Most cases are the first.

**Path A — Growing an existing domain (cheap, designer-driven):**

```
1. Open http://localhost:8765/ → click "New Leaf".
   Fill in name, category (pick from the existing list), description,
   Requires (suggested from the catalog), parameters, and body.
   Live-preview shows the rendered Python.

2. Submit.
   Server validates, writes runner_sim/zone_sim/user_leaves/<snake>.py,
   imports it (registry updates), and the editor refreshes the palette
   automatically. New leaf is immediately draggable into trees.

3. uv run pytest — confirms nothing else broke.
```

The generated `user_leaves/<snake>.py` file is just normal Python and
is committed alongside everything else. Hand-edit it later if you need
to tune the body.

**Path B — Opening a new domain (one-time, requires code review):**

See the "Adding a new domain" walkthrough above. The short version:
extend `Context` in the dispatcher, update the grid generators, then
use **Path A** to author the first leaf in the new domain.

**Built-in vs user leaves.** Two locations exist for `@bt_condition`
files:

- `runner_sim/zone_sim/ai_conditions.py` — the hand-curated catalog
  that ships with the game. Edit this for foundational leaves you
  consider part of the core vocabulary.
- `runner_sim/zone_sim/user_leaves/*.py` — auto-discovered package,
  one file per leaf. The editor's New Leaf form writes here. Hand-
  authoring a file here also works; the package's `__init__.py`
  uses `pkgutil.iter_modules` to import every sibling on startup.

The runtime makes no distinction — both register into the same
REGISTRY and appear in the same palette.

### Adding a new control structure (rare)

This is a real engine change. Touch:

- `ai_tree/composites.py` — define the new node class
- `ai_tree/runtime.py` — handle the new `type` in the JSON parser
- `ai_tree_editor/js/bt-nodes.js` — register a LiteGraph node type
- `ai_tree_editor/js/serialize.js` — handle the new node in graph ↔
  tree conversion
- Tests for all of the above

If you find yourself reaching for this, ask first: can the new
behaviour be expressed by combining existing composites with a new
leaf? Often yes.

---

## What `--trace-ai` is actually showing you

When you run with `--trace-ai`, every dispatcher emits one line per
decision:

```
[bt] T 2/8  extract_cautious → YES  loot= 1(UNCOMMON) dry=. enc=Y dmg=.
```

This is **the JSON tree's behaviour, rendered as it happens**. Reading
those lines is the fastest way to confirm:

- The right tree fired (`extract_cautious`)
- It got the right inputs (`loot= 1(UNCOMMON)` etc.)
- It returned the right answer (`YES`)

If the trace shows a tree returning a result you didn't expect, it's
almost always a JSON issue (wrong threshold, wrong leaf, wrong
ordering). The Python is rarely the bug after the system stabilises.

---

## Why this split exists

The system was designed with two principles in mind:

1. **Iteration on AI behaviour should be cheap.** A game designer
   wanting to tune a doctrine shouldn't have to wait for a programmer
   or restart anything. JSON edit + publish + observe loop is fast.
2. **The AI's vocabulary is a programmer concern.** What checks even
   *exist*, what data is available, how composites behave — those are
   architectural decisions that benefit from review. They're rare
   enough that the friction of a code change is acceptable.

The catalog is the seam between these two worlds. Today it has 11
entries; in a year it might have 50. The "no code required" zone
expands as the catalog grows, never shrinks.

This is the same shape as **SQL vs. the database engine**: the query
language is rich enough to express any combination of operators on
existing tables, but you can't query columns that don't exist, and
you can't invent a new SQL operator without modifying the engine.
Same logic applies here.
