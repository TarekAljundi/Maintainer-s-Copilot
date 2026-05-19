"""spaCy en_core_web_sm + EntityRuler with regex patterns for code-shaped entities.

Labels: IDENTIFIER, FILE_PATH, ERROR_TYPE, VERSION, PR_REF, URL, MODULE.

The regexes themselves are exported as `PATTERNS` so the unit tests can verify
them as plain Python regexes without loading spaCy or downloading the model.
The spaCy pipeline applies them via `EntityRuler` with token-level `REGEX`
patterns, with one 2-token rule for `#1234` PR refs (spaCy splits `#` off).

`EntityRuler(overwrite_ents=True)` so our code-shaped labels win over generic
spaCy NER (ORG, PERSON, …) on the same span.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

LABELS: tuple[str, ...] = (
    "IDENTIFIER",
    "FILE_PATH",
    "ERROR_TYPE",
    "VERSION",
    "PR_REF",
    "URL",
    "MODULE",
)

# Single-token regex patterns. Each value matches what spaCy will see as one
# token after its default tokenizer runs. Anchored with ^...$ so the rule fires
# only on a full-token match (EntityRuler's REGEX uses `re.search` so we anchor
# explicitly).
PATTERNS: dict[str, str] = {
    # snake_case (must contain `_`) OR camelCase (alpha + internal capital), >=3 chars.
    "IDENTIFIER": r"^(?:[a-z][a-z0-9]*_[a-z0-9_]+|[a-z][a-z0-9]*[A-Z][a-zA-Z0-9]*)$",
    # foo/bar/baz.ext  OR  bare file.ext, common code/doc/config extensions.
    "FILE_PATH": r"^[\w./\\-]+\.(py|md|yaml|yml|json|toml|js|ts|jsx|tsx|rs|go|html|css|sh|cfg|ini)$",
    # PascalCase ending Error / Exception / Warning (typical Python error types).
    "ERROR_TYPE": r"^[A-Z][a-zA-Z]*?(?:Error|Exception|Warning)$",
    # 1.2 or 1.2.3 with optional leading `v` and optional pre-release suffix.
    "VERSION": r"^v?\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9.+-]*)$",
    # org/repo#1234  (single-token PR ref). Bare `#1234` handled as 2-token rule.
    "PR_REF": r"^[\w-]+/[\w-]+#\d+$",
    # http(s) URL.
    "URL": r"^https?://\S+$",
    # Dotted Python import path (>=2 segments).
    "MODULE": r"^[a-z_][\w]*\.[a-z_][\w.]+$",
}


def build_entity_ruler_patterns() -> list[dict[str, Any]]:
    """Return a list of EntityRuler-shaped patterns. One per label + the 2-token PR_REF."""
    patterns: list[dict[str, Any]] = []
    for label, regex in PATTERNS.items():
        patterns.append({"label": label, "pattern": [{"TEXT": {"REGEX": regex}}]})
    # Bare `#1234` — spaCy tokenizer splits `#` off the digits.
    patterns.append(
        {
            "label": "PR_REF",
            "pattern": [{"TEXT": "#"}, {"TEXT": {"REGEX": r"^\d+$"}}],
        }
    )
    return patterns


class NERService:
    """Loads spaCy + EntityRuler at construction. `extract(text)` returns dict list."""

    def __init__(self) -> None:
        import spacy  # type: ignore[import-not-found]

        self.nlp = spacy.load("en_core_web_sm")
        # Add the EntityRuler before the default NER so our rules feed `doc.ents`
        # via the union, and overwrite_ents=True lets explicit rules win on overlap.
        ruler = self.nlp.add_pipe(
            "entity_ruler",
            before="ner",
            config={"overwrite_ents": True, "validate": True},
        )
        ruler.add_patterns(build_entity_ruler_patterns())  # type: ignore[attr-defined]
        log.info("NER ready: en_core_web_sm + EntityRuler (%d patterns)", len(PATTERNS) + 1)

    def extract(self, text: str) -> list[dict[str, Any]]:
        if not text:
            return []
        doc = self.nlp(text)
        out: list[dict[str, Any]] = []
        for ent in doc.ents:
            # Filter to our 7 labels — drop spaCy's built-in ORG/GPE/PERSON noise.
            if ent.label_ not in LABELS:
                continue
            out.append(
                {
                    "label": ent.label_,
                    "text": ent.text,
                    "start": int(ent.start_char),
                    "end": int(ent.end_char),
                }
            )
        return out
