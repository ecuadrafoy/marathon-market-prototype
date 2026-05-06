"""Server-side codegen for user-authored behaviour-tree leaves.

The editor's "New Leaf" form posts a structured spec to /leaves; this module
validates it and writes a single Python file under
`runner_sim/zone_sim/user_leaves/<snake>.py`. Importing that module triggers
its `@bt_condition` decorator, which registers the leaf in the global
REGISTRY. The editor then refreshes /catalog and the new leaf appears in
the palette without a server restart.

Why a separate module: keeping codegen out of `server.py` keeps the HTTP
handler small, and keeping the file-write logic here makes it
unit-testable without spinning up the server.
"""

from __future__ import annotations
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_tree.registry import REGISTRY


REPO_ROOT = Path(__file__).resolve().parent.parent
USER_LEAVES_DIR = REPO_ROOT / "runner_sim" / "zone_sim" / "user_leaves"
USER_LEAVES_PACKAGE = "runner_sim.zone_sim.user_leaves"


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamFormSpec:
    """One row of the editor's parameter table."""
    name: str
    type: str          # one of: "float", "int", "bool", "str"
    default: Any       # already JSON-typed by the time we get it
    description: str = ""


@dataclass(frozen=True)
class LeafSpec:
    """Everything the editor sends in a /leaves POST body."""
    name: str
    category: str
    description: str
    requires: tuple[str, ...] = ()
    params: tuple[ParamFormSpec, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str


@dataclass
class CreateResult:
    success: bool
    name: str = ""
    path: Path | None = None
    errors: list[ValidationError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_REQUIRES_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_VALID_TYPES = {"float", "int", "bool", "str"}


def validate(spec: LeafSpec, *, registry_names: set[str] | None = None,
             leaves_dir: Path | None = None) -> list[ValidationError]:
    """Run every check the create-leaf flow needs. Returns errors per field.

    `registry_names` defaults to the live REGISTRY's keys; pass an explicit
    set in tests so the check is deterministic.
    `leaves_dir` defaults to USER_LEAVES_DIR; tests pass a tmp path.
    """
    if registry_names is None:
        registry_names = set(REGISTRY.keys())
    if leaves_dir is None:
        leaves_dir = USER_LEAVES_DIR

    errors: list[ValidationError] = []

    if not spec.name:
        errors.append(ValidationError("name", "name is required"))
    elif not _NAME_RE.match(spec.name):
        errors.append(ValidationError(
            "name",
            "name must be PascalCase: start with uppercase, letters/digits only "
            f"(got {spec.name!r})"
        ))
    elif spec.name in registry_names:
        errors.append(ValidationError(
            "name", f"a leaf named {spec.name!r} already exists"
        ))

    if not spec.category.strip():
        errors.append(ValidationError("category", "category is required"))

    if not spec.description.strip():
        errors.append(ValidationError(
            "description", "description is required (helps the editor tooltip)"
        ))

    for r in spec.requires:
        if not _REQUIRES_NAME_RE.match(r):
            errors.append(ValidationError(
                "requires",
                f"context field {r!r} is not a valid Python identifier"
            ))

    seen_params: set[str] = set()
    for i, p in enumerate(spec.params):
        if not _PARAM_NAME_RE.match(p.name):
            errors.append(ValidationError(
                f"params[{i}].name",
                f"param name {p.name!r} is not a valid Python identifier"
            ))
        elif p.name in seen_params:
            errors.append(ValidationError(
                f"params[{i}].name", f"duplicate param name {p.name!r}"
            ))
        seen_params.add(p.name)
        if p.type not in _VALID_TYPES:
            errors.append(ValidationError(
                f"params[{i}].type",
                f"type {p.type!r} not supported (choose float / int / bool / str)"
            ))

    body_err = _validate_body(spec.body)
    if body_err is not None:
        errors.append(ValidationError("body", body_err))

    if spec.name and _NAME_RE.match(spec.name):
        target = leaves_dir / f"{to_snake_case(spec.name)}.py"
        if target.exists():
            errors.append(ValidationError(
                "name",
                f"file {target.name!r} already exists in user_leaves/ — "
                f"hand-delete it if you want to reuse the slot"
            ))

    return errors


def _validate_body(body: str) -> str | None:
    """Return None if body compiles inside a function context, else error msg."""
    if not body.strip():
        return "body is required (write the leaf logic, e.g. `return ctx.loot.items != []`)"
    lines = body.splitlines() or [""]
    indented = "\n".join("    " + ln for ln in lines)
    src = f"def __test_leaf__(ctx):\n{indented}\n"
    try:
        compile(src, "<leaf-body>", "exec")
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------
_TEMPLATE = '''"""User leaf — generated by the editor.

Hand-edits are preserved as long as the @bt_condition decorator and the
function signature stay intact. To remove this leaf, delete this file and
restart the server (or reload the package)."""
from __future__ import annotations

from ai_tree.registry import bt_condition, ParamSpec  # noqa: F401


@bt_condition(
    name={name!r},
    category={category!r},
    description={description!r},
    requires={requires!r},
    params=[{params}],
)
def {func_name}(ctx) -> bool:
{body_indented}
'''


def render_module(spec: LeafSpec) -> str:
    """Render a complete .py source for a user leaf. Pure function, easy to test."""
    func_name = to_snake_case(spec.name)
    requires_list = list(spec.requires)
    params_src = ", ".join(_render_param(p) for p in spec.params)
    body_indented = _indent_body(spec.body)
    return _TEMPLATE.format(
        name=spec.name,
        category=spec.category,
        description=spec.description,
        requires=requires_list,
        params=params_src,
        func_name=func_name,
        body_indented=body_indented,
    )


def _render_param(p: ParamFormSpec) -> str:
    return (
        f"ParamSpec(name={p.name!r}, type={p.type}, "
        f"default={p.default!r}, description={p.description!r})"
    )


def _indent_body(body: str) -> str:
    lines = body.splitlines() or [""]
    return "\n".join("    " + ln for ln in lines)


def to_snake_case(name: str) -> str:
    """PascalCase → snake_case, handling acronym runs sensibly.

    Examples:
        LowOnRunners      -> low_on_runners
        HasUncommonLoot   -> has_uncommon_loot
        HTTPSRequest      -> https_request
    """
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


# ---------------------------------------------------------------------------
# Create + import
# ---------------------------------------------------------------------------
def create_leaf(spec: LeafSpec, *, leaves_dir: Path | None = None) -> CreateResult:
    """Validate, write the file, and import it so the registry is updated.

    Caller (server) is responsible for converting CreateResult into an HTTP
    response. We do the work in three stages so partial failures are
    debuggable:
        1. validate -> errors
        2. write file
        3. import module -> if it raises, delete the file and surface the error
    """
    if leaves_dir is None:
        leaves_dir = USER_LEAVES_DIR

    errors = validate(spec, leaves_dir=leaves_dir)
    if errors:
        return CreateResult(success=False, errors=errors)

    leaves_dir.mkdir(parents=True, exist_ok=True)
    snake = to_snake_case(spec.name)
    target = leaves_dir / f"{snake}.py"
    target.write_text(render_module(spec), encoding="utf-8")

    try:
        importlib.invalidate_caches()
        module_name = f"{USER_LEAVES_PACKAGE}.{snake}"
        importlib.import_module(module_name)
    except Exception as exc:
        # Decorator failed (duplicate name race?) or syntax error slipped past
        # the validator. Roll back and surface the error so the editor can
        # render it in the modal.
        target.unlink(missing_ok=True)
        return CreateResult(
            success=False,
            errors=[ValidationError(
                "body",
                f"module failed to import: {type(exc).__name__}: {exc}"
            )],
        )

    return CreateResult(success=True, name=spec.name, path=target)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------
def parse_spec(payload: dict[str, Any]) -> tuple[LeafSpec | None, list[ValidationError]]:
    """Pull a LeafSpec out of a /leaves POST body. Catches type errors
    before running through validate()."""
    errors: list[ValidationError] = []
    if not isinstance(payload, dict):
        return None, [ValidationError("", "request body must be a JSON object")]

    name = payload.get("name", "")
    category = payload.get("category", "")
    description = payload.get("description", "")
    body = payload.get("body", "")

    requires_raw = payload.get("requires", []) or []
    if not isinstance(requires_raw, list):
        errors.append(ValidationError("requires", "must be a list of strings"))
        requires_raw = []
    requires = tuple(str(r) for r in requires_raw)

    params_raw = payload.get("params", []) or []
    if not isinstance(params_raw, list):
        errors.append(ValidationError("params", "must be a list of objects"))
        params_raw = []

    params: list[ParamFormSpec] = []
    for i, p in enumerate(params_raw):
        if not isinstance(p, dict):
            errors.append(ValidationError(
                f"params[{i}]", "must be an object with name/type/default/description"
            ))
            continue
        params.append(ParamFormSpec(
            name=str(p.get("name", "")),
            type=str(p.get("type", "")),
            default=p.get("default"),
            description=str(p.get("description", "")),
        ))

    if errors:
        return None, errors

    spec = LeafSpec(
        name=str(name),
        category=str(category),
        description=str(description),
        requires=requires,
        params=tuple(params),
        body=str(body),
    )
    return spec, []
