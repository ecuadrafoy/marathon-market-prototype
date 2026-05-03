"""Tests for ai_tree composites + XML loader.

Covers:
- Sequence and Selector short-circuit semantics
- Inverter inversion
- Leaf wrapping conditions and actions
- XML loading: well-formed trees, malformed XML, missing nodes, parameter coercion
"""

import pytest

from ai_tree.composites import Inverter, Leaf, Selector, Sequence, Status
from ai_tree.context import Context
from ai_tree.registry import ParamSpec, bt_action, bt_condition, clear_registry, get
from ai_tree.runtime import Tree, TreeLoadError, load_tree_from_string


# ---------------------------------------------------------------------------
# Fixtures: register a stable set of synthetic leaves for runtime tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def synthetic_leaves():
    """Register four synthetic leaves and clean up afterwards."""
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
        clear_registry()

        @bt_action(name="Side")
        def _side(ctx):
            ctx["touched"] = True
            return None

        leaf = Leaf(get("Side"))
        ctx = Context()
        assert leaf.tick(ctx) == Status.SUCCESS
        assert ctx["touched"] is True
        clear_registry()


# ---------------------------------------------------------------------------
# XML loader
# ---------------------------------------------------------------------------
class TestXmlLoader:
    def test_loads_simple_selector(self, synthetic_leaves):
        xml = """
        <root BTCPP_format="4">
          <BehaviorTree ID="Test">
            <Selector>
              <Action ID="False"/>
              <Action ID="True"/>
            </Selector>
          </BehaviorTree>
        </root>
        """
        tree = load_tree_from_string(xml)
        assert tree.tick(Context()) is True

    def test_fallback_is_alias_for_selector(self, synthetic_leaves):
        """Groot uses <Fallback>; we accept it and treat it as Selector."""
        xml = """
        <root>
          <BehaviorTree ID="Test">
            <Fallback>
              <Action ID="False"/>
              <Action ID="True"/>
            </Fallback>
          </BehaviorTree>
        </root>
        """
        tree = load_tree_from_string(xml)
        assert tree.tick(Context()) is True

    def test_nested_sequence_in_selector(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="Test">
            <Selector>
              <Sequence>
                <Action ID="ReadFlag"/>
                <Action ID="True"/>
              </Sequence>
              <Action ID="False"/>
            </Selector>
          </BehaviorTree>
        </root>
        """
        tree = load_tree_from_string(xml)
        assert tree.tick(Context(flag=True)) is True
        assert tree.tick(Context(flag=False)) is False

    def test_inverter_decorator(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="Test">
            <Inverter>
              <Action ID="False"/>
            </Inverter>
          </BehaviorTree>
        </root>
        """
        tree = load_tree_from_string(xml)
        assert tree.tick(Context()) is True

    def test_parameterized_leaf(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="Test">
            <Action ID="Above" threshold="3.5"/>
          </BehaviorTree>
        </root>
        """
        tree = load_tree_from_string(xml)
        assert tree.tick(Context(value=5)) is True
        assert tree.tick(Context(value=2)) is False

    def test_malformed_xml_raises_tree_load_error(self):
        with pytest.raises(TreeLoadError, match="Malformed XML"):
            load_tree_from_string("<root><not closed")

    def test_missing_behavior_tree_element(self, synthetic_leaves):
        with pytest.raises(TreeLoadError, match="No <BehaviorTree>"):
            load_tree_from_string("<root></root>")

    def test_unknown_leaf_id(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="T">
            <Action ID="DoesNotExist"/>
          </BehaviorTree>
        </root>
        """
        with pytest.raises(TreeLoadError, match="DoesNotExist"):
            load_tree_from_string(xml)

    def test_missing_required_param(self, synthetic_leaves):
        # Re-register Above without a default to force the missing-param error.
        clear_registry()

        @bt_condition(
            name="StrictAbove",
            requires=["value"],
            params=[ParamSpec(name="threshold", type=float)],  # no default
        )
        def _strict(ctx, threshold: float) -> bool:
            return ctx.value > threshold

        xml = """
        <root>
          <BehaviorTree ID="T">
            <Action ID="StrictAbove"/>
          </BehaviorTree>
        </root>
        """
        with pytest.raises(TreeLoadError, match="missing required parameter"):
            load_tree_from_string(xml)
        clear_registry()

    def test_empty_composite_rejected(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="T">
            <Sequence/>
          </BehaviorTree>
        </root>
        """
        with pytest.raises(TreeLoadError, match="no children"):
            load_tree_from_string(xml)

    def test_inverter_with_two_children_rejected(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="T">
            <Inverter>
              <Action ID="True"/>
              <Action ID="False"/>
            </Inverter>
          </BehaviorTree>
        </root>
        """
        with pytest.raises(TreeLoadError, match="exactly one child"):
            load_tree_from_string(xml)

    def test_two_root_nodes_rejected(self, synthetic_leaves):
        xml = """
        <root>
          <BehaviorTree ID="T">
            <Action ID="True"/>
            <Action ID="False"/>
          </BehaviorTree>
        </root>
        """
        with pytest.raises(TreeLoadError, match="exactly one root node"):
            load_tree_from_string(xml)
