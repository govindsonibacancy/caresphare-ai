"""app/sources/citations.py: pure unit tests for citation extraction and
validation - no DB, no HTTP, no LLM dependency. See
docs/SOURCES_AND_CITATIONS.md, "Citation validation".
"""

from app.sources.citations import extract_cited_numbers, validate_citations
from app.sources.models import SourceType
from app.sources.registry import SourceRegistry


def _registry(count: int) -> SourceRegistry:
    registry = SourceRegistry()
    for i in range(count):
        registry.add(type=SourceType.DOCUMENT, label=f"doc {i + 1}")
    return registry


# --- valid citation / multiple valid citations -----------------------------


def test_single_valid_citation_is_recognized():
    registry = _registry(1)
    result = validate_citations("The fee is 25 dollars. [Source 1]", registry)
    assert result.valid == [1]
    assert result.invalid == []


def test_multiple_valid_citations_are_recognized_in_order():
    registry = _registry(3)
    text = "First fact [Source 1]. Second fact [Source 2]. Third fact [Source 3]."
    result = validate_citations(text, registry)
    assert result.valid == [1, 2, 3]
    assert result.invalid == []


# --- duplicate citations ----------------------------------------------------


def test_duplicate_citation_is_normalized_to_one_entry():
    registry = _registry(1)
    text = "Fact one [Source 1]. Restated fact [Source 1] again. And once more [Source 1]."
    result = validate_citations(text, registry)
    assert result.valid == [1]  # not [1, 1, 1]


def test_repeated_citation_of_different_sources_normalizes_each_once():
    registry = _registry(2)
    text = "[Source 1] [Source 2] [Source 1] [Source 2] [Source 1]"
    result = validate_citations(text, registry)
    assert result.valid == [1, 2]


# --- unknown source ----------------------------------------------------


def test_unknown_source_number_is_invalid():
    registry = _registry(1)
    result = validate_citations("According to the policy [Source 99], the fee is 25 dollars.", registry)
    assert result.valid == []
    assert result.invalid == [99]


def test_mix_of_valid_and_unknown_sources():
    registry = _registry(2)
    text = "[Source 1] is fine, but [Source 2] and [Source 3] are cited too."
    result = validate_citations(text, registry)
    assert result.valid == [1, 2]
    assert result.invalid == [3]


# --- malformed source -------------------------------------------------


def test_malformed_citation_syntax_produces_no_match():
    registry = _registry(1)
    for malformed in ["[Source]", "[Source abc]", "Source 1", "[Sources 1]", "(Source 1)"]:
        result = validate_citations(malformed, registry)
        assert result.valid == []
        assert result.invalid == []


def test_malformed_citation_does_not_raise():
    registry = _registry(1)
    # must never raise, regardless of how garbled the text is
    validate_citations("[[[Source Source 1 2 ]]] {{bad}} \x00\x01", registry)


# --- Source 0 / negative / very large number -------------------------


def test_source_zero_is_invalid():
    registry = _registry(3)
    result = validate_citations("[Source 0]", registry)
    assert result.invalid == [0]
    assert result.valid == []


def test_negative_source_is_invalid():
    registry = _registry(3)
    result = validate_citations("[Source -1]", registry)
    assert result.invalid == [-1]
    assert result.valid == []


def test_very_large_source_number_is_invalid():
    registry = _registry(3)
    result = validate_citations("[Source 999999999]", registry)
    assert result.invalid == [999999999]
    assert result.valid == []


# --- empty registry / empty text -----------------------------------------


def test_empty_registry_makes_every_citation_invalid():
    registry = SourceRegistry()
    result = validate_citations("This claim cites [Source 1].", registry)
    assert result.valid == []
    assert result.invalid == [1]


def test_no_citations_in_text_is_not_an_error():
    registry = _registry(2)
    result = validate_citations("This answer cites nothing at all.", registry)
    assert result.valid == []
    assert result.invalid == []


# --- extract_cited_numbers directly ----------------------------------------


def test_extract_cited_numbers_preserves_duplicates_and_order():
    assert extract_cited_numbers("[Source 2] then [Source 1] then [Source 2] again") == [2, 1, 2]


def test_extract_cited_numbers_is_case_insensitive():
    assert extract_cited_numbers("[source 1] and [SOURCE 2] and [SoUrCe 3]") == [1, 2, 3]
