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
          JSON only.                    ▼
          Edit a tree in           Q: Do I want a new kind of
          the editor and               check that doesn't exist
          publish.                     in the catalog yet?
                                     ─────────────────────────
                                       │
                                ┌──────┴──────┐
                                │             │
                              YES             NO
                                │             │
                                ▼             ▼
                          New @bt_condition   Q: Do I want the
                          in ai_conditions.   AI to react to
                          py. Then back to    new game data?
                          JSON to use it.    ───────────────
                                              │
                                       ┌──────┴──────┐
                                       │             │
                                     YES             NO
                                       │             │
                                       ▼             ▼
                                Extend Context    Engine change
                                in the dispatcher.   (composites.py
                                Update grid in       runtime.py).
                                publisher.py.        Rare; review
                                Then maybe a new     carefully.
                                leaf to read it.
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

### Adding a new leaf (a few times per feature)

```
1. Add the function to runner_sim/zone_sim/ai_conditions.py:

   @bt_condition(
       name="LowOnRunners",
       category="Squad.Health",
       description="True if the squad has 2 or fewer surviving runners.",
       requires=["squad"],
   )
   def low_on_runners(ctx) -> bool:
       return len([r for r in ctx.squad.runners if r.alive]) <= 2

2. (If the leaf needs new Context fields like `ctx.squad` above — add
    them in extraction_ai.should_extract / encounter_ai.should_engage,
    and to the corresponding grid generator in publisher.py. Otherwise
    skip this step.)

3. Add a unit test in tests/test_ai_conditions.py covering both branches.

4. In the editor, click "Refresh palette" — the new leaf appears in
   the "Squad.Health" category. Drag it into trees as needed.

5. uv run pytest — confirms nothing else broke.
```

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
