"""Request-scoped source registry - see docs/SOURCES_AND_CITATIONS.md,
"Source registry". A fresh `SourceRegistry` is created once per
`POST /api/rag/answer` call (app/llm/answer_service.py) and discarded
after the response is built - never persisted, never shared across
requests or users (see Phase 12's own instruction not to add citation
state to the database).

Only ever populated from data an authorized retrieval step already
produced (Phase 9's `search()` results, Phase 11's
`run_structured_query()` results) - there is no method here that queries
anything itself, so a source can never enter the registry without having
already passed the exact same authorization every other read of that data
requires.
"""

from dataclasses import dataclass, field

from app.sources.models import Source, SourceType


@dataclass
class SourceRegistry:
    _sources: list[Source] = field(default_factory=list)

    def add(self, *, type: SourceType, label: str, **fields: object) -> Source:
        """Assigns the next sequential number - deterministic, backend-
        controlled, never reused within one registry (see
        docs/SOURCES_AND_CITATIONS.md, "Source numbering")."""
        number = len(self._sources) + 1
        source = Source(number=number, id=f"source-{number}", type=type, label=label, **fields)
        self._sources.append(source)
        return source

    @property
    def sources(self) -> list[Source]:
        return list(self._sources)

    def __len__(self) -> int:
        return len(self._sources)

    def is_valid(self, number: int) -> bool:
        """Whether `number` names a real, backend-registered source - the
        only question that matters for citation validation (see
        app/sources/citations.py). A number outside `[1, len(self)]` -
        including 0, negative, or larger than what was ever registered -
        is never valid, regardless of what the model claims."""
        return 1 <= number <= len(self._sources)
