"""Tree loader (XML and JSON) and Tree wrapper.

During the transition from Groot/XML to a custom JSON-based editor, the runtime
supports both formats. `load_tree(path)` dispatches on extension:

- `*.xml` — Groot2 / BehaviorTree.CPP format (legacy, removed once migration completes)
- `*.json` — our nested format authored by the custom editor

Both formats produce the same Node graph; the parsers are separate but the
walker is shared.

XML composite tags supported: <Sequence>, <Selector>, <Fallback> (alias for
Selector), <Inverter>. Leaves use <Action ID="..."> or <Condition ID="...">
with attribute-style parameters.

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
"""

from __future__ import annotations
import json
import xml.etree.ElementTree as ET
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
# Public dispatch
# ---------------------------------------------------------------------------
def load_tree(path: str | Path) -> Tree:
    """Parse a tree file (XML or JSON) and return a Tree.

    Dispatches on file extension. Both formats produce semantically identical
    Tree objects.
    """
    path = Path(path)
    if not path.exists():
        raise TreeLoadError(f"Tree file not found: {path}")
    if path.suffix.lower() == ".json":
        return load_tree_from_json_string(path.read_text(encoding="utf-8"),
                                          source=str(path))
    return load_tree_from_xml_string(path.read_text(encoding="utf-8"),
                                     source=str(path))


# ---------------------------------------------------------------------------
# XML loading
# ---------------------------------------------------------------------------
_XML_COMPOSITE_TAGS = {"Sequence", "Selector", "Fallback"}
_XML_DECORATOR_TAGS = {"Inverter"}
_XML_LEAF_TAGS = {"Action", "Condition"}


def load_tree_from_xml_string(xml_text: str, source: str = "<string>") -> Tree:
    """Parse an XML string — useful for tests and editor previews."""
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise TreeLoadError(f"Malformed XML in {source}: {e}") from e
    return _load_tree_from_xml_element(root_el, source=source)


# Backwards-compatible alias for the XML-only API used in older tests.
load_tree_from_string = load_tree_from_xml_string


def _load_tree_from_xml_element(root_el: ET.Element, source: str) -> Tree:
    """Find the <BehaviorTree> element and build a Tree from its child node."""
    if root_el.tag == "BehaviorTree":
        bt_el = root_el
    else:
        bt_el = root_el.find("BehaviorTree")
    if bt_el is None:
        raise TreeLoadError(f"No <BehaviorTree> element in {source}")

    children = list(bt_el)
    if len(children) != 1:
        raise TreeLoadError(
            f"<BehaviorTree> in {source} must have exactly one root node, "
            f"found {len(children)}"
        )

    root_node = _build_from_xml(children[0], source=source)
    return Tree(root=root_node, name=bt_el.get("ID", ""))


def _build_from_xml(el: ET.Element, source: str) -> Node:
    """Recursively turn an XML element into a Node."""
    tag = el.tag
    name = el.get("name", "")

    if tag in _XML_COMPOSITE_TAGS:
        children = [_build_from_xml(child, source) for child in el]
        if not children:
            raise TreeLoadError(f"Composite <{tag}> in {source} has no children")
        if tag == "Sequence":
            return Sequence(children, name=name)
        return Selector(children, name=name)   # Selector or Fallback (alias)

    if tag in _XML_DECORATOR_TAGS:
        children = list(el)
        if len(children) != 1:
            raise TreeLoadError(
                f"Decorator <{tag}> in {source} must have exactly one child, "
                f"found {len(children)}"
            )
        return Inverter(_build_from_xml(children[0], source), name=name)

    if tag in _XML_LEAF_TAGS:
        node_id = el.get("ID")
        if not node_id:
            raise TreeLoadError(f"Leaf <{tag}> in {source} missing ID attribute")
        try:
            spec = get(node_id)
        except KeyError as e:
            raise TreeLoadError(str(e)) from e
        params = _coerce_xml_params(el, spec.params, source=source, node_id=node_id)
        return Leaf(spec, params=params)

    raise TreeLoadError(f"Unknown tag <{tag}> in {source}")


