"""Tests for ai_tree/registry.py — decorator side effects and lookup."""

import pytest

from ai_tree.registry import (
    REGISTRY,
    NodeKind,
    ParamSpec,
    bt_action,
    bt_condition,
    clear_registry,
    get,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty REGISTRY so decorators in one test
    don't leak into another. The original contents (e.g., game leaves
    registered by ai_conditions) are saved and restored on teardown so
    other test files aren't left with an empty registry."""
    saved = dict(REGISTRY)
    clear_registry()
    yield
    clear_registry()
    REGISTRY.update(saved)


class TestBtCondition:
    def test_decoration_registers_node(self):
        @bt_condition(name="AlwaysTrue", category="Test", description="Always returns True")
        def always_true(ctx) -> bool:
            return True

        assert "AlwaysTrue" in REGISTRY
        spec = REGISTRY["AlwaysTrue"]
        assert spec.kind == NodeKind.CONDITION
        assert spec.category == "Test"
        assert spec.description == "Always returns True"
        assert spec.func is always_true

    def test_function_remains_callable_after_decoration(self):
        @bt_condition(name="ReturnsArg")
        def returns_arg(ctx) -> bool:
            return ctx

        # The decorated function should still work as a normal Python function
        assert returns_arg(True) is True
        assert returns_arg(False) is False

    def test_requires_recorded_as_tuple(self):
        @bt_condition(name="NeedsLoot", requires=["loot", "perception"])
        def needs_loot(ctx) -> bool:
            return True

        assert REGISTRY["NeedsLoot"].requires == ("loot", "perception")

    def test_params_recorded_as_tuple(self):
        threshold = ParamSpec(name="threshold", type=float, default=0.5)
        @bt_condition(name="Param", params=[threshold])
        def param(ctx, threshold: float = 0.5) -> bool:
            return True

        assert REGISTRY["Param"].params == (threshold,)


class TestBtAction:
    def test_action_kind_is_distinct_from_condition(self):
        @bt_action(name="DoStuff")
        def do_stuff(ctx):
            return None

        assert REGISTRY["DoStuff"].kind == NodeKind.ACTION


class TestDuplicateRegistration:
    def test_duplicate_name_raises(self):
        @bt_condition(name="Dup")
        def first(ctx) -> bool:
            return True

        with pytest.raises(ValueError, match="Duplicate BT node name"):
            @bt_condition(name="Dup")
            def second(ctx) -> bool:
                return False

    def test_duplicate_across_kinds_also_raises(self):
        """Action and Condition share the same namespace — names must be globally unique."""
        @bt_condition(name="Shared")
        def first(ctx) -> bool:
            return True

        with pytest.raises(ValueError, match="Duplicate BT node name"):
            @bt_action(name="Shared")
            def second(ctx):
                return None


class TestGet:
    def test_get_returns_spec(self):
        @bt_condition(name="Findable")
        def findable(ctx) -> bool:
            return True

        spec = get("Findable")
        assert spec.name == "Findable"
        assert spec.func is findable

    def test_get_unknown_raises_keyerror_with_available_list(self):
        @bt_condition(name="OnlyOne")
        def only_one(ctx) -> bool:
            return True

        with pytest.raises(KeyError, match="OnlyOne"):
            get("Missing")
