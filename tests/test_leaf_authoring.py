"""Tests for ai_tree/leaf_authoring.py — server-side codegen for user leaves.

The split:
- TestSnakeCase / TestRenderModule are pure-function tests
- TestValidate exercises every error class with deterministic inputs
- TestCreateLeaf actually writes files into a tmp dir and imports them
- TestParseSpec covers the JSON-body parser
"""

from __future__ import annotations

import sys

import pytest

from ai_tree import leaf_authoring as la
from ai_tree.registry import REGISTRY, clear_registry


# ---------------------------------------------------------------------------
# Snapshot of REGISTRY so leaf-creating tests don't poison other test files.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _restore_registry():
    saved = dict(REGISTRY)
    yield
    clear_registry()
    REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# to_snake_case
# ---------------------------------------------------------------------------
class TestSnakeCase:
    @pytest.mark.parametrize("pascal,snake", [
        ("LowOnRunners", "low_on_runners"),
        ("HasUncommonLoot", "has_uncommon_loot"),
        ("HTTPSRequest", "https_request"),
        ("A", "a"),
        ("AB", "ab"),
        ("ABc", "a_bc"),    # acronym followed by capitalised word
    ])
    def test_conversions(self, pascal, snake):
        assert la.to_snake_case(pascal) == snake


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------
def _spec(**overrides) -> la.LeafSpec:
    defaults = dict(
        name="LowOnRunners",
        category="Squad.Health",
        description="True if 2 or fewer runners alive.",
        requires=("squad",),
        params=(),
        body="return True",
    )
    defaults.update(overrides)
    return la.LeafSpec(**defaults)


class TestValidate:
    def test_happy_path_no_errors(self, tmp_path):
        errors = la.validate(_spec(), registry_names=set(), leaves_dir=tmp_path)
        assert errors == []

    def test_missing_name(self, tmp_path):
        errors = la.validate(_spec(name=""), registry_names=set(),
                             leaves_dir=tmp_path)
        assert any(e.field == "name" for e in errors)

    def test_invalid_name_lowercase(self, tmp_path):
        errors = la.validate(_spec(name="lowOnRunners"),
                             registry_names=set(), leaves_dir=tmp_path)
        assert any(e.field == "name" and "PascalCase" in e.message for e in errors)

    def test_invalid_name_with_underscore(self, tmp_path):
        errors = la.validate(_spec(name="Low_On_Runners"),
                             registry_names=set(), leaves_dir=tmp_path)
        assert any(e.field == "name" for e in errors)

    def test_name_already_in_registry(self, tmp_path):
        errors = la.validate(
            _spec(name="HasUncommonLoot"),
            registry_names={"HasUncommonLoot"},
            leaves_dir=tmp_path,
        )
        assert any("already exists" in e.message for e in errors)

    def test_blank_category(self, tmp_path):
        errors = la.validate(_spec(category="   "), registry_names=set(),
                             leaves_dir=tmp_path)
        assert any(e.field == "category" for e in errors)

    def test_blank_description(self, tmp_path):
        errors = la.validate(_spec(description=""), registry_names=set(),
                             leaves_dir=tmp_path)
        assert any(e.field == "description" for e in errors)

    def test_invalid_requires_identifier(self, tmp_path):
        errors = la.validate(
            _spec(requires=("not a valid name",)),
            registry_names=set(),
            leaves_dir=tmp_path,
        )
        assert any(e.field == "requires" for e in errors)

    def test_param_invalid_name(self, tmp_path):
        errors = la.validate(
            _spec(params=(la.ParamFormSpec(
                name="1bad", type="float", default=0.0, description=""),)),
            registry_names=set(),
            leaves_dir=tmp_path,
        )
        assert any(e.field == "params[0].name" for e in errors)

    def test_param_unsupported_type(self, tmp_path):
        errors = la.validate(
            _spec(params=(la.ParamFormSpec(
                name="threshold", type="dict", default=None, description=""),)),
            registry_names=set(),
            leaves_dir=tmp_path,
        )
        assert any(e.field == "params[0].type" for e in errors)

    def test_param_duplicate_names(self, tmp_path):
        errors = la.validate(
            _spec(params=(
                la.ParamFormSpec(name="x", type="float", default=0.0, description=""),
                la.ParamFormSpec(name="x", type="int", default=0, description=""),
            )),
            registry_names=set(),
            leaves_dir=tmp_path,
        )
        assert any("duplicate" in e.message for e in errors)

    def test_body_required(self, tmp_path):
        errors = la.validate(_spec(body=""), registry_names=set(),
                             leaves_dir=tmp_path)
        assert any(e.field == "body" for e in errors)

    def test_body_syntax_error(self, tmp_path):
        errors = la.validate(_spec(body="return ctx.x >"),
                             registry_names=set(), leaves_dir=tmp_path)
        assert any(e.field == "body" for e in errors)

    def test_file_already_exists(self, tmp_path):
        (tmp_path / "low_on_runners.py").write_text("# stub", encoding="utf-8")
        errors = la.validate(_spec(), registry_names=set(),
                             leaves_dir=tmp_path)
        assert any("already exists" in e.message for e in errors)

    def test_multiline_body_compiles(self, tmp_path):
        body = "n = len(ctx.squad.runners)\nreturn n <= 2"
        errors = la.validate(_spec(body=body), registry_names=set(),
                             leaves_dir=tmp_path)
        assert errors == []


