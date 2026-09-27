"""Whitespace normalization and obvious extraction-artifact removal only -
see docs/RAG_INGESTION.md, "Text cleaning". This never rewrites, summarizes,
or otherwise reinterprets the source material's actual words: the RAG
knowledge base must represent the source document, not an LLM's
interpretation of it. Paragraph boundaries (blank lines) and headings
(markdown `#` prefixes) are preserved deliberately - chunking.py depends on
both to find section boundaries.
"""

import re

_MULTIPLE_BLANK_LINES = re.compile(r"\n{3,}")
_MULTIPLE_SPACES = re.compile(r"[ \t]{2,}")
_FORM_FEED = "\x0c"  # page-break artifact some PDF extractors emit


def clean_text(text: str) -> str:
    text = text.replace(_FORM_FEED, "\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_MULTIPLE_SPACES.sub(" ", line.strip()) for line in text.split("\n")]
    text = "\n".join(lines)
    text = _MULTIPLE_BLANK_LINES.sub("\n\n", text)
    return text.strip()
