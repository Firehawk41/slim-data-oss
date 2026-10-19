"""The dependency rules of docs/DESIGN.md §3, enforced on the source code.

`domain/` and `engine/` may import only the standard library and pydantic (plus their own layers),
so they can be lifted into slim-domain unchanged and the engine stays pure for exact replay.
"""

import ast
import sys
from pathlib import Path

import pytest

import slim_data

PACKAGE = Path(slim_data.__file__).resolve().parent
THIRD_PARTY_ALLOWED = {"pydantic", "pydantic_core", "annotated_types", "typing_extensions"}

# layer -> internal packages it may import
LAYER_RULES = {
    "domain": ("slim_data.domain",),
    "engine": ("slim_data.domain", "slim_data.engine"),
}


def imported_modules(source: str, package: str) -> list[str]:
    """Absolute names of every module imported by `source`, which lives in `package`."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                names.append(f"{base}.{node.module}" if node.module else base)
            else:
                names.append(node.module)
    return names


def forbidden_imports(source: str, package: str, allowed_internal: tuple[str, ...]) -> list[str]:
    bad = []
    for name in imported_modules(source, package):
        top = name.split(".")[0]
        if top in sys.stdlib_module_names or top in THIRD_PARTY_ALLOWED:
            continue
        if any(name == allowed or name.startswith(allowed + ".") for allowed in allowed_internal):
            continue
        bad.append(name)
    return bad


def _package_of(path: Path) -> str:
    """The package a file belongs to (what its relative imports resolve against)."""
    return ".".join(path.relative_to(PACKAGE.parent).parent.parts)


@pytest.mark.parametrize("layer", sorted(LAYER_RULES))
def test_layer_imports_only_what_it_may(layer):
    offenders = {}
    for path in (PACKAGE / layer).rglob("*.py"):
        bad = forbidden_imports(path.read_text(), _package_of(path), LAYER_RULES[layer])
        if bad:
            offenders[str(path.relative_to(PACKAGE))] = bad
    assert offenders == {}


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "from sqlalchemy.orm import Session",
        "from slim_data.persistence.db import Base",
        "from ..persistence import db",
        "import slim_domain",
        "from slim_data.engine import steps",  # domain may not depend on the engine
    ],
)
def test_the_check_catches_forbidden_imports_in_domain(source):
    assert forbidden_imports(source, "slim_data.domain", LAYER_RULES["domain"]) != []


@pytest.mark.parametrize(
    "source",
    [
        "from __future__ import annotations",
        "from decimal import Decimal",
        "from pydantic import BaseModel",
        "from slim_data.domain.values import SampleRef",
        "from ..domain import values",
        "from .steps import agreement",
    ],
)
def test_the_check_allows_stdlib_pydantic_and_lower_layers_in_engine(source):
    assert forbidden_imports(source, "slim_data.engine", LAYER_RULES["engine"]) == []