# ---------------------------------------------------------------------------
# render_module()
# ---------------------------------------------------------------------------
class TestRenderModule:
    def test_renders_valid_python(self):
        src = la.render_module(_spec())
        compile(src, "<test>", "exec")   # must compile

    def test_includes_decorator_and_function(self):
        src = la.render_module(_spec())
        assert "@bt_condition" in src
        # repr() picks single quotes by default — that's what the file uses
        assert "name='LowOnRunners'" in src
        assert "def low_on_runners(ctx) -> bool:" in src
        assert "return True" in src

    def test_renders_param_with_correct_type_token(self):
        src = la.render_module(_spec(params=(
            la.ParamFormSpec(name="threshold", type="float",
                             default=1.5, description="cutoff"),
        )))
        # The type goes in unquoted (so Python sees the type, not a string)
        assert "type=float" in src
        assert "default=1.5" in src

    def test_renders_each_supported_type(self):
        for type_name in ("float", "int", "bool", "str"):
            default = {"float": 0.0, "int": 0, "bool": False, "str": ""}[type_name]
            src = la.render_module(_spec(params=(
                la.ParamFormSpec(name="x", type=type_name,
                                 default=default, description=""),
            )))
            compile(src, "<test>", "exec")
            assert f"type={type_name}" in src

    def test_multiline_body_indented(self):
        src = la.render_module(_spec(body="x = 1\nreturn x > 0"))
        compile(src, "<test>", "exec")
        assert "    x = 1\n    return x > 0" in src


