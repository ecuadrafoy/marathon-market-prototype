"""Composite and leaf node classes for the behaviour-tree walker.

The semantics follow the standard BehaviorTree.CPP / Groot model:
- **Sequence** ticks children left-to-right, returning FAILURE on the first
  failure; otherwise SUCCESS. (Reads as "AND".)
- **Selector** (alias: Fallback) ticks children left-to-right, returning
  SUCCESS on the first success; otherwise FAILURE. (Reads as "OR".)
- **Inverter** wraps a single child and flips SUCCESS ↔ FAILURE.
- **Leaf** wraps a registered Condition or Action. A Condition's returned
  bool maps True → SUCCESS, False → FAILURE.

RUNNING is included in the Status enum for future async/long-running actions
but is not produced by any composite currently — every tick of every node
yields a terminal Status today.
"""

from __future__ import annotations
from enum import Enum
from typing import Any

from .context import Context
from .registry import NodeKind, NodeSpec


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class Node:
    """Base class — every node must implement tick(ctx) -> Status."""

    def tick(self, ctx: Context) -> Status:
        raise NotImplementedError


class Sequence(Node):
    """AND across children. Returns FAILURE on the first child that fails."""

    def __init__(self, children: list[Node], name: str = "") -> None:
        self.children = children
        self.name = name

    def tick(self, ctx: Context) -> Status:
        for child in self.children:
            result = child.tick(ctx)
            if result != Status.SUCCESS:
                return result
        return Status.SUCCESS

    def __repr__(self) -> str:
        return f"Sequence({len(self.children)} children)"


class Selector(Node):
    """OR across children. Returns SUCCESS on the first child that succeeds.

    Groot/BT.CPP calls this Fallback in its XML. The XML loader accepts both
    <Selector> and <Fallback> tags and constructs the same node type.
    """

    def __init__(self, children: list[Node], name: str = "") -> None:
        self.children = children
        self.name = name

    def tick(self, ctx: Context) -> Status:
        for child in self.children:
            result = child.tick(ctx)
            if result != Status.FAILURE:
                return result
        return Status.FAILURE

    def __repr__(self) -> str:
        return f"Selector({len(self.children)} children)"


class Inverter(Node):
    """Flips SUCCESS ↔ FAILURE. Passes RUNNING through unchanged."""

    def __init__(self, child: Node, name: str = "") -> None:
        self.child = child
        self.name = name

    def tick(self, ctx: Context) -> Status:
        result = self.child.tick(ctx)
        if result == Status.SUCCESS:
            return Status.FAILURE
        if result == Status.FAILURE:
            return Status.SUCCESS
        return result   # RUNNING

    def __repr__(self) -> str:
        return f"Inverter({self.child!r})"


class Leaf(Node):
    """Wraps a registered Condition or Action.

    Conditions return bool — mapped to SUCCESS/FAILURE here so the rest of the
    tree composes uniformly. Per-instance parameter values (e.g., the threshold
    for TimePressureAbove) are passed as keyword arguments to the underlying
    function.
    """

    def __init__(self, spec: NodeSpec, params: dict[str, Any] | None = None) -> None:
        self.spec = spec
        self.params = params or {}

    def tick(self, ctx: Context) -> Status:
        result = self.spec.func(ctx, **self.params)
        if self.spec.kind == NodeKind.CONDITION:
            return Status.SUCCESS if bool(result) else Status.FAILURE
        # Actions may return a Status directly, or None/bool. Be generous.
        if isinstance(result, Status):
            return result
        if result is None or result is True:
            return Status.SUCCESS
        if result is False:
            return Status.FAILURE
        # Anything else: treat as success (the action did *something*).
        return Status.SUCCESS

    def __repr__(self) -> str:
        suffix = f" {self.params}" if self.params else ""
        return f"Leaf({self.spec.name}{suffix})"
