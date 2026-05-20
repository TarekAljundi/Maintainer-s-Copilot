"""One positive + one negative per pattern category, plus redact_obj walk.

PRD §Observability §Redaction patterns + §Tier 1 tests.
"""

from __future__ import annotations

import pytest

from app.infra.redaction import redact, redact_obj


# Each row: (category, positive_input, negative_input).
# - positive_input: must produce `[REDACTED:<category>]`
# - negative_input: must pass through unchanged
PATTERN_CASES = [
    (
        "openai_key",
        "key=sk-abcDEF1234567890abcDEF1234567890abcDEF rest",
        "sk-too-short",
    ),
    (
        "anthropic_key",
        "Authorization: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz",
        "sk-ant-",
    ),
    (
        "groq_key",
        "GROQ=gsk_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        "gsk_short",
    ),
    (
        "github_token",
        "token=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 done",
        "ghp_short",
    ),
    (
        "aws_access_key",
        "creds AKIAIOSFODNN7EXAMPLE ok",
        "AKIA123",  # too short
    ),
    (
        "google_api_key",
        "GOOGLE_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuv",  # AIza + 35
        "AIzaShort",
    ),
    (
        "slack_token",
        "slack=xoxb-1234567890-abcdefghij ok",
        "xoxb-short",
    ),
    (
        "jwt",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.signature_part",
        "eyJ.foo.bar",  # not all base64url + too short
    ),
    (
        "url_credentials",
        "connect to postgres://copilot:hunter2@db:5432/copilot now",
        "https://example.com/path",  # no creds
    ),
    (
        "password_kv",
        "PASSWORD=hunter2",
        "passport=blue",  # not the password keyword
    ),
    (
        "email",
        "ping me at user.name@example.com please",
        "user@local",  # no TLD
    ),
    (
        "user_path",
        r"trace from C:\Users\alice\Desktop\foo.py line 42",
        r"C:\Program Files\app",  # not the Users segment
    ),
]


@pytest.mark.parametrize("category,positive,negative", PATTERN_CASES)
def test_pattern_positive(category, positive, negative):
    out = redact(positive)
    assert f"[REDACTED:{category}]" in out, (
        f"category {category} failed on positive input — got {out!r}"
    )


@pytest.mark.parametrize("category,positive,negative", PATTERN_CASES)
def test_pattern_negative(category, positive, negative):
    out = redact(negative)
    assert out == negative, (
        f"category {category} unexpectedly fired on negative input — got {out!r}"
    )


def test_url_credentials_runs_before_password_kv():
    """Order regression: full URL with creds should redact as one token, not
    leak the user/host because password_kv ate the password substring first."""
    src = "DATABASE_URL=postgres://copilot:hunter2@db:5432/copilot"
    out = redact(src)
    assert "[REDACTED:url_credentials]" in out
    assert "hunter2" not in out
    assert "copilot:" not in out


def test_redact_obj_walks_nested_structures():
    src = {
        "headers": {"Authorization": "Bearer ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"},
        "payloads": [
            {"key": "gsk_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"},
            "plain string",
            ("tuple-with-sk-abcDEF1234567890abcDEF1234567890abcDEF",),
        ],
        "count": 7,
    }
    out = redact_obj(src)
    assert "[REDACTED:github_token]" in out["headers"]["Authorization"]
    assert "[REDACTED:groq_key]" in out["payloads"][0]["key"]
    assert out["payloads"][1] == "plain string"
    assert "[REDACTED:openai_key]" in out["payloads"][2][0]
    assert out["count"] == 7


def test_dict_keys_not_redacted():
    """Keys are structural metadata; only values are walked."""
    src = {"user@example.com": "value"}
    out = redact_obj(src)
    assert "user@example.com" in out  # key preserved
    assert out["user@example.com"] == "value"


def test_empty_and_none():
    assert redact("") == ""
    assert redact_obj(None) is None
    assert redact_obj(42) == 42
