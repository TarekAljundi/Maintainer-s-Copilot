"""Layer-boundary static checks (PRD §Testing Decisions — Layer boundary tests).

Walks every .py file in `app/` and asserts per-layer import rules using `ast`
(not grep — handles aliased imports, `from X import Y as Z`, and so on).

Rules:
- `app/api/*.py` (routers — shallow HTTP glue): must NOT import `sqlalchemy.*`
  or `redis.*`. Carve-out: `app/api/auth.py` may import `fastapi_users.db`
  (the library forces `SQLAlchemyUserDatabase` on us; hiding the whole library
  behind an adapter is out of slice 15 scope).
- `app/repositories/*.py`: must NOT import `fastapi.*`. Carve-out:
  `app/repositories/users.py` may import `fastapi` (Depends) and
  `fastapi_users.db` — same fastapi-users constraint.
- `app/services/*.py`: must NOT import raw `sqlalchemy.*`, `redis.*`, or
  top-level `httpx`. The DI surface is `app.infra.*` — those wrappers are the
  port the rule is protecting, so importing them is fine.

CARVEOUT_ALLOW: file path -> set of top-level package names that file is
allowed to import despite the layer rule. Adding a new exception is a
deliberate one-line change reviewed in its own PR.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

CARVEOUT_ALLOW: dict[str, set[str]] = {
    # fastapi-users forces SQLAlchemyUserDatabase on the router and table.
    "app/api/auth.py": {"fastapi_users", "sqlalchemy"},
    "app/repositories/users.py": {"fastapi", "fastapi_users", "sqlalchemy"},
}


def _imported_top_level_packages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                packages.add(node.module.split(".", 1)[0])
    return packages


def _layer_files(subdir: str) -> list[Path]:
    base = APP_ROOT / subdir
    if not base.exists():
        return []
    return [p for p in base.glob("*.py") if p.name != "__init__.py"]


def _carveout_for(path: Path) -> set[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return CARVEOUT_ALLOW.get(rel, set())


def _assert_no_imports(path: Path, forbidden: set[str]) -> None:
    actual = _imported_top_level_packages(path)
    carveout = _carveout_for(path)
    bad = (actual & forbidden) - carveout
    assert not bad, (
        f"{path.relative_to(REPO_ROOT).as_posix()} imports forbidden packages "
        f"{sorted(bad)}; layer rule violated (allowed via CARVEOUT_ALLOW: "
        f"{sorted(carveout)})"
    )


# ---- routers (app/api/) ---------------------------------------------------

ROUTER_FORBIDDEN = {"sqlalchemy", "redis"}


@pytest.mark.parametrize("path", _layer_files("api"), ids=lambda p: p.name)
def test_routers_no_db_or_cache_imports(path: Path) -> None:
    _assert_no_imports(path, ROUTER_FORBIDDEN)


# ---- repositories (app/repositories/) -------------------------------------

REPO_FORBIDDEN = {"fastapi"}


@pytest.mark.parametrize("path", _layer_files("repositories"), ids=lambda p: p.name)
def test_repositories_no_fastapi_imports(path: Path) -> None:
    _assert_no_imports(path, REPO_FORBIDDEN)


# ---- services (app/services/) ---------------------------------------------
#
# Services may not reach past `app.infra.*` to grab a raw DB session, redis
# client, or HTTP client. The wrappers in `app/infra/` ARE the DI ports the
# layer rule is protecting; importing them is the intended path.

SERVICE_FORBIDDEN = {"sqlalchemy", "redis", "httpx"}


@pytest.mark.parametrize("path", _layer_files("services"), ids=lambda p: p.name)
def test_services_no_raw_infra_imports(path: Path) -> None:
    _assert_no_imports(path, SERVICE_FORBIDDEN)
