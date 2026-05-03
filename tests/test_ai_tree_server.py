"""Integration tests for ai_tree/server.py — the editor HTTP API.

Each test boots a real server on an ephemeral port (in a background thread),
makes urllib requests against it, and asserts response shapes. The publisher
state directories are redirected via monkeypatch so tests don't touch the
repo's real `ai_trees/`.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ai_tree import publisher as pub
from ai_tree.server import BTRequestHandler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_tree_dirs(tmp_path, monkeypatch):
    """Redirect publisher's directories to a temp dir for the test."""
    drafts = tmp_path / "drafts"
    published = tmp_path / "published"
    drafts.mkdir()
    published.mkdir()
    manifest = tmp_path / "manifest.json"

    monkeypatch.setattr(pub, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(pub, "PUBLISHED_DIR", published)
    monkeypatch.setattr(pub, "MANIFEST_PATH", manifest)
    return drafts, published, manifest


@pytest.fixture
def running_server(isolated_tree_dirs):
    """Start a real HTTP server on an ephemeral port; tear down after test."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BTRequestHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body}


def _put(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _post(url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body else b""
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# /catalog
# ---------------------------------------------------------------------------
class TestCatalog:
    def test_catalog_lists_all_registered_leaves(self, running_server):
        status, body = _get(f"{running_server}/catalog")
        assert status == 200
        assert "leaves" in body
        names = {leaf["name"] for leaf in body["leaves"]}
        # Spot-check a few we know exist
        assert "OpponentHelpless" in names
        assert "CombatRatioAbove" in names
        assert "IsFinalTick" in names

    def test_catalog_entries_have_required_shape(self, running_server):
        _, body = _get(f"{running_server}/catalog")
        for leaf in body["leaves"]:
            assert "name" in leaf
            assert leaf["kind"] in ("condition", "action")
            assert "category" in leaf
            assert "description" in leaf
            assert isinstance(leaf["requires"], list)
            assert isinstance(leaf["params"], list)

    def test_catalog_entries_sorted_by_category_then_name(self, running_server):
        _, body = _get(f"{running_server}/catalog")
        keys = [(leaf["category"], leaf["name"]) for leaf in body["leaves"]]
        assert keys == sorted(keys)

    def test_parameter_metadata_preserved(self, running_server):
        _, body = _get(f"{running_server}/catalog")
        threshold_leaves = [
            leaf for leaf in body["leaves"]
            if any(p["name"] == "threshold" for p in leaf["params"])
        ]
        assert threshold_leaves, "expected at least one parameterised leaf"
        first = threshold_leaves[0]
        param = next(p for p in first["params"] if p["name"] == "threshold")
        assert param["type"] == "float"
        assert param["default"] is not None


# ---------------------------------------------------------------------------
# /trees CRUD
# ---------------------------------------------------------------------------
_MINIMAL_TREE = {
    "name": "T",
    "root": {"type": "leaf", "id": "IsFinalTick"},
}


class TestTreesCrud:
    def test_list_trees_empty_initially(self, running_server):
        status, body = _get(f"{running_server}/trees")
        assert status == 200
        assert body == {"drafts": [], "published": []}

    def test_put_creates_draft_and_get_round_trips(self, running_server):
        url = f"{running_server}/trees/extraction_cautious"
        put_status, put_body = _put(url, _MINIMAL_TREE)
        assert put_status == 200
        assert put_body["ok"] is True

        get_status, doc = _get(url)
        assert get_status == 200
        assert doc == _MINIMAL_TREE

    def test_list_after_put_reflects_draft(self, running_server):
        _put(f"{running_server}/trees/extraction_cautious", _MINIMAL_TREE)
        _, body = _get(f"{running_server}/trees")
        assert body["drafts"] == ["extraction_cautious"]
        assert body["published"] == []

    def test_get_unknown_draft_404s(self, running_server):
        status, body = _get(f"{running_server}/trees/no_such_tree")
        assert status == 404
        assert "not found" in body["error"]

    def test_put_rejects_invalid_name_with_slash(self, running_server):
        # urllib will percent-encode but the server should still reject after decode.
        # Construct a request whose path explicitly contains a slash post-decode by
        # using a name with a known encoded slash.
        req = urllib.request.Request(
            f"{running_server}/trees/foo%2Fbar",
            data=b"{}", method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400


# ---------------------------------------------------------------------------
# /trees/<name>/publish
# ---------------------------------------------------------------------------
class TestPublishEndpoint:
    def test_first_publish_succeeds(self, running_server):
        _put(f"{running_server}/trees/extraction_cautious", _MINIMAL_TREE)
        status, body = _post(
            f"{running_server}/trees/extraction_cautious/publish"
        )
        assert status == 200
        assert body["success"] is True
        assert body["snapshot_updated"] is True
        assert body["grid_size"] == 160

    def test_unknown_draft_publish_returns_diagnostics(self, running_server):
        status, body = _post(f"{running_server}/trees/never_drafted/publish")
        assert status == 422
        assert body["success"] is False
        assert any(d["check"] == "schema" for d in body["diagnostics"])

    def test_behavioural_change_fails_publish(self, running_server):
        # Publish once
        _put(f"{running_server}/trees/extraction_cautious", _MINIMAL_TREE)
        _post(f"{running_server}/trees/extraction_cautious/publish")

        # Replace draft with a behaviourally different tree
        always_extract = {
            "name": "T",
            "root": {
                "type": "selector",
                "children": [
                    {"type": "leaf", "id": "IsFinalTick"},
                    {"type": "leaf", "id": "CarryingNothing"},
                    {"type": "leaf", "id": "CarryingAnything"},
                ],
            },
        }
        _put(f"{running_server}/trees/extraction_cautious", always_extract)
        status, body = _post(
            f"{running_server}/trees/extraction_cautious/publish"
        )
        assert status == 422
        assert body["success"] is False
        assert any(d["check"] == "snapshot" for d in body["diagnostics"])

    def test_update_snapshot_query_param_blesses_change(self, running_server):
        _put(f"{running_server}/trees/extraction_cautious", _MINIMAL_TREE)
        _post(f"{running_server}/trees/extraction_cautious/publish")

        always_extract = {
            "name": "T",
            "root": {
                "type": "selector",
                "children": [
                    {"type": "leaf", "id": "IsFinalTick"},
                    {"type": "leaf", "id": "CarryingNothing"},
                    {"type": "leaf", "id": "CarryingAnything"},
                ],
            },
        }
        _put(f"{running_server}/trees/extraction_cautious", always_extract)
        status, body = _post(
            f"{running_server}/trees/extraction_cautious/publish"
            "?update_snapshot=true"
        )
        assert status == 200
        assert body["success"] is True
        assert body["snapshot_updated"] is True


# ---------------------------------------------------------------------------
# Bad routes
# ---------------------------------------------------------------------------
class TestUnknownRoutes:
    def test_unknown_get_returns_404(self, running_server):
        status, _ = _get(f"{running_server}/no/such/route")
        # Falls through to static handler which returns 404
        assert status == 404

    def test_unknown_put_returns_404(self, running_server):
        status, _ = _put(f"{running_server}/something", {})
        assert status == 404
