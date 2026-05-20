"""BootValidator orchestration: stop-at-first-failure, run-all-on-success,
SystemExit(1) propagates with the BOOT FAIL #N message in stderr.
"""

from __future__ import annotations

import pytest

from app.boot import validator as v
from app.domain.exceptions import DatabaseError


@pytest.mark.asyncio
async def test_validate_all_runs_every_check_on_success(monkeypatch):
    called: list[int] = []

    def _ok(n):
        def f():
            called.append(n)

        return f

    seq = tuple(v._BootCheck(i, f"c{i}", _ok(i)) for i in range(1, 9))
    monkeypatch.setattr(v, "_CHECKS", seq)

    await v.BootValidator().validate_all()
    assert called == list(range(1, 9))


@pytest.mark.asyncio
async def test_validate_all_stops_at_first_failure(monkeypatch, capsys):
    called: list[int] = []

    def _ok(n):
        def f():
            called.append(n)

        return f

    def _boom():
        called.append(3)
        raise DatabaseError("simulated db_at_head failure")

    seq = (
        v._BootCheck(1, "vault_health", _ok(1)),
        v._BootCheck(2, "vault_paths", _ok(2)),
        v._BootCheck(3, "db_at_head", _boom),
        v._BootCheck(4, "classifier_loaded", _ok(4)),
    )
    monkeypatch.setattr(v, "_CHECKS", seq)

    with pytest.raises(SystemExit) as exc_info:
        await v.BootValidator().validate_all()
    assert exc_info.value.code == 1
    assert called == [1, 2, 3]  # 4 never ran
    err = capsys.readouterr().err
    assert "BOOT FAIL #3" in err
    assert "db_at_head" in err
    assert "simulated db_at_head failure" in err


@pytest.mark.asyncio
async def test_validate_all_supports_async_checks(monkeypatch):
    flag = {"hit": False}

    async def _async_ok():
        flag["hit"] = True

    seq = (v._BootCheck(1, "async_one", _async_ok),)
    monkeypatch.setattr(v, "_CHECKS", seq)

    await v.BootValidator().validate_all()
    assert flag["hit"] is True


@pytest.mark.asyncio
async def test_unexpected_exception_wrapped_as_boot_fail(monkeypatch, capsys):
    def _raise_value_error():
        raise ValueError("totally unexpected")

    seq = (v._BootCheck(7, "eval_thresholds", _raise_value_error),)
    monkeypatch.setattr(v, "_CHECKS", seq)

    with pytest.raises(SystemExit):
        await v.BootValidator().validate_all()
    err = capsys.readouterr().err
    assert "BOOT FAIL #7" in err
    assert "totally unexpected" in err
