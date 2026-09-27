"""Deterministic, paragraph- and heading-aware chunking - see
docs/RAG_INGESTION.md, "Chunking". Two inputs from config.py drive it:
`document_chunk_size_chars` (default 1200 - roughly 200-300 tokens, a
standard RAG chunk size that keeps a chunk small enough to embed
meaningfully while large enough to hold real context) and
`document_chunk_overlap_chars` (default 150, carried from the tail of one
chunk into the start of the next so a fact split across a chunk boundary
still shows up whole in at least one chunk).

Never LLM-based, never non-deterministic: the same input always produces
the same chunks.
"""

import dataclasses
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclasses.dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    page_number: int | None
    heading: str | None


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]


def _hard_split(text: str, max_len: int) -> list[str]:
    """A single paragraph longer than one whole chunk (rare, but not
    impossible) is split on whitespace boundaries so nothing is silently
    dropped and no chunk ever exceeds max_len - never mid-word."""
    words = text.split(" ")
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current_len + added > max_len and current:
            pieces.append(" ".join(current))
            current = []
            current_len = 0
            added = len(word)
        current.append(word)
        current_len += added
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_pages(pages: list[tuple[str, int | None]], *, chunk_size: int, overlap: int) -> list[Chunk]:
    """`pages` is a list of (cleaned_text, page_number) - page_number is
    None for formats with no pagination concept (.txt/.md/.docx). Headings
    (markdown `#`/`##`/... prefixes - see extraction.py for how DOCX
    heading styles become these too) always start a new chunk, so a
    section's content is never silently merged with the section before it;
    overlap is not carried across a heading boundary for the same reason.
    """
    blocks: list[tuple[str, int | None, bool]] = []
    for page_text, page_number in pages:
        for paragraph in _paragraphs(page_text):
            blocks.append((paragraph, page_number, bool(_HEADING_RE.match(paragraph))))

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_len = 0
    buffer_page: int | None = None
    current_heading: str | None = None
    index = 0

    def flush() -> None:
        nonlocal buffer, buffer_len, buffer_page, index
        if not buffer:
            return
        content = "\n\n".join(buffer).strip()
        if content:
            chunks.append(Chunk(index=index, content=content, page_number=buffer_page, heading=current_heading))
            index += 1
        buffer = []
        buffer_len = 0
        buffer_page = None

    for text, page_number, is_heading in blocks:
        if is_heading:
            # Flush whatever was accumulated *under the previous heading*
            # before changing current_heading - otherwise the outgoing
            # chunk gets mislabeled with the new section's heading instead
            # of the one that was actually active while its content was
            # being buffered.
            flush()
            match = _HEADING_RE.match(text)
            current_heading = match.group(2).strip() if match else text
            continue

        for piece in _hard_split(text, chunk_size) if len(text) > chunk_size else [text]:
            if buffer_len + len(piece) + 2 > chunk_size and buffer:
                flush()
                if overlap > 0 and chunks:
                    tail = chunks[-1].content[-overlap:]
                    buffer = [tail]
                    buffer_len = len(tail)
                    buffer_page = page_number
            if buffer_page is None:
                buffer_page = page_number
            buffer.append(piece)
            buffer_len += len(piece) + 2

    flush()
    return chunks
