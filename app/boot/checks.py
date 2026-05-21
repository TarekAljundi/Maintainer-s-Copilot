"""Standalone boot-check callables — unit-testable in isolation, raise the
specific InfraError subclass that the lifespan maps to `BOOT FAIL #N`.

PRD §Boot-time refusal calls for 8 checks (see issue 14):
    1. Vault reachable        -> VaultError
    2. Vault paths load        -> VaultError
    3. DB at Alembic head      -> DatabaseError
    4. classifier_loaded=true  -> ClassifierUnavailable
    5. weights SHA match       -> InfraError
    6. Langfuse auth_check     -> LLMProviderError
    7. eval thresholds 0<x<1   -> InfraError
    8. prompt SHAs match       -> InfraError

Most checks read process state (vault cache, env, files); only #3, #4, #6
touch the network. None mutate state.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from app.domain.exceptions import (
    ClassifierUnavailable,
    DatabaseError,
    InfraError,
    LLMProviderError,
    VaultError,
)
from app.infra import tracing
from app.infra._classifier_registry import WEIGHTS_SHA256
from app.infra.model_server_client import ModelServerClient
from app.infra.vault import REQUIRED_PATHS, get_vault

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_log = logging.getLogger(__name__)


# ---- 1: Vault health ------------------------------------------------------


def check_vault_health() -> None:
    if not get_vault().health():
        raise VaultError("vault unreachable or sealed")


# ---- 2: Required vault paths --------------------------------------------


def check_vault_paths(paths: Iterable[str] = REQUIRED_PATHS) -> None:
    # `load_all` raises VaultError on the first missing/failed path.
    get_vault().load_all(paths)


# ---- 3: DB at Alembic head ----------------------------------------------


async def check_db_at_head() -> None:
    """Compare the `alembic_version.version_num` column against the script
    directory's head revision. Either side missing => DatabaseError.

    Uses asyncpg (already a project dep) for the probe — avoids dragging a
    sync Postgres driver into the api image just for one boot check.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except Exception as exc:  # pragma: no cover — install error
        raise DatabaseError(f"alembic import failed: {exc}") from exc

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if not head:
        raise DatabaseError("alembic script dir has no head revision")

    try:
        url = get_vault().cached("api/db").get("url") or ""
    except VaultError as exc:
        raise DatabaseError(f"db url not loaded from vault: {exc}") from exc
    if not url:
        raise DatabaseError("vault api/db.url is empty")
    # asyncpg uses the plain `postgresql://` scheme; SQLAlchemy adds the
    # driver suffix (`+asyncpg`) we strip here.
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import asyncpg
    except Exception as exc:  # pragma: no cover
        raise DatabaseError(f"asyncpg import failed: {exc}") from exc

    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:
        raise DatabaseError(f"db probe failed: {exc}") from exc
    try:
        row = await conn.fetchrow("SELECT version_num FROM alembic_version")
    except Exception as exc:
        raise DatabaseError(f"alembic_version probe failed: {exc}") from exc
    finally:
        await conn.close()

    if row is None:
        raise DatabaseError("alembic_version table empty")
    current = row["version_num"]
    if current != head:
        raise DatabaseError(f"db at {current!r}, alembic head is {head!r}")


# ---- 4: model server reports classifier_loaded --------------------------


def check_model_server_loaded() -> None:
    # CI doesn't carry the fine-tuned classifier artifacts (training requires
    # GPU + minutes). MC_BOOT_SKIP_CLASSIFIER=1 makes this check a no-op so
    # the rest of the boot path can be exercised; classification eval in CI
    # then runs `--models classical` only. RUNBOOK §CI documents the carve-out.
    if os.environ.get("MC_BOOT_SKIP_CLASSIFIER") == "1":
        _log.warning(
            "MC_BOOT_SKIP_CLASSIFIER=1 — boot check #4 bypassed; "
            "classifier predictions will return 503"
        )
        return
    try:
        h = ModelServerClient().health()
    except ClassifierUnavailable:
        raise
    except Exception as exc:
        raise ClassifierUnavailable(f"model-server /health raised: {exc}") from exc
    if not h.get("classifier_loaded"):
        raise ClassifierUnavailable("model-server reports classifier_loaded=false")


# ---- 5: classifier weights SHA pin --------------------------------------


def check_classifier_weights_sha() -> None:
    if not WEIGHTS_SHA256:
        # Treat empty pin as "unpinned" — explicit dev escape hatch documented
        # in app/infra/_classifier_registry.py. CI must reject this on main.
        return
    try:
        h = ModelServerClient().health()
    except Exception as exc:
        raise InfraError(f"model-server /health unreachable: {exc}") from exc
    reported = h.get("weights_sha") or ""
    if reported != WEIGHTS_SHA256:
        raise InfraError(
            f"classifier weights SHA mismatch — pinned={WEIGHTS_SHA256} reported={reported}"
        )


# ---- 6: Langfuse auth ---------------------------------------------------


def check_langfuse_auth() -> None:
    """Delegates to `tracing.init_tracing()` which is idempotent and raises
    `LLMProviderError` on real-key auth failure. Placeholder/disabled mode
    stays a no-op (boot stays alive in dev)."""
    try:
        tracing.init_tracing()
    except LLMProviderError:
        raise
    except Exception as exc:
        raise LLMProviderError(f"tracing init unexpected error: {exc}") from exc


# ---- 7: eval thresholds bounded -----------------------------------------


def _walk_numeric(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_numeric(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_numeric(v, f"{path}[{i}]")
    elif isinstance(obj, bool):
        return  # bool is int; explicit skip so `must_pass: true` doesn't trip the check
    elif isinstance(obj, (int, float)):
        yield path, obj


def check_eval_thresholds(yaml_path: Path | None = None) -> None:
    """Every numeric threshold must be strictly between 0 and 1.

    Brief-mandated refusal: a 0 floor effectively disables the gate.
    """
    try:
        import yaml
    except Exception as exc:  # pragma: no cover
        raise InfraError(f"pyyaml import failed: {exc}") from exc

    p = yaml_path or (PROJECT_ROOT / "eval_thresholds.yaml")
    if not p.exists():
        raise InfraError(f"eval_thresholds.yaml missing at {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise InfraError(f"eval_thresholds.yaml unparseable: {exc}") from exc

    offenders = [(path, val) for path, val in _walk_numeric(data) if not (0 < float(val) < 1)]
    if offenders:
        rendered = ", ".join(f"{p}={v}" for p, v in offenders)
        raise InfraError(f"eval threshold(s) out of (0,1) range: {rendered}")


# ---- 8: prompt file SHAs ------------------------------------------------


def _sha256_file(p: Path) -> str:
    """LF-normalized: see prompts/_registry.py:sha256_file for the rationale."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_prompt_shas() -> None:
    from prompts._registry import PROMPT_SHAS, PROMPTS_DIR

    if not PROMPT_SHAS:
        # Unpinned bootstrap: a freshly-cloned dev tree before `bump_prompt_shas.py`
        # has been run. Allow, but visibly so CI catches it on main.
        return

    offenders: list[str] = []
    for name, pinned in PROMPT_SHAS.items():
        f = PROMPTS_DIR / f"{name}.md"
        if not f.exists():
            offenders.append(f"{name}.md MISSING")
            continue
        actual = _sha256_file(f)
        if actual != pinned:
            offenders.append(f"{name}.md sha={actual[:12]}.. pinned={pinned[:12]}..")
    if offenders:
        raise InfraError(
            "prompt SHA mismatch (re-run scripts/bump_prompt_shas.py): " + "; ".join(offenders)
        )
