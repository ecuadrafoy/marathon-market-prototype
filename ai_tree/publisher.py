"""Publish gate: lint + snapshot validation + manifest update.

A draft tree (in `ai_trees/drafts/`) is only allowed to enter `ai_trees/published/`
after passing four checks:

1. **Schema**     — the XML parses and conforms to our composite/leaf vocabulary
2. **Registry**   — every leaf ID exists; required parameters are supplied
3. **Smoke load** — the runtime can construct the full Tree from the XML
4. **Snapshot**   — evaluating the tree on a fixed input grid matches the
                    sibling `*.snapshot.json` exactly (or `--update-snapshot`
                    was passed to bless a deliberate change)

Once all four pass, the draft is copied to `published/`, the snapshot file is
written (or refreshed), and `manifest.json` is updated with a SHA256 of the
published XML. The runtime refuses to load any tree absent from the manifest
or whose checksum doesn't match — so unpublished work-in-progress trees can't
silently leak into a sim run.
"""

from __future__ import annotations
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from .context import Context
from .runtime import Tree, TreeLoadError, load_tree


REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = REPO_ROOT / "ai_trees" / "drafts"
PUBLISHED_DIR = REPO_ROOT / "ai_trees" / "published"
MANIFEST_PATH = REPO_ROOT / "ai_trees" / "manifest.json"


# ---------------------------------------------------------------------------
# Verified runtime loader
# ---------------------------------------------------------------------------
class UnverifiedTreeError(Exception):
    """The runtime refused to load a tree because it is missing from the
    manifest or its checksum does not match the manifest entry."""


def load_published(name: str) -> Tree:
    """Load a published tree after verifying it appears in the manifest with
    a matching SHA256. This is the only sanctioned way for the simulator to
    load a tree — drafts cannot be loaded this way, and tampering with a
    published file invalidates its checksum.
    """
    manifest = load_manifest()
    if name not in manifest:
        raise UnverifiedTreeError(
            f"Tree {name!r} is not in the manifest. "
            f"Run `uv run python scripts/publish_tree.py {name}` to publish it."
        )
    path = PUBLISHED_DIR / f"{name}.xml"
    if not path.exists():
        raise UnverifiedTreeError(
            f"Published tree file is missing: {path}. "
            f"Manifest is out of sync — republish or remove the manifest entry."
        )
    actual_sha = _sha256_of(path)
    expected_sha = manifest[name].sha256
    if actual_sha != expected_sha:
        raise UnverifiedTreeError(
            f"Tree {name!r} has been modified since publish. "
            f"Expected SHA {expected_sha[:12]}..., got {actual_sha[:12]}.... "
            f"Republish to validate the change."
        )
    return load_tree(path)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Diagnostic:
    severity: Severity
    check: str           # which check produced this (schema/registry/smoke/snapshot)
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}:{self.check}] {self.message}"


# ---------------------------------------------------------------------------
# Tree kinds and grids
# ---------------------------------------------------------------------------
class TreeKind(str, Enum):
    EXTRACTION = "extraction"
    ENCOUNTER = "encounter"


def infer_kind(tree_name: str) -> TreeKind:
    """Tree kind is derived from the filename prefix (extraction_* / encounter_*)."""
    if tree_name.startswith("extraction_"):
        return TreeKind.EXTRACTION
    if tree_name.startswith("encounter_"):
        return TreeKind.ENCOUNTER
    raise ValueError(
        f"Cannot infer tree kind for {tree_name!r} — name must start with "
        f"'extraction_' or 'encounter_'."
    )


def infer_doctrine(tree_name: str):
    """Tree's doctrine is the suffix after the kind prefix."""
    from runner_sim.zone_sim.extraction_ai import Doctrine
    suffix = tree_name.split("_", 1)[1]
    try:
        return Doctrine(suffix)
    except ValueError as e:
        raise ValueError(
            f"Cannot infer doctrine from {tree_name!r}: suffix {suffix!r} "
            f"is not a known doctrine."
        ) from e


