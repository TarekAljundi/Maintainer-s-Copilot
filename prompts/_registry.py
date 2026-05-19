"""Prompt registry w/ SHA-pinning. Boot check refuses if file SHA != PROMPT_SHAS value.

After editing any prompts/*.md, run scripts/bump_prompt_shas.py to regenerate this.
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

PROMPT_SHAS: dict[str, str] = {
    # filled by bump_prompt_shas.py
    # "system":            "<sha256>",
    # "hyde":              "<sha256>",
    # "summarize":         "<sha256>",
    # "classify_few_shot": "<sha256>",
}
