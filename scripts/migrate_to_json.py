"""One-shot migration: convert all XML trees in ai_trees/ to JSON.

Reads drafts/*.xml, emits drafts/*.json with semantically identical content,
then removes the .xml files. The publisher will subsequently regenerate
published/*.json on the next publish run.

This script is disposable — once the JSON migration is complete and the XML
parser is removed from runtime.py, it serves no further purpose and can be
deleted along with the XML support code.

Run:
    uv run python scripts/migrate_to_json.py
"""

from __future__ import annotations
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = REPO_ROOT / "ai_trees" / "drafts"


def _coerce_attr(value: str):
    """Convert XML string attributes to native JSON types where possible."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def xml_node_to_json(el: ET.Element) -> dict:
    """Convert one XML node element into our JSON node format."""
    label = el.get("name", "")

    if el.tag == "Sequence":
        out = {"type": "sequence",
               "children": [xml_node_to_json(c) for c in el]}
        if label:
            out["label"] = label
        return out

    if el.tag in ("Selector", "Fallback"):
        out = {"type": "selector",
               "children": [xml_node_to_json(c) for c in el]}
        if label:
            out["label"] = label
        return out

    if el.tag == "Inverter":
        kids = list(el)
        if len(kids) != 1:
            raise ValueError(f"<Inverter> needs exactly one child, has {len(kids)}")
        out = {"type": "inverter", "child": xml_node_to_json(kids[0])}
        if label:
            out["label"] = label
        return out

    if el.tag in ("Action", "Condition"):
        node_id = el.get("ID")
        if not node_id:
            raise ValueError(f"<{el.tag}> missing ID attribute")
        out = {"type": "leaf", "id": node_id}
        # Every attribute except ID and name becomes a parameter.
        params = {
            k: _coerce_attr(v)
            for k, v in el.attrib.items()
            if k not in ("ID", "name")
        }
        if params:
            out["params"] = params
        if label:
            out["label"] = label
        return out

    raise ValueError(f"Unknown XML tag <{el.tag}>")


def xml_tree_file_to_json(xml_path: Path) -> dict:
    """Convert a full XML tree file (root → BehaviorTree → node) to JSON."""
    doc = ET.parse(xml_path).getroot()
    bt = doc if doc.tag == "BehaviorTree" else doc.find("BehaviorTree")
    if bt is None:
        raise ValueError(f"No <BehaviorTree> in {xml_path}")
    children = list(bt)
    if len(children) != 1:
        raise ValueError(f"<BehaviorTree> in {xml_path} must have one root, "
                         f"has {len(children)}")
    return {"name": bt.get("ID", ""), "root": xml_node_to_json(children[0])}


def main() -> int:
    xml_files = sorted(DRAFTS_DIR.glob("*.xml"))
    if not xml_files:
        print("No XML drafts found — migration already complete.")
        return 0

    print(f"Migrating {len(xml_files)} draft tree(s) to JSON…")
    for xml_path in xml_files:
        json_path = xml_path.with_suffix(".json")
        try:
            doc = xml_tree_file_to_json(xml_path)
        except Exception as e:
            print(f"  FAIL {xml_path.name}: {e}")
            return 1
        json_path.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        xml_path.unlink()
        print(f"  OK   {xml_path.name} -> {json_path.name}")

    print(f"\nMigration complete. {len(xml_files)} draft(s) converted.")
    print("Next: run `uv run python scripts/publish_tree.py <name>` for each")
    print("      tree to regenerate published JSON + update the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