def extraction_grid() -> Iterable[tuple[str, Context]]:
    """Enumerate every behaviourally-relevant input for an extraction tree.

    Axes covered (Cartesian product):
    - tick stage: early/mid/late/final
    - dryness: fresh vs dry
    - encounter: had vs none
    - damage: took vs none
    - loot tier: empty / common / uncommon / rare / epic
    """
    from runner_sim.zone_sim.extraction_ai import (
        Item, SquadLoot, SquadPerception, Tier,
    )

    MAX = 10
    tick_stages = [
        ("early", 1),
        ("mid",   5),
        ("late",  8),
        ("final", 10),
    ]
    dryness = [("fresh", 0), ("dry", 4)]
    encounter_flags = [("noencounter", False), ("encounter", True)]
    damage_flags = [("nodamage", False), ("damage", True)]
    loot_kinds: list[tuple[str, list[Item]]] = [
        ("empty",    []),
        ("common",   [Item("c", Tier.COMMON,   1, {})]),
        ("uncommon", [Item("u", Tier.UNCOMMON, 1, {})]),
        ("rare",     [Item("r", Tier.RARE,     1, {})]),
        ("epic",     [Item("e", Tier.EPIC,     1, {})]),
    ]

    for tick_label, tick in tick_stages:
        for dry_label, ticks_since in dryness:
            for enc_label, enc in encounter_flags:
                for dmg_label, dmg in damage_flags:
                    for loot_label, loot_items in loot_kinds:
                        input_id = "|".join(
                            (tick_label, dry_label, enc_label, dmg_label, loot_label)
                        )
                        ctx = Context(
                            perception=SquadPerception(
                                ticks_since_last_find=ticks_since,
                                had_encounter_this_run=enc,
                                took_damage_this_run=dmg,
                                tick=tick,
                                max_ticks=MAX,
                            ),
                            loot=SquadLoot(items=list(loot_items)),
                        )
                        yield input_id, ctx


def encounter_grid() -> Iterable[tuple[str, Context]]:
    """Enumerate every behaviourally-relevant input for an encounter tree.

    Axes covered:
    - own_combat: low/mid/high
    - opponent_combat_estimate: helpless / weak / even / strong / overwhelming
    - own_loot tier: empty / common / uncommon / rare / epic
    """
    from runner_sim.zone_sim.extraction_ai import Item, SquadLoot, Tier

    own_combats = [("ownlow", 1.0), ("ownmid", 5.0), ("ownhigh", 10.0)]
    opponent_combats = [
        ("oppzero",  0.0),
        ("oppweak",  2.0),
        ("oppeven",  5.0),
        ("oppstrong", 8.0),
        ("oppmax",   15.0),
    ]
    loot_kinds: list[tuple[str, list[Item]]] = [
        ("empty",    []),
        ("common",   [Item("c", Tier.COMMON,   1, {})]),
        ("uncommon", [Item("u", Tier.UNCOMMON, 1, {})]),
        ("rare",     [Item("r", Tier.RARE,     1, {})]),
        ("epic",     [Item("e", Tier.EPIC,     1, {})]),
    ]

    for own_label, own in own_combats:
        for opp_label, opp in opponent_combats:
            for loot_label, loot_items in loot_kinds:
                input_id = "|".join((own_label, opp_label, loot_label))
                yield input_id, Context(
                    own_combat=own,
                    opponent_combat_estimate=opp,
                    loot=SquadLoot(items=list(loot_items)),
                )


def grid_for(kind: TreeKind) -> Iterable[tuple[str, Context]]:
    return extraction_grid() if kind == TreeKind.EXTRACTION else encounter_grid()


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
def lint(draft_path: Path) -> tuple[Tree | None, list[Diagnostic]]:
    """Run schema + registry + smoke checks. Returns the loaded Tree on success."""
    if not draft_path.exists():
        return None, [Diagnostic(Severity.ERROR, "schema",
                                 f"draft file not found: {draft_path}")]
    try:
        tree = load_tree(draft_path)
    except TreeLoadError as e:
        return None, [Diagnostic(Severity.ERROR, "schema", str(e))]
    return tree, []


# ---------------------------------------------------------------------------
# Snapshot evaluation and comparison
# ---------------------------------------------------------------------------
def evaluate_on_grid(tree: Tree, kind: TreeKind) -> dict[str, bool]:
    """Run the tree against every grid input. Returns {input_id: result}."""
    return {input_id: tree.tick(ctx) for input_id, ctx in grid_for(kind)}


def diff_snapshots(expected: dict[str, bool], actual: dict[str, bool]) -> list[str]:
    """Return human-readable diffs between two snapshot dicts."""
    diffs: list[str] = []
    expected_keys = set(expected.keys())
    actual_keys = set(actual.keys())

    only_expected = expected_keys - actual_keys
    only_actual = actual_keys - expected_keys
    for k in sorted(only_expected):
        diffs.append(f"missing input in result: {k} (expected {expected[k]})")
    for k in sorted(only_actual):
        diffs.append(f"new input not in snapshot: {k} (got {actual[k]})")

    for k in sorted(expected_keys & actual_keys):
        if expected[k] != actual[k]:
            diffs.append(f"{k}: expected {expected[k]}, got {actual[k]}")
    return diffs


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
@dataclass
class ManifestEntry:
    name: str
    sha256: str
    published_at: str   # ISO8601 UTC
    grid_size: int