# ---------------------------------------------------------------------------
# create_leaf() — actually writes + imports
# ---------------------------------------------------------------------------
class TestCreateLeaf:
    @pytest.fixture
    def isolated_leaves(self, tmp_path, monkeypatch):
        """Redirect USER_LEAVES_DIR to a tmp path, and add tmp_path's parent
        to sys.path so we can import the generated file by its tmp module name.

        Trick: the production package path is runner_sim.zone_sim.user_leaves.
        We can't easily monkeypatch that, so this fixture creates a sibling
        package under tmp_path, adds tmp_path to sys.path, and points
        leaf_authoring at it.
        """
        pkg_dir = tmp_path / "tmp_user_leaves"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

        monkeypatch.setattr(la, "USER_LEAVES_DIR", pkg_dir)
        monkeypatch.setattr(la, "USER_LEAVES_PACKAGE", "tmp_user_leaves")
        monkeypatch.syspath_prepend(str(tmp_path))

        yield pkg_dir

        # Drop any imported tmp_user_leaves modules so subsequent tests get
        # a fresh slate (and so REGISTRY restoration via the autouse fixture
        # doesn't get confused by re-imports).
        for modname in list(sys.modules):
            if modname.startswith("tmp_user_leaves"):
                del sys.modules[modname]

    def test_create_writes_file_and_registers(self, isolated_leaves):
        result = la.create_leaf(_spec(name="MyTestLeaf",
                                      body="return True"))
        assert result.success, [str(e) for e in result.errors]
        assert result.path is not None
        assert result.path.exists()
        assert "MyTestLeaf" in REGISTRY

    def test_create_with_validation_errors_does_not_write(self, isolated_leaves):
        result = la.create_leaf(_spec(name="badname",
                                      body="return True"))
        assert not result.success
        assert result.errors
        # No leaf file should have been written (the package's __init__.py
        # is created by the fixture and doesn't count).
        leaf_files = [p for p in isolated_leaves.glob("*.py")
                      if p.name != "__init__.py"]
        assert leaf_files == []

    def test_create_leaf_callable_through_registry(self, isolated_leaves):
        """The registered leaf must actually run."""
        la.create_leaf(_spec(name="AlwaysFalse", body="return False"))
        spec = REGISTRY["AlwaysFalse"]
        assert spec.func(ctx=None) is False

    def test_import_failure_rolls_back_file(self, isolated_leaves, monkeypatch):
        """If the import raises (e.g. duplicate registration), the file is
        deleted and the error surfaces in the result."""
        # First create succeeds
        first = la.create_leaf(_spec(name="DupLeaf", body="return True"))
        assert first.success

        # Pretend the file got cleared from sys.modules + REGISTRY but the
        # file is still on disk: simulate a TOCTOU race by re-creating with
        # the same snake_case name. The validator's file-exists check will
        # catch it, so this proves the rollback logic for a *different*
        # failure: artificially break import.
        broken = la.LeafSpec(
            name="BrokenLeaf",
            category="Test",
            description="x",
            requires=(),
            params=(),
            body="return True",
        )
        # Sneak past validation, then force an import error inside create_leaf
        original_import = la.importlib.import_module

        def boom(name):
            raise RuntimeError("synthetic import failure")
        monkeypatch.setattr(la.importlib, "import_module", boom)

        result = la.create_leaf(broken)
        assert not result.success
        assert any("synthetic import failure" in e.message for e in result.errors)
        # File should have been rolled back
        assert not (isolated_leaves / "broken_leaf.py").exists()

        # Restore for cleanup
        monkeypatch.setattr(la.importlib, "import_module", original_import)


# ---------------------------------------------------------------------------
# parse_spec()
# ---------------------------------------------------------------------------
class TestParseSpec:
    def test_minimal_payload(self):
        spec, errs = la.parse_spec({
            "name": "X", "category": "Y", "description": "z", "body": "return True",
        })
        assert errs == []
        assert spec is not None
        assert spec.name == "X"

    def test_full_payload(self):
        spec, errs = la.parse_spec({
            "name": "X",
            "category": "Y",
            "description": "z",
            "requires": ["loot", "perception"],
            "params": [
                {"name": "threshold", "type": "float",
                 "default": 1.5, "description": "cutoff"}
            ],
            "body": "return True",
        })
        assert errs == []
        assert spec.requires == ("loot", "perception")
        assert spec.params[0].name == "threshold"
        assert spec.params[0].default == 1.5

    def test_non_dict_payload(self):
        spec, errs = la.parse_spec(["not", "an", "object"])  # type: ignore[arg-type]
        assert spec is None
        assert errs

    def test_requires_not_a_list(self):
        spec, errs = la.parse_spec({
            "name": "X", "category": "Y", "description": "z",
            "body": "return True", "requires": "loot",
        })
        assert spec is None
        assert any(e.field == "requires" for e in errs)

    def test_param_not_an_object(self):
        spec, errs = la.parse_spec({
            "name": "X", "category": "Y", "description": "z",
            "body": "return True", "params": ["not an object"],
        })
        assert spec is None
        assert any(e.field.startswith("params[0]") for e in errs)
