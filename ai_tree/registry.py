"""Registry of available leaf nodes for the behaviour-tree engine.

Leaf nodes are Python functions decorated with `@bt_condition` or `@bt_action`.
The decorator records a NodeSpec in the global REGISTRY at import time, so any
module that defines leaves only needs to be imported once for the editor and
runtime to see every available node.

The editor server (ai_tree.server) walks REGISTRY to serve `/catalog`, which
populates the visual editor's palette. The runtime (ai_tree.runtime) looks up
leaves by name when constructing a Tree from JSON.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class NodeKind(str, Enum):
    """Whether a leaf returns a boolean (Condition) or performs an effect (Action).

    The runtime treats both as Leaf nodes — the distinction exists so the
    editor's palette can group and colour them differently, and so that
    future static checks can warn if an Action is used where a Condition
    is expected.
    """
    CONDITION = "condition"
    ACTION = "action"


@dataclass(frozen=True)
class ParamSpec:
    """A configurable parameter on a leaf node instance.

    Example: TimePressureAbove takes `threshold: float` so each placement of
    that node in a tree can have its own value. The catalog endpoint exposes
    these so the editor renders them as editable fields in its property
    panel; tree JSON stores them under a leaf's `params` object.
    """
    name: str
    type: type           # float, int, str, bool — kept simple on purpose
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class NodeSpec:
    """Everything the catalog and runtime need to know about one leaf."""
    name: str                                  # the leaf ID, e.g. "HasUncommonLoot"
    kind: NodeKind
    category: str                              # for palette grouping
    description: str
    func: Callable[..., bool]                  # the actual implementation
    requires: tuple[str, ...] = ()             # context attribute names this leaf reads
    params: tuple[ParamSpec, ...] = ()         # configurable per-instance parameters


REGISTRY: dict[str, NodeSpec] = {}


def bt_condition(
    name: str,
    *,
    category: str = "Misc",
    description: str = "",
    requires: list[str] | None = None,
    params: list[ParamSpec] | None = None,
) -> Callable[[Callable], Callable]:
    """Register a function as a Condition leaf node.

    The decorated function should accept (ctx, **params) and return bool.
    Registration happens at decoration time, so a duplicate name raises
    immediately rather than producing a silent override.
    """
    def decorator(func: Callable[..., bool]) -> Callable[..., bool]:
        if name in REGISTRY:
            raise ValueError(
                f"Duplicate BT node name: {name!r} "
                f"(already registered by {REGISTRY[name].func.__module__}.{REGISTRY[name].func.__name__})"
            )
        REGISTRY[name] = NodeSpec(
            name=name,
            kind=NodeKind.CONDITION,
            category=category,
            description=description,
            func=func,
            requires=tuple(requires or ()),
            params=tuple(params or ()),
        )
        return func
    return decorator


def bt_action(
    name: str,
    *,
    category: str = "Misc",
    description: str = "",
    requires: list[str] | None = None,
    params: list[ParamSpec] | None = None,
) -> Callable[[Callable], Callable]:
    """Register a function as an Action leaf node.

    Currently unused by the existing AI (which is purely decision-based) but
    defined for symmetry — when squad-dispatch trees land, they'll need
    Actions like ChooseHardestZone, AssignToSquad, etc.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in REGISTRY:
            raise ValueError(
                f"Duplicate BT node name: {name!r} "
                f"(already registered by {REGISTRY[name].func.__module__}.{REGISTRY[name].func.__name__})"
            )
        REGISTRY[name] = NodeSpec(
            name=name,
            kind=NodeKind.ACTION,
            category=category,
            description=description,
            func=func,
            requires=tuple(requires or ()),
            params=tuple(params or ()),
        )
        return func
    return decorator


def clear_registry() -> None:
    """Test helper — wipe the registry between tests that register synthetic nodes."""
    REGISTRY.clear()


def get(name: str) -> NodeSpec:
    """Look up a node by name. Raises KeyError with a helpful message if missing."""
    if name not in REGISTRY:
        available = ", ".join(sorted(REGISTRY.keys())) or "(none registered)"
        raise KeyError(f"BT node {name!r} is not registered. Available: {available}")
    return REGISTRY[name]
