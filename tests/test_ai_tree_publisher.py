"""Tests for ai_tree/publisher.py — the publish gate.

Covers:
- Lint catches schema, missing-leaf, parameter-missing errors
- Snapshot diff catches behavioural drift
- --update-snapshot blesses changes
- --bless-from-legacy bootstraps the snapshot from a legacy function
- Manifest is written/updated correctly
- Grid generators produce stable, deterministic IDs
"""

import json
from pathlib import Path

import pytest

from ai_tree import publisher as pub
from ai_tree.context import Context
from runner_sim.zone_sim import ai_conditions  # noqa: F401 — registers leaves


# ---------------------------------------------------------------------------
# Fixture: redirect publisher paths to a temp dir so tests are hermetic
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_tree_dirs(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    published = tmp_path / "published"
    drafts.mkdir()
    published.mkdir()
    manifest = tmp_path / "manifest.json"

    monkeypatch.setattr(pub, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(pub, "PUBLISHED_DIR", published)
    monkeypatch.setattr(pub, "MANIFEST_PATH", manifest)
    return drafts, published, manifest


def _write_draft(drafts: Path, name: str, body: str) -> Path:
    path = drafts / f"{name}.xml"
    path.write_text(body, encoding="utf-8")
    return path


# A minimal valid extraction tree: extract iff IsFinalTick
_MINIMAL_EXTRACTION = """
<root BTCPP_format="4">
  <BehaviorTree ID="T">
    <Condition ID="IsFinalTick"/>
  </BehaviorTree>
</root>
""".strip()

# Same logical behaviour as above but reordered — used for snapshot-diff tests
_EQUIVALENT_EXTRACTION = """
<root BTCPP_format="4">
  <BehaviorTree ID="T">
    <Selector>
      <Condition ID="IsFinalTick"/>
    </Selector>
  </BehaviorTree>
</root>
""".strip()


# ---------------------------------------------------------------------------
# Grid generators
# ---------------------------------------------------------------------------
class TestGrids:
    def test_extraction_grid_stable(self):
        ids_first = [i for i, _ in pub.extraction_grid()]
        ids_second = [i for i, _ in pub.extraction_grid()]
        assert ids_first == ids_second
        # Sanity: 4 ticks × 2 dryness × 2 encounter × 2 damage × 5 loot = 160
        assert len(ids_first) == 160

    def test_encounter_grid_stable(self):
        ids_first = [i for i, _ in pub.encounter_grid()]
        ids_second = [i for i, _ in pub.encounter_grid()]
        assert ids_first == ids_second
        # 3 own × 5 opp × 5 loot = 75
        assert len(ids_first) == 75

    def test_extraction_grid_ids_are_unique(self):
        ids = [i for i, _ in pub.extraction_grid()]
        assert len(ids) == len(set(ids))

    def test_encounter_grid_ids_are_unique(self):
        ids = [i for i, _ in pub.encounter_grid()]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Naming conventions
# ---------------------------------------------------------------------------
class TestNaming:
    def test_extraction_prefix(self):
        assert pub.infer_kind("extraction_cautious") == pub.TreeKind.EXTRACTION

    def test_encounter_prefix(self):
        assert pub.infer_kind("encounter_greedy") == pub.TreeKind.ENCOUNTER

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Cannot infer tree kind"):
            pub.infer_kind("strategy_aggressive")

    def test_doctrine_inferred_from_suffix(self):
        from runner_sim.zone_sim.extraction_ai import Doctrine
        assert pub.infer_doctrine("extraction_cautious") == Doctrine.CAUTIOUS
        assert pub.infer_doctrine("encounter_greedy") == Doctrine.GREEDY


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
class TestLint:
    def test_missing_file(self, tmp_path):
        result = pub.lint(tmp_path / "nope.xml")
        assert result[0] is None
        assert any(d.check == "schema" and "not found" in d.message
                   for d in result[1])

    def test_malformed_xml(self, isolated_tree_dirs):
        drafts, _, _ = isolated_tree_dirs
        path = _write_draft(drafts, "extraction_cautious", "<root><not closed")
        tree, diags = pub.lint(path)
        assert tree is None
        assert any("Malformed XML" in d.message for d in diags)

    def test_unknown_leaf(self, isolated_tree_dirs):
        drafts, _, _ = isolated_tree_dirs
        body = """
        <root><BehaviorTree ID="T"><Condition ID="DoesNotExist"/></BehaviorTree></root>
        """
        path = _write_draft(drafts, "extraction_cautious", body)
        tree, diags = pub.lint(path)
        assert tree is None
        assert any("DoesNotExist" in d.message for d in diags)


# ---------------------------------------------------------------------------
# Publish workflow
# ---------------------------------------------------------------------------
class TestPublishFirstTime:
    def test_first_publish_creates_snapshot_and_manifest(self, isolated_tree_dirs):
        drafts, published, manifest_path = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)

        result = pub.publish("extraction_cautious")

        assert result.success, result.report()
        assert result.snapshot_updated is True
        assert (published / "extraction_cautious.xml").exists()
        assert (published / "extraction_cautious.snapshot.json").exists()
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "extraction_cautious" in manifest
        assert manifest["extraction_cautious"]["grid_size"] == 160

    def test_first_publish_with_bless_from_legacy(self, isolated_tree_dirs):
        """The bless-from-legacy path generates the snapshot from a custom callable
        (used in production to make the migration provably equivalent)."""
        drafts, published, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)

        # Synthetic legacy that says "always extract" — different from
        # the tree's actual behaviour (which only extracts at IsFinalTick).
        # Bless should still record the legacy values and then check the tree
        # against them; since they differ, publish must fail.
        def fake_legacy(kind, grid):
            return {input_id: True for input_id, _ in grid}

        result = pub.publish("extraction_cautious", bless_from_legacy=fake_legacy)
        assert not result.success
        assert any(d.check == "legacy_parity" for d in result.diagnostics)


