"""On-demand HTTP server for the visual behaviour-tree editor.

Run with:
    uv run python -m ai_tree.server [--port 8765]

Then point a browser at http://localhost:8765/. The server stays in the
foreground; Ctrl+C stops it. The editor frontend (when implemented) is served
from `ai_tree_editor/` as static files.

Endpoints:

    GET  /catalog                   list all registered leaves with metadata
    GET  /scaffolds                 list kinds (strict) and doctrines (suggestions)
    GET  /trees                     list draft and published tree names
    GET  /trees/<name>              read a draft tree as JSON
    PUT  /trees/<name>              write a draft tree (JSON body)
    POST /trees/<name>              create a new empty draft (refuses if exists)
    POST /trees/<name>/publish      run the publish gate; returns diagnostics
                                    (optional `update_snapshot` query flag)
    POST /leaves                    create a new user-authored leaf (codegen + reload)
    GET  /                          serve editor index.html
    GET  /<static>                  serve any file under ai_tree_editor/

The server uses stdlib http.server only — no third-party deps. Designed to
run on the developer's machine while authoring trees and stop afterwards;
not hardened for hostile environments.
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


# Tree names are file stems on disk, so reject anything that isn't safely
# round-trippable as a filename. Letters, digits, and underscores only —
# matches our own naming convention (extraction_cautious, encounter_greedy)
# and gives no way to traverse paths via percent-encoding.
_VALID_NAME = re.compile(r"^[A-Za-z0-9_]+$")

# When the editor *creates* a new tree we enforce a stricter shape: the
# kind prefix must be one of the dispatchable kinds, and the doctrine
# suffix must be in the Doctrine enum (checked separately at request
# time). Existing files (loaded via PUT) are only checked against
# _VALID_NAME — strict creation prevents new garbage, but we don't
# break round-tripping of older trees that might predate this rule.
_CREATE_TREE_NAME = re.compile(r"^(extraction|encounter)_([a-z][a-z0-9_]*)$")

REPO_ROOT = Path(__file__).resolve().parent.parent
EDITOR_DIR = REPO_ROOT / "ai_tree_editor"

# Importing these populates REGISTRY with every leaf the catalog needs.
# `ai_conditions` provides the built-in catalog; `user_leaves` autoloads any
# editor-authored leaves (the package may be empty on a fresh checkout).
sys.path.insert(0, str(REPO_ROOT))
from runner_sim.zone_sim import ai_conditions  # noqa: E402, F401
from runner_sim.zone_sim import user_leaves  # noqa: E402, F401

from ai_tree import leaf_authoring, publisher  # noqa: E402
from ai_tree.registry import REGISTRY, NodeKind  # noqa: E402


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def build_catalog() -> dict[str, Any]:
    """Serialise every registered leaf into a catalog payload for the editor.

    The shape is JSON-friendly (no Python types in the response):

        {
          "leaves": [
            {
              "name": "OpponentHelpless",
              "kind": "condition",                 // condition | action
              "category": "Encounter.Combat",
              "description": "...",
              "requires": ["opponent_combat_estimate"],
              "params": [
                {"name": "threshold", "type": "float",
                 "default": 1.0, "description": "..."}
              ]
            },
            ...
          ]
        }
    """
    leaves = []
    for spec in sorted(REGISTRY.values(), key=lambda s: (s.category, s.name)):
        leaves.append({
            "name": spec.name,
            "kind": "condition" if spec.kind == NodeKind.CONDITION else "action",
            "category": spec.category,
            "description": spec.description,
            "requires": list(spec.requires),
            "params": [
                {
                    "name": p.name,
                    "type": p.type.__name__,   # "float", "int", "bool", "str"
                    "default": p.default,
                    "description": p.description,
                }
                for p in spec.params
            ],
        })
    return {"leaves": leaves}


# ---------------------------------------------------------------------------
# Scaffolds — vocabulary used by the "New Tree" modal
# ---------------------------------------------------------------------------
def build_scaffolds() -> dict[str, list[str]]:
    """Vocabulary for the New Tree form.

    `kinds` is strict — the publisher's grid generators and the runtime
    dispatchers know about exactly these two. Adding a new kind is a code
    change.

    `doctrines` is suggestions only — the editor renders them as a
    <datalist>, so authors can pick a known doctrine or type a new one.
    Authoring `extraction_experimental` is fine; the runtime ignores
    it until `Doctrine.EXPERIMENTAL` lands in extraction_ai.py.
    """
    from runner_sim.zone_sim.extraction_ai import Doctrine
    return {
        "kinds": [k.value for k in publisher.TreeKind],
        "doctrines": [d.value for d in Doctrine],
    }


# ---------------------------------------------------------------------------
# Tree CRUD
# ---------------------------------------------------------------------------
def list_trees() -> dict[str, list[str]]:
    """List the draft and published tree names (without extension)."""
    drafts = sorted(p.stem for p in publisher.DRAFTS_DIR.glob("*.json"))
    published = sorted(publisher.load_manifest().keys())
    return {"drafts": drafts, "published": published}


def read_draft(name: str) -> dict[str, Any] | None:
    """Return the parsed draft JSON, or None if the draft doesn't exist."""
    path = publisher.DRAFTS_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_draft(name: str, doc: dict[str, Any]) -> None:
    """Persist the draft to disk. Caller is responsible for content validity;
    publish-time lint will catch structural problems."""
    publisher.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = publisher.DRAFTS_DIR / f"{name}.json"
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def create_empty_tree(name: str) -> tuple[bool, str | None]:
    """Initialise a brand-new draft as an empty Selector root.

    Returns (created, error). If a draft or published version already
    exists with this name, returns (False, "<reason>") and leaves the
    filesystem untouched.
    """
    draft_path = publisher.DRAFTS_DIR / f"{name}.json"
    if draft_path.exists():
        return False, f"draft {name!r} already exists"
    if name in publisher.load_manifest():
        return False, f"published tree {name!r} already exists"
    write_draft(name, {
        "name": name,
        "root": {"type": "selector", "children": []},
    })
    return True, None


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
def publish_via_api(name: str, *, update_snapshot: bool = False) -> dict[str, Any]:
    """Wrap publisher.publish() in a JSON-serialisable response."""
    result = publisher.publish(name, update_snapshot=update_snapshot)
    return {
        "name": result.name,
        "success": result.success,
        "grid_size": result.grid_size,
        "snapshot_updated": result.snapshot_updated,
        "diagnostics": [
            {"severity": d.severity.value, "check": d.check, "message": d.message}
            for d in result.diagnostics
        ],
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class BTRequestHandler(BaseHTTPRequestHandler):
    # Keep stdlib's per-request log line; it's useful during dev.
    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[bt-server] {format % args}\n")

    # ----- helpers -----
    def _send_json(self, status: HTTPStatus, body: Any) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, status: HTTPStatus, body: str,
                   content_type: str = "text/plain") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"error": f"invalid JSON body: {e}"})
            return None

    # ----- GET -----
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path

        if path == "/catalog":
            self._send_json(HTTPStatus.OK, build_catalog())
            return
        if path == "/scaffolds":
            self._send_json(HTTPStatus.OK, build_scaffolds())
            return
        if path == "/trees":
            self._send_json(HTTPStatus.OK, list_trees())
            return
        if path.startswith("/trees/"):
            name = unquote(path[len("/trees/"):])
            if not _VALID_NAME.match(name):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "invalid tree name"})
                return
            doc = read_draft(name)
            if doc is None:
                self._send_json(HTTPStatus.NOT_FOUND,
                                {"error": f"draft {name!r} not found"})
                return
            self._send_json(HTTPStatus.OK, doc)
            return

        # Static file fallback (editor frontend)
        self._serve_static(path)

    # ----- PUT -----
    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/trees/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        name = unquote(path[len("/trees/"):])
        if not _VALID_NAME.match(name):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid tree name"})
            return
        doc = self._read_json_body()
        if doc is None:
            return   # _read_json_body already sent the error
        write_draft(name, doc)
        self._send_json(HTTPStatus.OK, {"ok": True, "name": name})

    # ----- POST -----
    def do_POST(self) -> None:
        url = urlparse(self.path)
        path = url.path

        # POST /leaves — create a new user-authored leaf
        if path == "/leaves":
            self._handle_create_leaf()
            return

        # POST /trees/<name>/publish — existing publish flow
        if path.startswith("/trees/") and path.endswith("/publish"):
            self._handle_publish(url, path)
            return

        # POST /trees/<name> — create new empty draft
        if path.startswith("/trees/"):
            self._handle_create_tree(path)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})

    def _handle_publish(self, url, path: str) -> None:
        name = unquote(path[len("/trees/"):-len("/publish")])
        if not _VALID_NAME.match(name):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid tree name"})
            return
        params = parse_qs(url.query)
        update_snapshot = params.get("update_snapshot", ["false"])[0].lower() in (
            "true", "1", "yes",
        )
        result = publish_via_api(name, update_snapshot=update_snapshot)
        status = HTTPStatus.OK if result["success"] else HTTPStatus.UNPROCESSABLE_ENTITY
        self._send_json(status, result)

    def _handle_create_tree(self, path: str) -> None:
        name = unquote(path[len("/trees/"):])
        if not _VALID_NAME.match(name):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid tree name"})
            return
        m = _CREATE_TREE_NAME.match(name)
        if not m:
            self._send_json(HTTPStatus.BAD_REQUEST, {
                "error": "tree name must match (extraction|encounter)_<doctrine>",
            })
            return
        # Doctrine must be one the runtime can actually dispatch — that means
        # it has to be in the Doctrine enum, which itself is bounded by the
        # SHELL_DOCTRINE mapping. Authoring a tree no squad can ever trigger
        # is dead weight, and the editor shouldn't write one.
        from runner_sim.zone_sim.extraction_ai import Doctrine
        doctrine_suffix = m.group(2)
        valid_doctrines = {d.value for d in Doctrine}
        if doctrine_suffix not in valid_doctrines:
            self._send_json(HTTPStatus.BAD_REQUEST, {
                "error": (
                    f"doctrine {doctrine_suffix!r} is not in the Doctrine enum. "
                    f"Add it to runner_sim/zone_sim/extraction_ai.py:Doctrine "
                    f"(and map shells to it in SHELL_DOCTRINE) first. "
                    f"Known doctrines: {sorted(valid_doctrines)}."
                ),
            })
            return
        created, err = create_empty_tree(name)
        if not created:
            self._send_json(HTTPStatus.CONFLICT, {"error": err})
            return
        self._send_json(HTTPStatus.CREATED, {"name": name})

    def _handle_create_leaf(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return  # _read_json_body already sent 400
        spec, parse_errors = leaf_authoring.parse_spec(payload)
        if spec is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {
                "errors": [{"field": e.field, "message": e.message}
                           for e in parse_errors],
            })
            return
        result = leaf_authoring.create_leaf(spec)
        if not result.success:
            self._send_json(HTTPStatus.BAD_REQUEST, {
                "errors": [{"field": e.field, "message": e.message}
                           for e in result.errors],
            })
            return
        self._send_json(HTTPStatus.CREATED, {"name": result.name})

    # ----- static -----
    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = (EDITOR_DIR / path.lstrip("/")).resolve()
        # Reject path traversal — the resolved path must stay inside EDITOR_DIR.
        try:
            target.relative_to(EDITOR_DIR.resolve())
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "path escapes static root"})
            return
        if not target.exists() or not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"not found: {path}"})
            return
        content_type = _guess_content_type(target.suffix)
        self._send_text(HTTPStatus.OK,
                        target.read_text(encoding="utf-8"),
                        content_type=content_type)


_CONTENT_TYPES = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
}


def _guess_content_type(suffix: str) -> str:
    return _CONTENT_TYPES.get(suffix.lower(), "text/plain")


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------
def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), BTRequestHandler)
    print(f"[bt-server] listening on http://{host}:{port}/")
    print(f"[bt-server] Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bt-server] shutting down.")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Behaviour-tree editor server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
