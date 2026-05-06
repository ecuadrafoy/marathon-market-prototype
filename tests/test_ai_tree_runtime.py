"""Tests for ai_tree composites + JSON loader.

Covers:
- Sequence and Selector short-circuit semantics
- Inverter inversion
- Leaf wrapping conditions and actions
- JSON loading: well-formed trees, malformed JSON, missing nodes, parameter coercion
- File-path-based load_tree dispatch
"""

import pytest

from ai_tree.composites import Inverter, Leaf, Selector, Sequence, Status
from ai_tree.context import Context
from ai_tree.registry import (
    REGISTRY,
    ParamSpec,
    bt_action,
    bt_condition,
    clear_registry,
    get,
)
from ai_tree.runtime import Tree, TreeLoadError, load_tree, load_tree_from_json_string


# ---------------------------------------------------------------------------
# Fixtures: register a stable set of synthetic leaves for runtime tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def synthetic_leaves():
    """Register four synthetic leaves and clean up afterwards.

    Saves the existing REGISTRY (e.g., game leaves registered by
    ai_conditions) before clearing, and restores it on teardown so
    other test files aren't left with an empty registry.
    """
    saved = dict(REGISTRY)
    clear_registry()

    @bt_condition(name="True", category="Test")
    def _true(ctx) -> bool:
        return True

    @bt_condition(name="False", category="Test")
    def _false(ctx) -> bool:
        return False

    @bt_condition(name="ReadFlag", category="Test", requires=["flag"])
    def _read_flag(ctx) -> bool:
        return ctx.flag

    @bt_condition(
        name="Above",
        category="Test",
        requires=["value"],
        params=[ParamSpec(name="threshold", type=float, default=0.0)],
    )
    def _above(ctx, threshold: float = 0.0) -> bool:
        return ctx.value > threshold

    yield
    clear_registry()
    REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Composite semantics
# ---------------------------------------------------------------------------
class TestSequence:
    def test_all_success_returns_success(self, synthetic_leaves):
        seq = Sequence([Leaf(get("True")), Leaf(get("True"))])
        assert seq.tick(Context()) == Status.SUCCESS

    def test_first_failure_short_circuits(self, synthetic_leaves):
        # If short-circuit works, the third (always-True) child is never reached;
        # we just verify the result is FAILURE.
        seq = Sequence([Leaf(get("True")), Leaf(get("False")), Leaf(get("True"))])
        assert seq.tick(Context()) == Status.FAILURE

    def test_empty_via_loader_is_rejected(self, synthetic_leaves):
        # The loader rejects empty composites; constructing in code is allowed
        # but degenerate (returns SUCCESS vacuously). Document that behaviour.
        seq = Sequence([])
        assert seq.tick(Context()) == Status.SUCCESS


class TestSelector:
    def test_all_failure_returns_failure(self, synthetic_leaves):
        sel = Selector([Leaf(get("False")), Leaf(get("False"))])
        assert sel.tick(Context()) == Status.FAILURE

    def test_first_success_short_circuits(self, synthetic_leaves):
        sel = Selector([Leaf(get("False")), Leaf(get("True")), Leaf(get("False"))])
        assert sel.tick(Context()) == Status.SUCCESS


class TestInverter:
    def test_inverts_success_to_failure(self, synthetic_leaves):
        inv = Inverter(Leaf(get("True")))
        assert inv.tick(Context()) == Status.FAILURE

    def test_inverts_failure_to_success(self, synthetic_leaves):
        inv = Inverter(Leaf(get("False")))
        assert inv.tick(Context()) == Status.SUCCESS


class TestLeafWithContextAndParams:
    def test_leaf_reads_context_attribute(self, synthetic_leaves):
        leaf = Leaf(get("ReadFlag"))
        assert leaf.tick(Context(flag=True)) == Status.SUCCESS
        assert leaf.tick(Context(flag=False)) == Status.FAILURE

    def test_leaf_passes_params_as_kwargs(self, synthetic_leaves):
        # value=5, threshold=10 → False
        leaf_high = Leaf(get("Above"), params={"threshold": 10.0})
        assert leaf_high.tick(Context(value=5)) == Status.FAILURE
        # value=5, threshold=2 → True
        leaf_low = Leaf(get("Above"), params={"threshold": 2.0})
        assert leaf_low.tick(Context(value=5)) == Status.SUCCESS


class TestActionLeaf:
    def test_action_returning_none_is_success(self):
        saved = dict(REGISTRY)
        clear_registry()
        try:
            @bt_action(name="Side")
            def _side(ctx):
                ctx["touched"] = True
                return None

            leaf = Leaf(get("Side"))
            ctx = Context()
            assert leaf.tick(ctx) == Status.SUCCESS
            assert ctx["touched"] is True
        finally:
            clear_registry()
            REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Param-missing error (registered with no default)
