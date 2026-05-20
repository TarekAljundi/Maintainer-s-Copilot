"""Prompt registry w/ SHA-pinning. Boot check #8 refuses to start the app
when any file's actual SHA-256 differs from the pinned value below.

Editing flow:
    1. Edit a prompts/*.md file.
    2. Run `uv run python -m scripts.bump_prompt_shas` (or `python scripts/bump_prompt_shas.py`)
       — rewrites the PROMPT_SHAS dict in-place.
    3. Commit both the .md change and this file together.

Runtime usage:
    from prompts._registry import Prompt
    Prompt.HYDE.render(query="how do I groupby?")
    Prompt.SYSTEM.text  # raw string, no substitution
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


PROMPT_SHAS: dict[str, str] = {
    "classify_few_shot": "03170ec39202680a541c768cb1fda232be6e05591ab3a710e3bd5dbdf78bf9d9",
    "hyde": "8777279f68cdcf7e93e9836987ad671500def27a9ce1e7b21ebe6ed457c93714",
    "summarize": "b2391f65a24024a83a19ea74a1b00cd019be3f18cbd940925229de9b9bf26e24",
    "system": "4838c26820f0a990074a8ce4ea9b69690b8e817354be57c54321227ed008a58f",
}


class Prompt(Enum):
    """Typed accessor — `Prompt.HYDE.text` / `Prompt.HYDE.render(**ctx)`.

    The enum value is the basename (no `.md` suffix), which is also the key
    in PROMPT_SHAS so the runtime can join them.
    """

    SYSTEM = "system"
    HYDE = "hyde"
    SUMMARIZE = "summarize"
    CLASSIFY_FEW_SHOT = "classify_few_shot"

    @property
    def path(self) -> Path:
        return PROMPTS_DIR / f"{self.value}.md"

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def render(self, **ctx: object) -> str:
        """`{name}` -> `str(ctx[name])`. Matches the inline pattern already
        used by hyde.generate (no Jinja for one-shot variables)."""
        out = self.text
        for k, v in ctx.items():
            out = out.replace("{" + k + "}", str(v))
        return out


def sha256_file(path: Path) -> str:
    """LF-normalized SHA so Windows checkouts (CRLF) and CI Linux checkouts (LF)
    produce the same digest. Without this, every Windows-authored pin breaks CI
    and vice versa."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
