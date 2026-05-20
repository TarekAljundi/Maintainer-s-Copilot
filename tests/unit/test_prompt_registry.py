"""PromptRegistry behavior tests — render substitutes {var}, accessors expose
the raw text, and the SHA helper agrees with the bump script."""

from __future__ import annotations


from prompts._registry import PROMPT_SHAS, PROMPTS_DIR, Prompt, sha256_file


def test_each_enum_has_existing_file():
    for p in Prompt:
        assert p.path.exists(), f"missing prompt file: {p.path}"


def test_render_substitutes_placeholder():
    rendered = Prompt.HYDE.render(query="how do I groupby pandas")
    assert "how do I groupby pandas" in rendered
    assert "{query}" not in rendered


def test_text_returns_raw_unmodified():
    raw = Prompt.SYSTEM.text
    assert isinstance(raw, str)
    assert raw  # non-empty
    # SYSTEM contains no {var} placeholders today; assert idempotent .render()
    assert Prompt.SYSTEM.render() == raw


def test_sha256_helper_matches_pin():
    for name, pinned in PROMPT_SHAS.items():
        actual = sha256_file(PROMPTS_DIR / f"{name}.md")
        assert actual == pinned, f"PROMPT_SHAS for {name} stale — run bump_prompt_shas.py"


def test_render_unknown_var_left_unsubstituted():
    """Defensive: unknown vars stay as `{name}` rather than crashing — the
    LLM will surface the bug verbatim, easier to catch in eval."""
    out = Prompt.HYDE.render(unrelated="x")
    assert "{query}" in out
