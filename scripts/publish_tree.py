"""CLI for publishing a draft behaviour tree.

Usage:
    uv run python scripts/publish_tree.py <tree_name> [--update-snapshot] [--bless-from-legacy]

<tree_name> is the bare filename (no extension), e.g. extraction_cautious.

Flags:
    --update-snapshot     Regenerate the snapshot from the tree's current outputs.
                          Use when intentionally changing tree behaviour.
    --bless-from-legacy   For first-time publish only: generate the snapshot by
                          calling the legacy should_extract / should_engage on
                          the same grid. Only takes effect if no snapshot file
                          exists yet.

Exit code 0 on success, 1 on any failed check.
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Importing this module triggers leaf registration.
from runner_sim.zone_sim import ai_conditions  # noqa: E402, F401

from ai_tree.context import Context  # noqa: E402
from ai_tree.publisher import TreeKind, infer_doctrine, publish  # noqa: E402


def _legacy_extraction(kind: TreeKind, grid: Iterable[tuple[str, Context]],
                       doctrine) -> dict[str, bool]:
    from runner_sim.zone_sim.extraction_ai import should_extract
    return {
        input_id: should_extract(doctrine, ctx.loot, ctx.perception)
        for input_id, ctx in grid
    }


def _legacy_encounter(kind: TreeKind, grid: Iterable[tuple[str, Context]],
                      doctrine) -> dict[str, bool]:
    from runner_sim.zone_sim.encounter_ai import should_engage
    return {
        input_id: should_engage(
            doctrine, ctx.own_combat, ctx.opponent_combat_estimate, ctx.loot
        )
        for input_id, ctx in grid
    }


def _make_legacy_blesser(name: str):
    """Curry a legacy-blesser appropriate to the tree's kind and doctrine."""
    doctrine = infer_doctrine(name)
    def bless(kind: TreeKind, grid: Iterable[tuple[str, Context]]) -> dict[str, bool]:
        if kind == TreeKind.EXTRACTION:
            return _legacy_extraction(kind, grid, doctrine)
        return _legacy_encounter(kind, grid, doctrine)
    return bless


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a draft behaviour tree.")
    parser.add_argument("name", help="Tree name without .xml extension")
    parser.add_argument("--update-snapshot", action="store_true",
                        help="Bless the current tree output as the new snapshot")
    parser.add_argument("--bless-from-legacy", action="store_true",
                        help="On first publish, compute snapshot from the legacy "
                             "should_extract/should_engage instead of the tree itself")
    args = parser.parse_args()

    bless = _make_legacy_blesser(args.name) if args.bless_from_legacy else None

    result = publish(
        args.name,
        update_snapshot=args.update_snapshot,
        bless_from_legacy=bless,
    )
    print(result.report())
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