def _coerce_xml_params(el: ET.Element, param_specs: tuple[ParamSpec, ...],
                       source: str, node_id: str) -> dict[str, object]:
    """Read XML attributes into a typed param dict, applying defaults."""
    out: dict[str, object] = {}
    for spec in param_specs:
        raw = el.get(spec.name)
        if raw is None:
            if spec.default is None:
                raise TreeLoadError(
                    f"Leaf {node_id!r} in {source} missing required parameter "
                    f"{spec.name!r}"
                )
            out[spec.name] = spec.default
            continue
        try:
            if spec.type is bool:
                out[spec.name] = raw.lower() in ("true", "1", "yes")
            else:
                out[spec.name] = spec.type(raw)
        except (TypeError, ValueError) as e:
            raise TreeLoadError(
                f"Leaf {node_id!r} in {source}: parameter {spec.name!r} "
                f"could not be coerced to {spec.type.__name__}: {raw!r} ({e})"
            ) from e
    return out


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------
_JSON_COMPOSITE_TYPES = {"sequence", "selector"}
_JSON_DECORATOR_TYPES = {"inverter"}
_JSON_LEAF_TYPES = {"leaf"}


def load_tree_from_json_string(json_text: str, source: str = "<string>") -> Tree:
    """Parse a JSON string and build a Tree."""
    try:
        doc = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise TreeLoadError(f"Malformed JSON in {source}: {e}") from e
    return _load_tree_from_json_doc(doc, source=source)


def _load_tree_from_json_doc(doc: Any, source: str) -> Tree:
    if not isinstance(doc, dict):
        raise TreeLoadError(f"JSON root in {source} must be an object")
    if "root" not in doc:
        raise TreeLoadError(f"JSON tree in {source} missing 'root' field")
    root_node = _build_from_json(doc["root"], source=source, path="root")
    return Tree(root=root_node, name=doc.get("name", ""))


def _build_from_json(node: Any, source: str, path: str) -> Node:
    """Recursively turn a JSON node dict into a Node."""
    if not isinstance(node, dict):
        raise TreeLoadError(f"Node at {path} in {source} must be an object")
    if "type" not in node:
        raise TreeLoadError(f"Node at {path} in {source} missing 'type' field")
    node_type = node["type"]
    label = node.get("label", "")

    if node_type in _JSON_COMPOSITE_TYPES:
        raw_children = node.get("children")
        if not isinstance(raw_children, list) or not raw_children:
            raise TreeLoadError(
                f"Composite {node_type!r} at {path} in {source} requires a "
                f"non-empty 'children' array"
            )
        children = [
            _build_from_json(c, source, f"{path}.children[{i}]")
            for i, c in enumerate(raw_children)
        ]
        if node_type == "sequence":
            return Sequence(children, name=label)
        return Selector(children, name=label)

    if node_type in _JSON_DECORATOR_TYPES:
        child = node.get("child")
        if child is None:
            raise TreeLoadError(
                f"Decorator {node_type!r} at {path} in {source} requires a "
                f"'child' field"
            )
        return Inverter(_build_from_json(child, source, f"{path}.child"),
                        name=label)

    if node_type in _JSON_LEAF_TYPES:
        node_id = node.get("id")
        if not node_id:
            raise TreeLoadError(
                f"Leaf at {path} in {source} missing 'id' field"
            )
        try:
            spec = get(node_id)
        except KeyError as e:
            raise TreeLoadError(str(e)) from e
        params = _coerce_json_params(
            node.get("params") or {}, spec.params,
            source=source, node_id=node_id, path=path,
        )
        return Leaf(spec, params=params)

    raise TreeLoadError(f"Unknown node type {node_type!r} at {path} in {source}")


def _coerce_json_params(raw: dict[str, Any], param_specs: tuple[ParamSpec, ...],
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
