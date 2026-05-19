"""Classifier-input preprocessing. Shared by training script and /classify endpoint.

Kept here (not in app/) because both training and inference must apply identical
transforms — train/serve skew would invalidate the eval numbers in DECISIONS.md.
"""

from __future__ import annotations

import re

CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]+`")
CODE_PLACEHOLDER = " <CODE> "


def replace_code_blocks(text: str | None) -> str:
    """Strip fenced + inline code to a single <CODE> placeholder."""
    if not text:
        return ""
    text = CODE_FENCE.sub(CODE_PLACEHOLDER, text)
    text = INLINE_CODE.sub(CODE_PLACEHOLDER, text)
    return text


def build_input(title: str | None, body: str | None) -> str:
    title = (title or "").strip()
    body = replace_code_blocks(body).strip()
    return f"{title}\n\n{body}" if body else title
