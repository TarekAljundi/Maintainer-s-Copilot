"""Recompute SHA-256 for every prompts/*.md and rewrite the PROMPT_SHAS dict
literal in prompts/_registry.py. Idempotent. Run after editing any prompt.

Usage:
    python scripts/bump_prompt_shas.py [--check]

With --check: exits non-zero if the registry is stale (CI guard).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
REGISTRY = PROMPTS_DIR / "_registry.py"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _current_shas() -> dict[str, str]:
    return {p.stem: _sha(p) for p in sorted(PROMPTS_DIR.glob("*.md"))}


def _render_dict(shas: dict[str, str]) -> str:
    lines = ["PROMPT_SHAS: dict[str, str] = {"]
    for name in sorted(shas):
        lines.append(f'    "{name}": "{shas[name]}",')
    lines.append("}")
    return "\n".join(lines)


_DICT_RE = re.compile(
    r"PROMPT_SHAS:\s*dict\[str,\s*str\]\s*=\s*\{[^}]*\}",
    re.DOTALL,
)


def main(check_only: bool = False) -> int:
    shas = _current_shas()
    new_block = _render_dict(shas)
    old = REGISTRY.read_text(encoding="utf-8")
    if not _DICT_RE.search(old):
        print("PROMPT_SHAS dict not found in _registry.py", file=sys.stderr)
        return 2
    new = _DICT_RE.sub(new_block, old)
    if check_only:
        if new != old:
            print("PROMPT_SHAS stale — run `python scripts/bump_prompt_shas.py`", file=sys.stderr)
            return 1
        return 0
    if new != old:
        REGISTRY.write_text(new, encoding="utf-8")
        print(f"bumped PROMPT_SHAS ({len(shas)} entries)")
    else:
        print("PROMPT_SHAS already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main(check_only="--check" in sys.argv[1:]))
