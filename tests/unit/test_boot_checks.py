"""Per-check failure-case tests. Each check is isolated from the lifespan —
one failure injected, the specific InfraError subclass is asserted."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.boot import checks
from app.domain.exceptions import (
    ClassifierUnavailable,
    DatabaseError,
    InfraError,
    LLMProviderError,
    VaultError,
)


# ---- 1: vault unreachable -------------------------------------------------


def test_check_vault_health_fails_when_unreachable(monkeypatch):
    class _V:
        def health(self):
            return False

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())
    with pytest.raises(VaultError, match="unreachable or sealed"):
        checks.check_vault_health()


def test_check_vault_health_passes(monkeypatch):
    class _V:
        def health(self):
            return True

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())
    checks.check_vault_health()  # no raise


# ---- 2: missing vault path ------------------------------------------------


def test_check_vault_paths_raises_on_missing(monkeypatch):
    class _V:
        def load_all(self, paths):
            raise VaultError("path 'api/llm' not found")

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())
    with pytest.raises(VaultError, match="api/llm"):
        checks.check_vault_paths()


# ---- 3: DB not at head ---------------------------------------------------


async def test_check_db_at_head_mismatch(monkeypatch):
    monkeypatch.setattr("alembic.script.ScriptDirectory.get_current_head", lambda self: "abc123")

    class _V:
        def cached(self, p):
            return {"url": "postgresql+asyncpg://x@h/d"}

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())

    class _Conn:
        async def fetchrow(self, _sql):
            return {"version_num": "old"}

        async def close(self):
            pass

    async def _fake_connect(_dsn):
        return _Conn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", _fake_connect)

    with pytest.raises(DatabaseError, match="db at 'old'"):
        await checks.check_db_at_head()


async def test_check_db_at_head_missing_url(monkeypatch):
    monkeypatch.setattr("alembic.script.ScriptDirectory.get_current_head", lambda self: "abc")

    class _V:
        def cached(self, p):
            return {"url": ""}

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())
    with pytest.raises(DatabaseError, match="vault api/db.url is empty"):
        await checks.check_db_at_head()


async def test_check_db_at_head_passes_when_aligned(monkeypatch):
    monkeypatch.setattr("alembic.script.ScriptDirectory.get_current_head", lambda self: "abc")

    class _V:
        def cached(self, p):
            return {"url": "postgresql+asyncpg://x@h/d"}

    monkeypatch.setattr("app.boot.checks.get_vault", lambda: _V())

    class _Conn:
        async def fetchrow(self, _sql):
            return {"version_num": "abc"}

        async def close(self):
            pass

    async def _fake_connect(_dsn):
        return _Conn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", _fake_connect)

    await checks.check_db_at_head()  # no raise


# ---- 4: classifier_loaded=false ------------------------------------------


def test_check_model_server_loaded_false(monkeypatch):
    monkeypatch.setattr(
        "app.boot.checks.ModelServerClient.health",
        lambda self: {"classifier_loaded": False},
    )
    with pytest.raises(ClassifierUnavailable, match="classifier_loaded=false"):
        checks.check_model_server_loaded()


def test_check_model_server_loaded_unreachable(monkeypatch):
    def _raise(self):
        raise ClassifierUnavailable("connection refused")

    monkeypatch.setattr("app.boot.checks.ModelServerClient.health", _raise)
    with pytest.raises(ClassifierUnavailable, match="connection refused"):
        checks.check_model_server_loaded()


def test_check_model_server_loaded_env_bypass(monkeypatch):
    # MC_BOOT_SKIP_CLASSIFIER=1 must short-circuit before the network call,
    # otherwise CI (which has no classifier artifacts) cannot boot the API.
    def _should_not_be_called(self):
        raise AssertionError("network call should be skipped under bypass")

    monkeypatch.setattr("app.boot.checks.ModelServerClient.health", _should_not_be_called)
    monkeypatch.setenv("MC_BOOT_SKIP_CLASSIFIER", "1")
    checks.check_model_server_loaded()  # no raise


# ---- 5: weights SHA mismatch ---------------------------------------------


def test_check_classifier_weights_sha_mismatch(monkeypatch):
    monkeypatch.setattr("app.boot.checks.WEIGHTS_SHA256", "aaa")
    monkeypatch.setattr(
        "app.boot.checks.ModelServerClient.health",
        lambda self: {"classifier_loaded": True, "weights_sha": "bbb"},
    )
    with pytest.raises(InfraError, match="SHA mismatch"):
        checks.check_classifier_weights_sha()


def test_check_classifier_weights_sha_empty_pin_passes(monkeypatch):
    """Unpinned (empty string) — boot allows; CI rejects on main."""
    monkeypatch.setattr("app.boot.checks.WEIGHTS_SHA256", "")
    checks.check_classifier_weights_sha()  # no raise even without a model-server


# ---- 6: Langfuse auth ----------------------------------------------------


def test_check_langfuse_auth_propagates_llm_error(monkeypatch):
    def _raise():
        raise LLMProviderError("auth_check failed")

    monkeypatch.setattr("app.infra.tracing.init_tracing", _raise)
    with pytest.raises(LLMProviderError, match="auth_check failed"):
        checks.check_langfuse_auth()


# ---- 7: eval threshold = 0 (mandated) ------------------------------------


def test_check_eval_thresholds_zero_floor_fails(tmp_path: Path):
    p = tmp_path / "eval_thresholds.yaml"
    p.write_text(
        "classification:\n  macro_f1: { floor: 0.0, regression_margin: 0.02 }\n",
        encoding="utf-8",
    )
    with pytest.raises(InfraError, match=r"floor=0\.0"):
        checks.check_eval_thresholds(yaml_path=p)


def test_check_eval_thresholds_ge_one_fails(tmp_path: Path):
    p = tmp_path / "eval_thresholds.yaml"
    p.write_text(
        "rag:\n  hit_at_5: { floor: 1.0, regression_margin: 0.03 }\n",
        encoding="utf-8",
    )
    with pytest.raises(InfraError, match=r"floor=1\.0"):
        checks.check_eval_thresholds(yaml_path=p)


def test_check_eval_thresholds_real_file_passes():
    """The committed eval_thresholds.yaml must satisfy the gate."""
    checks.check_eval_thresholds()


def test_check_eval_thresholds_must_pass_bool_is_skipped(tmp_path: Path):
    p = tmp_path / "eval_thresholds.yaml"
    p.write_text("redaction:\n  must_pass: true\n  floor: 0.5\n", encoding="utf-8")
    checks.check_eval_thresholds(yaml_path=p)


# ---- 8: prompt SHA mismatch (mandated) -----------------------------------


def test_check_prompt_shas_byte_change_fails(monkeypatch, tmp_path: Path):
    """Mutate one byte in a committed prompt → check #8 names the file."""
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    (fake_dir / "hyde.md").write_text("hello", encoding="utf-8")

    monkeypatch.setattr("prompts._registry.PROMPTS_DIR", fake_dir)
    monkeypatch.setattr(
        "prompts._registry.PROMPT_SHAS",
        {"hyde": "0" * 64},  # wrong on purpose
    )

    with pytest.raises(InfraError, match="hyde.md"):
        checks.check_prompt_shas()


def test_check_prompt_shas_missing_file_fails(monkeypatch, tmp_path: Path):
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    monkeypatch.setattr("prompts._registry.PROMPTS_DIR", fake_dir)
    monkeypatch.setattr("prompts._registry.PROMPT_SHAS", {"ghost": "a" * 64})

    with pytest.raises(InfraError, match="ghost.md MISSING"):
        checks.check_prompt_shas()


def test_check_prompt_shas_real_files_match():
    """Committed prompts must match the pinned SHAs."""
    checks.check_prompt_shas()


def test_check_prompt_shas_empty_pin_passes(monkeypatch):
    monkeypatch.setattr("prompts._registry.PROMPT_SHAS", {})
    checks.check_prompt_shas()  # bootstrap mode allowed
