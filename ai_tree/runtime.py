"""Tree loader (JSON) and Tree wrapper.

JSON node shape (nested):

    {
      "name": "EncounterBalanced",
      "root": {
        "type": "selector",                     // sequence | selector | inverter | leaf
        "label": "BalancedEngageDecision",      // optional, editor display only
        "children": [...],                      // composites
        "child":  {...},                        // inverter
        "id":     "OpponentHelpless",           // leaf
        "params": {"threshold": 1.2}            // leaf, optional
      },
      "_layout": {...}                          // editor sidecar; runtime ignores
    }

The runtime ignores any field not in this schema (notably `_layout`, which
the editor uses to persist node positions). This keeps the parser strict
about behaviourally-relevant data while leaving room for editor evolution.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from .composites import Inverter, Leaf, Node, Selector, Sequence, Status
from .context import Context
from .registry import ParamSpec, get


class TreeLoadError(Exception):
    """Raised when a tree fails to parse or references unknown nodes."""


class Tree:
    """A parsed behaviour tree, ready to tick.

    Wraps a root node and exposes both the strict Status-returning evaluate()
    and a convenience tick() that returns bool for the common decision-tree
    use case (where SUCCESS at the root means "yes, do the thing").
    """

    def __init__(self, root: Node, name: str = "") -> None:
        self.root = root
        self.name = name

    def evaluate(self, ctx: Context) -> Status:
        return self.root.tick(ctx)

    def tick(self, ctx: Context) -> bool:
        """Return True iff the root yielded SUCCESS — the boolean decision-tree API."""
        return self.evaluate(ctx) == Status.SUCCESS

    def __repr__(self) -> str:
        return f"Tree(name={self.name!r}, root={self.root!r})"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_tree(path: str | Path) -> Tree:
    """Parse a JSON tree file and return a Tree."""
    path = Path(path)
    if not path.exists():
        raise TreeLoadError(f"Tree file not found: {path}")
    return load_tree_from_json_string(path.read_text(encoding="utf-8"),
                                      source=str(path))


def load_tree_from_json_string(json_text: str, source: str = "<string>") -> Tree:
    """Parse a JSON string and build a Tree."""
    try:
        doc = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise TreeLoadError(f"Malformed JSON in {source}: {e}") from e
    return _load_tree_from_doc(doc, source=source)


def _load_tree_from_doc(doc: Any, source: str) -> Tree:
    if not isinstance(doc, dict):
        raise TreeLoadError(f"JSON root in {source} must be an object")
    if "root" not in doc:
        raise TreeLoadError(f"JSON tree in {source} missing 'root' field")
    root_node = _build(doc["root"], source=source, path="root")
    return Tree(root=root_node, name=doc.get("name", ""))


_COMPOSITE_TYPES = {"sequence", "selector"}
_DECORATOR_TYPES = {"inverter"}
_LEAF_TYPES = {"leaf"}


def _build(node: Any, source: str, path: str) -> Node:
    """Recursively turn a JSON node dict into a Node."""
    if not isinstance(node, dict):
        raise TreeLoadError(f"Node at {path} in {source} must be an object")
    if "type" not in node:
        raise TreeLoadError(f"Node at {path} in {source} missing 'type' field")
    node_type = node["type"]
    label = node.get("label", "")

    if node_type in _COMPOSITE_TYPES:
        raw_children = node.get("children")
        if not isinstance(raw_children, list) or not raw_children:
            raise TreeLoadError(
                f"Composite {node_type!r} at {path} in {source} requires a "
                f"non-empty 'children' array"
            )
        children = [
            _build(c, source, f"{path}.children[{i}]")
            for i, c in enumerate(raw_children)
        ]
        if node_type == "sequence":
            return Sequence(children, name=label)
        return Selector(children, name=label)

    if node_type in _DECORATOR_TYPES:
        child = node.get("child")
        if child is None:
            raise TreeLoadError(
                f"Decorator {node_type!r} at {path} in {source} requires a "
                f"'child' field"
            )
        return Inverter(_build(child, source, f"{path}.child"), name=label)

    if node_type in _LEAF_TYPES:
        node_id = node.get("id")
        if not node_id:
            raise TreeLoadError(
                f"Leaf at {path} in {source} missing 'id' field"
            )
        try:
            spec = get(node_id)
        except KeyError as e:
            raise TreeLoadError(str(e)) from e
        params = _coerce_params(
            node.get("params") or {}, spec.params,
            source=source, node_id=node_id, path=path,
        )
        return Leaf(spec, params=params)

    raise TreeLoadError(f"Unknown node type {node_type!r} at {path} in {source}")


def _coerce_params(raw: dict[str, Any], param_specs: tuple[ParamSpec, ...],
                   source: str, node_id: str, path: str) -> dict[str, object]:
    """Validate and coerce JSON params to their declared types."""
    if not isinstance(raw, dict):
        raise TreeLoadError(
            f"Leaf {node_id!r} at {path} in {source}: 'params' must be an object"
        )
    out: dict[str, object] = {}
    for spec in param_specs:
        if spec.name not in raw:
            if spec.default is None:
                raise TreeLoadError(
                    f"Leaf {node_id!r} at {path} in {source} missing required "
                    f"parameter {spec.name!r}"
                )
            out[spec.name] = spec.default
            continue
        value = raw[spec.name]
        try:
            if spec.type is bool:
                out[spec.name] = bool(value)
            else:
                out[spec.name] = spec.type(value)
        except (TypeError, ValueError) as e:
            raise TreeLoadError(
                f"Leaf {node_id!r} at {path} in {source}: parameter "
                f"{spec.name!r} could not be coerced to {spec.type.__name__}: "
                f"{value!r} ({e})"
            ) from e
    return out
