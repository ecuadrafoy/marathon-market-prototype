"""Parity test — the durable regression guard for behaviour-tree decisions.

Every published tree's outputs must still match its blessed snapshot file.
The publish gate enforces this at publish time; this pytest ensures CI also
catches drift caused by changes to leaf functions, composite semantics, or
tree XML that didn't go through `scripts/publish_tree.py`.

If a test fails, the fix is one of:
- Re-publish the tree if the change was intentional:
    `uv run python scripts/publish_tree.py <name> --update-snapshot`
- Revert the leaf/composite change if the regression was unintended.
"""

import json

import pytest

from ai_tree.publisher import (
    PUBLISHED_DIR,
    evaluate_on_grid,
    infer_kind,
    load_published,
)
from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves


PUBLISHED_NAMES = [
    "extraction_greedy",
    "extraction_cautious",
    "extraction_balanced",
    "extraction_support",
    "encounter_greedy",
    "encounter_cautious",
    "encounter_balanced",
    "encounter_support",
]


@pytest.mark.parametrize("name", PUBLISHED_NAMES)
def test_tree_matches_snapshot(name: str):
    """Re-evaluating a published tree on its grid must match its snapshot."""
    tree = load_published(name)
    kind = infer_kind(name)
    actual = evaluate_on_grid(tree, kind)

    snapshot_path = PUBLISHED_DIR / f"{name}.snapshot.json"
    assert snapshot_path.exists(), f"snapshot missing for {name}"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    # Coerce both to {str: bool} for a clean equality check.
    expected_pairs = {k: bool(v) for k, v in expected.items()}
    actual_pairs = {k: bool(v) for k, v in actual.items()}
    assert actual_pairs == expected_pairs, (
        f"{name} drifted from its snapshot — re-publish to bless the change"
    )
