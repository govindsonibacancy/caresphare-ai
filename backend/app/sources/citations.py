"""Citation extraction/validation - see docs/SOURCES_AND_CITATIONS.md,
"Citation validation". The model's own `[Source N]` markers in its answer
text are parsed and cross-checked against the `SourceRegistry` that
already existed *before* generation ran; this module never creates a
source, never widens the registry, and never fails the whole answer over
a malformed or hallucinated citation - an invalid marker is simply
excluded from `valid`, exactly like "malformed citation -> ignore safely"
requires.

This is provenance bookkeeping, not fact-checking: a valid citation means
"the model referenced a source the backend actually supplied", never
"the backend has verified the model's factual claim is correct" - see
docs/SOURCES_AND_CITATIONS.md, "What citations do NOT prove".
"""

import re
from dataclasses import dataclass

from app.sources.registry import SourceRegistry

# Matches "[Source N]" (case-insensitive, optional leading minus so a
# negative reference is captured and then rejected as invalid rather than
# silently failing to match at all). Deliberately does NOT match
# "[Source]" (no number) or "[Source abc]" - malformed syntax like that
# simply produces no match, never an exception.
_CITATION_RE = re.compile(r"\[Source\s+(-?\d+)\]", re.IGNORECASE)


@dataclass(frozen=True)
class ValidatedCitations:
    valid: list[int]
    invalid: list[int]


def extract_cited_numbers(text: str) -> list[int]:
    """Every `[Source N]`-shaped marker in `text`, in the order they
    appear, duplicates included (deduplication/normalization happens in
    `validate_citations`, not here) - see docs/SOURCES_AND_CITATIONS.md,
    "Citation validation"."""
    return [int(match) for match in _CITATION_RE.findall(text)]


def validate_citations(text: str, registry: SourceRegistry) -> ValidatedCitations:
    """Splits every cited number into `valid` (a real, backend-registered
    source - normalized to the unique set, in first-seen order) or
    `invalid` (out of range, including 0, negative, or larger than
    anything the registry ever assigned - i.e. a hallucinated citation).
    An invalid number is reported for visibility/testing; it never becomes
    a `Source` object and never appears in the response's `sources` list -
    see docs/SOURCES_AND_CITATIONS.md, "Important distinction".
    """
    seen_valid: list[int] = []
    seen_invalid: list[int] = []
    for number in extract_cited_numbers(text):
        if registry.is_valid(number):
            if number not in seen_valid:
                seen_valid.append(number)
        elif number not in seen_invalid:
            seen_invalid.append(number)
    return ValidatedCitations(valid=seen_valid, invalid=seen_invalid)
