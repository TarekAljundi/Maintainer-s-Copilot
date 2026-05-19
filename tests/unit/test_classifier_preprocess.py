"""Classifier preprocess tests. Pure regex; no torch/HF imports."""

from __future__ import annotations

from model_server.preprocess import CODE_PLACEHOLDER, build_input, replace_code_blocks


def test_replace_code_blocks_fenced():
    out = replace_code_blocks("before ```python\nfoo()\n``` after")
    assert CODE_PLACEHOLDER.strip() in out
    assert "foo()" not in out


def test_replace_code_blocks_inline():
    out = replace_code_blocks("call `bar()` here")
    assert "bar()" not in out
    assert "<CODE>" in out


def test_replace_code_blocks_empty():
    assert replace_code_blocks("") == ""
    assert replace_code_blocks(None) == ""


def test_build_input_concatenates_title_body():
    out = build_input("Title here", "Body here")
    assert out.startswith("Title here")
    assert "Body here" in out


def test_build_input_empty_body():
    assert build_input("Just title", "") == "Just title"
    assert build_input("Just title", None) == "Just title"


def test_build_input_strips_code():
    out = build_input("T", "use ```py\nx=1\n``` here")
    assert "x=1" not in out
    assert "<CODE>" in out