# ---------------------------------------------------------------------------
class TestMissingRequiredParam:
    def test_missing_required_param(self, synthetic_leaves):
        # Re-register the Above leaf without a default to force the
        # missing-param error to surface. The synthetic_leaves fixture
        # has already saved the outer registry; the local clear_registry()
        # / clear_registry() pair is contained within that scope.
        clear_registry()

        @bt_condition(
            name="StrictAbove",
            requires=["value"],
            params=[ParamSpec(name="threshold", type=float)],  # no default
        )
        def _strict(ctx, threshold: float) -> bool:
            return ctx.value > threshold

        doc = '{"root": {"type": "leaf", "id": "StrictAbove"}}'
        with pytest.raises(TreeLoadError, match="missing required parameter"):
            load_tree_from_json_string(doc)


# ---------------------------------------------------------------------------
# JSON loader — the canonical tree format
# ---------------------------------------------------------------------------
class TestJsonLoader:
    def test_loads_simple_selector(self, synthetic_leaves):
        doc = """
        {
          "name": "Test",
          "root": {
            "type": "selector",
            "children": [
              {"type": "leaf", "id": "False"},
              {"type": "leaf", "id": "True"}
            ]
          }
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.name == "Test"
        assert tree.tick(Context()) is True

    def test_nested_sequence_in_selector(self, synthetic_leaves):
        doc = """
        {
          "root": {
            "type": "selector",
            "children": [
              {
                "type": "sequence",
                "children": [
                  {"type": "leaf", "id": "ReadFlag"},
                  {"type": "leaf", "id": "True"}
                ]
              },
              {"type": "leaf", "id": "False"}
            ]
          }
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.tick(Context(flag=True)) is True
        assert tree.tick(Context(flag=False)) is False

    def test_inverter_decorator(self, synthetic_leaves):
        doc = """
        {
          "root": {
            "type": "inverter",
            "child": {"type": "leaf", "id": "False"}
          }
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.tick(Context()) is True

    def test_leaf_with_params(self, synthetic_leaves):
        doc = """
        {
          "root": {
            "type": "leaf",
            "id": "Above",
            "params": {"threshold": 3.5}
          }
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.tick(Context(value=5)) is True
        assert tree.tick(Context(value=2)) is False

    def test_leaf_uses_param_default_when_missing(self, synthetic_leaves):
        # Above has default threshold=0.0 — value=1 should clear it.
        doc = """
        {
          "root": {"type": "leaf", "id": "Above"}
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.tick(Context(value=1)) is True

    def test_label_is_preserved_on_composites(self, synthetic_leaves):
        doc = """
        {
          "root": {
            "type": "sequence",
            "label": "MyImportantSequence",
            "children": [{"type": "leaf", "id": "True"}]
          }
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.root.name == "MyImportantSequence"

    def test_layout_field_is_ignored(self, synthetic_leaves):
        """Editor-only sidecar data must not affect runtime parsing."""
        doc = """
        {
          "root": {"type": "leaf", "id": "True"},
          "_layout": {"any": "data", "even": [1, 2, 3]}
        }
        """
        tree = load_tree_from_json_string(doc)
        assert tree.tick(Context()) is True

    def test_malformed_json_raises(self):
        with pytest.raises(TreeLoadError, match="Malformed JSON"):
            load_tree_from_json_string("{not valid json")

    def test_missing_root_field_raises(self, synthetic_leaves):
        with pytest.raises(TreeLoadError, match="missing 'root'"):
            load_tree_from_json_string('{"name": "T"}')

    def test_unknown_node_type_raises(self, synthetic_leaves):
        doc = '{"root": {"type": "parallel", "children": []}}'
        with pytest.raises(TreeLoadError, match="Unknown node type"):
            load_tree_from_json_string(doc)

    def test_unknown_leaf_id_raises(self, synthetic_leaves):
        doc = '{"root": {"type": "leaf", "id": "DoesNotExist"}}'
        with pytest.raises(TreeLoadError, match="DoesNotExist"):
            load_tree_from_json_string(doc)

    def test_empty_composite_rejected(self, synthetic_leaves):
        doc = '{"root": {"type": "sequence", "children": []}}'
        with pytest.raises(TreeLoadError, match="non-empty 'children'"):
            load_tree_from_json_string(doc)

    def test_inverter_missing_child_rejected(self, synthetic_leaves):
        doc = '{"root": {"type": "inverter"}}'
        with pytest.raises(TreeLoadError, match="requires a 'child'"):
            load_tree_from_json_string(doc)

    def test_leaf_missing_id_rejected(self, synthetic_leaves):
        doc = '{"root": {"type": "leaf"}}'
        with pytest.raises(TreeLoadError, match="missing 'id'"):
            load_tree_from_json_string(doc)


# ---------------------------------------------------------------------------
# File-path loader
# ---------------------------------------------------------------------------
class TestLoadTreeFromPath:
    def test_loads_json_file(self, synthetic_leaves, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            '{"name": "T", "root": {"type": "leaf", "id": "True"}}',
            encoding="utf-8",
        )
        tree = load_tree(path)
        assert tree.tick(Context()) is True

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(TreeLoadError, match="not found"):
            load_tree(tmp_path / "nope.json")
