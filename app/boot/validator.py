"""BootValidator — runs the 8 boot checks in order. Single point of failure
for the lifespan: any check raising an InfraError subclass propagates up and
becomes `SystemExit(1)` with a `BOOT FAIL #N: ...` message.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.boot import checks
from app.domain.exceptions import AppError, InfraError
from app.infra.logging import get_logger

log = get_logger(__name__)

CheckFn = Callable[[], None] | Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _BootCheck:
    number: int
    name: str
    fn: CheckFn


_CHECKS: tuple[_BootCheck, ...] = (
    _BootCheck(1, "vault_health", checks.check_vault_health),
    _BootCheck(2, "vault_paths", checks.check_vault_paths),
    _BootCheck(3, "db_at_head", checks.check_db_at_head),
    _BootCheck(4, "classifier_loaded", checks.check_model_server_loaded),
    _BootCheck(5, "classifier_weights_sha", checks.check_classifier_weights_sha),
    _BootCheck(6, "langfuse_auth", checks.check_langfuse_auth),
    _BootCheck(7, "eval_thresholds", checks.check_eval_thresholds),
    _BootCheck(8, "prompt_shas", checks.check_prompt_shas),
)


class BootValidator:
    """Sequential orchestrator. Stop-at-first-failure semantics — the first
    failing check raises, the rest are skipped, so a single boot failure log
    line names exactly the broken thing (no cascade noise)."""

    def __init__(self, checks_seq: tuple[_BootCheck, ...] | None = None) -> None:
        # Resolved lazily so tests that monkey-patch `_CHECKS` after import
        # still take effect (default args bind at def time, not call time).
        self._checks = checks_seq

    async def validate_all(self) -> None:
        active = self._checks if self._checks is not None else _CHECKS
        for c in active:
            try:
                result = c.fn()
                if asyncio.iscoroutine(result):
                    await result
            except AppError as exc:
                _emit_boot_fail(c.number, c.name, exc)
                raise SystemExit(1) from exc
            except Exception as exc:
                wrapped = InfraError(f"check {c.name} raised unexpected error: {exc}")
                _emit_boot_fail(c.number, c.name, wrapped)
                raise SystemExit(1) from exc
            log.info("boot_check_ok", number=c.number, name=c.name)


def _emit_boot_fail(number: int, name: str, exc: AppError) -> None:
    msg = f"BOOT FAIL #{number} ({name}): {exc.message or str(exc)}"
    print(msg, file=sys.stderr)
    log.error(
        "boot_check_fail",
        number=number,
        name=name,
        code=exc.code,
        message=exc.message or str(exc),
    )
