"""XML loader and Tree wrapper.

Parses Groot2 / BehaviorTree.CPP XML format:

    <root BTCPP_format="4">
      <BehaviorTree ID="MyTree">
        <Selector>
          <Action ID="HasUncommonLoot"/>
          <Sequence>
            <Action ID="ZoneFeelsDry"/>
            <Action ID="HadEncounter"/>
          </Sequence>
        </Selector>
      </BehaviorTree>
    </root>

Composite tags supported: <Sequence>, <Selector>, <Fallback> (alias for Selector),
<Inverter>. Leaves use <Action ID="..."> with attribute-style parameters.
"""

from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path

from .composites import Inverter, Leaf, Node, Selector, Sequence, Status
from .context import Context
from .registry import ParamSpec, get


class TreeLoadError(Exception):
    """Raised when an XML tree fails to parse or references unknown nodes."""


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
# XML loading
# ---------------------------------------------------------------------------
_COMPOSITE_TAGS = {"Sequence", "Selector", "Fallback"}
_DECORATOR_TAGS = {"Inverter"}
_LEAF_TAGS = {"Action", "Condition"}


def load_tree(xml_path: str | Path) -> Tree:
    """Parse an XML file and return a Tree."""
    path = Path(xml_path)
    if not path.exists():
        raise TreeLoadError(f"Tree file not found: {path}")
    try:
        doc = ET.parse(path)
    except ET.ParseError as e:
        raise TreeLoadError(f"Malformed XML in {path}: {e}") from e
    return load_tree_from_element(doc.getroot(), source=str(path))


def load_tree_from_string(xml_text: str, source: str = "<string>") -> Tree:
    """Parse an XML string — useful for tests."""
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise TreeLoadError(f"Malformed XML in {source}: {e}") from e
    return load_tree_from_element(root_el, source=source)


def load_tree_from_element(root_el: ET.Element, source: str) -> Tree:
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

    root_node = _build(children[0], source=source)
    return Tree(root=root_node, name=bt_el.get("ID", ""))


def _build(el: ET.Element, source: str) -> Node:
    """Recursively turn an XML element into a Node."""
    tag = el.tag
    name = el.get("name", "")

    if tag in _COMPOSITE_TAGS:
        children = [_build(child, source) for child in el]
        if not children:
            raise TreeLoadError(
                f"Composite <{tag}> in {source} has no children"
            )
        if tag == "Sequence":
            return Sequence(children, name=name)
        # Selector or Fallback (alias)
        return Selector(children, name=name)

    if tag in _DECORATOR_TAGS:
        children = list(el)
        if len(children) != 1:
            raise TreeLoadError(
                f"Decorator <{tag}> in {source} must have exactly one child, "
                f"found {len(children)}"
            )
        return Inverter(_build(children[0], source), name=name)

    if tag in _LEAF_TAGS:
        node_id = el.get("ID")
        if not node_id:
            raise TreeLoadError(f"Leaf <{tag}> in {source} missing ID attribute")
        try:
            spec = get(node_id)
        except KeyError as e:
            raise TreeLoadError(str(e)) from e
        params = _coerce_params(el, spec.params, source=source, node_id=node_id)
        return Leaf(spec, params=params)

    raise TreeLoadError(f"Unknown tag <{tag}> in {source}")


def _coerce_params(
    el: ET.Element,
    param_specs: tuple[ParamSpec, ...],
    source: str,
    node_id: str,
) -> dict[str, object]:
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