class TestPublishSecondTime:
    def test_unchanged_tree_publishes_again(self, isolated_tree_dirs):
        drafts, published, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        first = pub.publish("extraction_cautious")
        assert first.success
        # Republish identical draft → success, no snapshot update needed
        second = pub.publish("extraction_cautious")
        assert second.success
        assert second.snapshot_updated is False

    def test_behavioural_change_fails_without_update(self, isolated_tree_dirs):
        """A tree that produces different output must fail until --update-snapshot."""
        drafts, _, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        first = pub.publish("extraction_cautious")
        assert first.success

        # Replace with a tree that always returns SUCCESS (always extract) —
        # behaviourally different.
        always_extract = """
        <root><BehaviorTree ID="T">
          <Selector>
            <Condition ID="IsFinalTick"/>
            <Condition ID="CarryingNothing"/>
            <Condition ID="CarryingAnything"/>
          </Selector>
        </BehaviorTree></root>
        """
        _write_draft(drafts, "extraction_cautious", always_extract)
        second = pub.publish("extraction_cautious")
        assert not second.success
        assert any(d.check == "snapshot" for d in second.diagnostics)

    def test_update_snapshot_blesses_change(self, isolated_tree_dirs):
        drafts, published, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        pub.publish("extraction_cautious")

        always_extract = """
        <root><BehaviorTree ID="T">
          <Selector>
            <Condition ID="IsFinalTick"/>
            <Condition ID="CarryingNothing"/>
            <Condition ID="CarryingAnything"/>
          </Selector>
        </BehaviorTree></root>
        """
        _write_draft(drafts, "extraction_cautious", always_extract)
        result = pub.publish("extraction_cautious", update_snapshot=True)
        assert result.success
        assert result.snapshot_updated is True

        # Re-publishing without the flag should now succeed because the snapshot
        # was just refreshed.
        third = pub.publish("extraction_cautious")
        assert third.success


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------
class TestDiff:
    def test_no_diff(self):
        a = {"x": True, "y": False}
        assert pub.diff_snapshots(a, dict(a)) == []

    def test_value_changed(self):
        diffs = pub.diff_snapshots({"x": True}, {"x": False})
        assert len(diffs) == 1
        assert "expected True, got False" in diffs[0]

    def test_missing_and_extra_keys(self):
        diffs = pub.diff_snapshots({"a": True, "b": False}, {"b": False, "c": True})
        joined = "\n".join(diffs)
        assert "missing input in result: a" in joined
        assert "new input not in snapshot: c" in joined


# ---------------------------------------------------------------------------
# Manifest gate (load_published) — the runtime safety boundary
# ---------------------------------------------------------------------------
class TestLoadPublished:
    def test_load_after_publish_succeeds(self, isolated_tree_dirs):
        drafts, _, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        pub.publish("extraction_cautious")

        tree = pub.load_published("extraction_cautious")
        assert tree is not None
        # Tree should evaluate sensibly: at the final tick, returns True
        from runner_sim.zone_sim.extraction_ai import SquadLoot, SquadPerception
        ctx = Context(
            perception=SquadPerception(
                ticks_since_last_find=0, had_encounter_this_run=False,
                took_damage_this_run=False, tick=10, max_ticks=10,
            ),
            loot=SquadLoot(items=[]),
        )
        assert tree.tick(ctx) is True

    def test_unpublished_tree_raises(self, isolated_tree_dirs):
        with pytest.raises(pub.UnverifiedTreeError, match="not in the manifest"):
            pub.load_published("extraction_never_published")

    def test_modified_published_file_raises(self, isolated_tree_dirs):
        drafts, published, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        pub.publish("extraction_cautious")

        # Simulate someone hand-editing the published file after publish
        published_path = published / "extraction_cautious.xml"
        published_path.write_text(
            published_path.read_text(encoding="utf-8") + "\n<!-- tampered -->",
            encoding="utf-8",
        )

        with pytest.raises(pub.UnverifiedTreeError, match="modified since publish"):
            pub.load_published("extraction_cautious")

    def test_manifest_entry_without_file_raises(self, isolated_tree_dirs):
        drafts, published, _ = isolated_tree_dirs
        _write_draft(drafts, "extraction_cautious", _MINIMAL_EXTRACTION)
        pub.publish("extraction_cautious")

        # Manifest entry exists but the file got deleted somehow
        (published / "extraction_cautious.xml").unlink()

        with pytest.raises(pub.UnverifiedTreeError, match="missing"):
            pub.load_published("extraction_cautious")