def load_manifest() -> dict[str, ManifestEntry]:
    if not MANIFEST_PATH.exists():
        return {}
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        name: ManifestEntry(name=name, **entry)
        for name, entry in raw.items()
    }


def save_manifest(entries: dict[str, ManifestEntry]) -> None:
    serialised = {
        name: {"sha256": e.sha256, "published_at": e.published_at, "grid_size": e.grid_size}
        for name, e in sorted(entries.items())
    }
    MANIFEST_PATH.write_text(
        json.dumps(serialised, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
@dataclass
class PublishResult:
    name: str
    success: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    grid_size: int = 0
    snapshot_updated: bool = False

    def report(self) -> str:
        lines = [f"Publish {self.name}: {'OK' if self.success else 'FAILED'}"]
        for d in self.diagnostics:
            lines.append(f"  {d}")
        if self.success:
            lines.append(f"  grid_size={self.grid_size}")
            if self.snapshot_updated:
                lines.append("  snapshot updated")
        return "\n".join(lines)


def publish(
    name: str,
    *,
    update_snapshot: bool = False,
    bless_from_legacy: Callable[[TreeKind, Iterable[tuple[str, Context]]],
                                dict[str, bool]] | None = None,
) -> PublishResult:
    """Validate and publish the named draft tree.

    name: bare tree name (no extension), e.g. "extraction_cautious"
    update_snapshot: regenerate the snapshot from the tree's current outputs,
        bypassing the diff check. Use when intentionally changing behaviour.
    bless_from_legacy: callable that, given (kind, grid), returns the
        snapshot dict computed by the *legacy* should_extract / should_engage.
        Used for first-time publish during migration so the published tree
        is provably equivalent to the original code at publish time.
    """
    draft_path = DRAFTS_DIR / f"{name}.xml"
    published_path = PUBLISHED_DIR / f"{name}.xml"
    snapshot_path = PUBLISHED_DIR / f"{name}.snapshot.json"

    # Lint
    tree, diags = lint(draft_path)
    if tree is None:
        return PublishResult(name=name, success=False, diagnostics=diags)

    try:
        kind = infer_kind(name)
    except ValueError as e:
        diags.append(Diagnostic(Severity.ERROR, "naming", str(e)))
        return PublishResult(name=name, success=False, diagnostics=diags)

    # Evaluate on grid
    try:
        actual = evaluate_on_grid(tree, kind)
    except Exception as e:
        diags.append(Diagnostic(
            Severity.ERROR, "smoke",
            f"tree raised during grid evaluation: {type(e).__name__}: {e}"
        ))
        return PublishResult(name=name, success=False, diagnostics=diags)

    # Determine expected snapshot
    snapshot_existed = snapshot_path.exists()
    snapshot_updated = False
    if bless_from_legacy is not None and not snapshot_existed:
        # Bootstrap: the legacy implementation defines the contract for first publish.
        expected = bless_from_legacy(kind, grid_for(kind))
        snapshot_updated = True
    elif update_snapshot or not snapshot_existed:
        expected = actual
        snapshot_updated = True
    else:
        expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    # Compare
    if not snapshot_updated:
        diffs = diff_snapshots(expected, actual)
        if diffs:
            for line in diffs[:20]:
                diags.append(Diagnostic(Severity.ERROR, "snapshot", line))
            if len(diffs) > 20:
                diags.append(Diagnostic(
                    Severity.ERROR, "snapshot",
                    f"... and {len(diffs) - 20} more differences (truncated)"
                ))
            return PublishResult(name=name, success=False, diagnostics=diags,
                                 grid_size=len(actual))
    else:
        # Bootstrap or --update-snapshot — make sure the tree at least matches
        # the expected (which is either legacy-blessed or the tree's own output).
        diffs = diff_snapshots(expected, actual)
        if diffs and bless_from_legacy is not None:
            for line in diffs[:20]:
                diags.append(Diagnostic(Severity.ERROR, "legacy_parity", line))
            return PublishResult(name=name, success=False, diagnostics=diags,
                                 grid_size=len(actual))

    # All checks passed — copy + update manifest
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(draft_path, published_path)
    snapshot_path.write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = load_manifest()
    manifest[name] = ManifestEntry(
        name=name,
        sha256=_sha256_of(published_path),
        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        grid_size=len(actual),
    )
    save_manifest(manifest)
    return PublishResult(
        name=name, success=True, diagnostics=diags,
        grid_size=len(actual), snapshot_updated=snapshot_updated,
    )
