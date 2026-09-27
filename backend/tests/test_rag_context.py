"""app/rag/context.py: pure, DB-free unit tests for the LLM context
builder - source ordering, chunk/character limits, truncation, and that no
internal/authorization detail is ever embedded in the built prompt text.
See docs/LLM_GENERATION.md, "Context construction".
"""

import uuid

from app.rag.context import build_context
from app.schemas.rag import RagSearchResult


def _result(**overrides) -> RagSearchResult:
    defaults = dict(
        document_id=uuid.uuid4(),
        document_title="Test Document",
        document_type="HOSPITAL_POLICY",
        chunk_id=uuid.uuid4(),
        chunk_index=0,
        content="Some chunk content.",
        score=0.5,
        page=None,
        section=None,
    )
    defaults.update(overrides)
    return RagSearchResult(**defaults)


# --- 1. correct source ordering -------------------------------------------


def test_sources_are_numbered_in_input_order():
    results = [_result(document_title="First"), _result(document_title="Second"), _result(document_title="Third")]
    bundle = build_context(results, max_chunks=10, max_chars=10_000)
    assert [s.number for s in bundle.sources] == [1, 2, 3]
    assert [s.result.document_title for s in bundle.sources] == ["First", "Second", "Third"]
    assert bundle.text.index("SOURCE 1") < bundle.text.index("SOURCE 2") < bundle.text.index("SOURCE 3")


# --- 2. maximum chunk count -----------------------------------------------


def test_max_chunks_is_respected():
    results = [_result(document_title=f"Doc {i}") for i in range(10)]
    bundle = build_context(results, max_chunks=3, max_chars=10_000)
    assert len(bundle.sources) == 3
    assert "SOURCE 4" not in bundle.text


# --- 3. maximum character limit -------------------------------------------


def test_max_chars_stops_adding_sources():
    results = [_result(content="x" * 500, document_title=f"Doc {i}") for i in range(10)]
    bundle = build_context(results, max_chunks=10, max_chars=1000)
    assert len(bundle.text) <= 1000
    assert len(bundle.sources) < 10


# --- 4. large individual chunk handling ------------------------------------


def test_oversized_single_chunk_is_truncated_not_dropped():
    huge = _result(content="y" * 50_000, document_title="Huge Doc")
    bundle = build_context([huge], max_chunks=5, max_chars=1000)
    assert len(bundle.sources) == 1  # still included, just truncated
    assert len(bundle.text) <= 1000
    assert "SOURCE 1" in bundle.text
    assert "Huge Doc" in bundle.text


def test_truncated_chunk_does_not_starve_a_later_useful_source():
    """A chunk that would only leave a sliver of budget is skipped
    entirely rather than included as a near-useless fragment."""
    first = _result(content="a" * 950, document_title="First")
    second = _result(content="b" * 200, document_title="Second")
    bundle = build_context([first, second], max_chunks=5, max_chars=1000)
    # first source consumes almost the whole budget; second has no room
    # for a *useful* amount of content, so it's dropped instead of kept
    # as a near-empty fragment.
    assert len(bundle.sources) == 1
    assert bundle.sources[0].result.document_title == "First"


# --- 5. unicode content ----------------------------------------------------


def test_unicode_content_is_handled_and_truncated_safely():
    unicode_content = "héllo wörld 你好世界 🏥🩺💊 " * 200
    result = _result(content=unicode_content, document_title="Unicode Doc")
    bundle = build_context([result], max_chunks=5, max_chars=500)
    assert len(bundle.sources) == 1
    # never raises a UnicodeDecodeError/EncodeError-shaped exception, and
    # produces valid, re-encodable text
    bundle.text.encode("utf-8")


def test_unicode_content_under_budget_is_preserved_exactly():
    unicode_content = "日本語のテキストです。緊急治療プロトコル。"
    result = _result(content=unicode_content, document_title="Japanese Doc")
    bundle = build_context([result], max_chunks=5, max_chars=10_000)
    assert unicode_content in bundle.text


# --- 6. empty content -------------------------------------------------


def test_empty_content_result_is_still_included():
    result = _result(content="", document_title="Empty Doc")
    bundle = build_context([result], max_chunks=5, max_chars=10_000)
    assert len(bundle.sources) == 1
    assert "SOURCE 1" in bundle.text
    assert "Empty Doc" in bundle.text


def test_empty_results_list_produces_empty_bundle():
    bundle = build_context([], max_chunks=5, max_chars=10_000)
    assert bundle.sources == []
    assert bundle.text == ""


# --- 7. source numbering (labels match position, not database order) ------


def test_source_numbers_are_stable_and_sequential_even_with_gaps_from_dropped_sources():
    results = [_result(content="z" * 900, document_title="Big"), _result(content="small", document_title="Small")]
    bundle = build_context(results, max_chunks=5, max_chars=950)
    numbers = [s.number for s in bundle.sources]
    assert numbers == list(range(1, len(numbers) + 1))  # no gaps, e.g. never [1, 3]


# --- 8/9/10. no authorization metadata / storage paths / embeddings -------


def test_context_text_never_contains_internal_identifiers_or_score():
    result = _result(document_title="Policy Doc", score=0.987654321)
    bundle = build_context([result], max_chunks=5, max_chars=10_000)
    assert str(result.document_id) not in bundle.text
    assert str(result.chunk_id) not in bundle.text
    assert "0.987654321" not in bundle.text
    assert "score" not in bundle.text.lower()


def test_context_text_never_contains_storage_or_embedding_language():
    result = _result(content="Ordinary policy content about parking permits.")
    bundle = build_context([result], max_chunks=5, max_chars=10_000)
    lowered = bundle.text.lower()
    for forbidden in ("embedding", "vector(", "storage_key", "/data/documents", "hospital_id", "role_id"):
        assert forbidden not in lowered


def test_context_includes_page_and_section_when_present_but_not_when_absent():
    with_meta = _result(page=4, section="Cancellation", document_title="With Meta")
    without_meta = _result(page=None, section=None, document_title="Without Meta")
    bundle = build_context([with_meta, without_meta], max_chunks=5, max_chars=10_000)
    assert "Page: 4" in bundle.text
    assert "Section: Cancellation" in bundle.text
    # the second source's block must not carry over the first's page/section
    without_block = bundle.text.split("SOURCE 2")[1]
    assert "Page:" not in without_block
    assert "Section:" not in without_block
