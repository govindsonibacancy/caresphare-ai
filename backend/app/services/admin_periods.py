"""Deterministic natural-language date-period resolution for Phase 14
admin queries - see docs/ADMIN_AI.md, "Date-range handling".

The LLM never computes or invents a date range. A fixed set of relative-
period phrases ("this month", "last week", ...) is recognized here by
plain regex and converted to an explicit `[start, end]` date range by
Python's own calendar arithmetic - the same "backend decides, LLM only
narrates" principle every other admin aggregate in this phase follows.
A phrase that is clearly date-shaped but not one of the recognized ones
(e.g. "the month before last", "two quarters ago") resolves to AMBIGUOUS
rather than being guessed at - see docs/ADMIN_AI.md, "Known limitations".
No query text at all, or text with no recognizable period phrase, means
no date filter: the caller gets an all-time answer, not an error.

There is no per-hospital timezone configuration in this project (see
app/core/config.py) - "today" is always today in UTC. Documented as a
known limitation, not silently pretended away.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum


class PeriodOutcome(StrEnum):
    NONE = "NONE"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class PeriodResult:
    outcome: PeriodOutcome
    start: date | None = None
    end: date | None = None  # inclusive
    label: str | None = None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return date(year, start_month, 1), _month_bounds(year, end_month)[1]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


# Checked in order; the first match wins. Each is a distinct, unambiguous
# relative-period phrase - never a substring that could also appear inside
# a longer, unsupported phrase (see docs/ADMIN_AI.md, "Known limitations"
# for what falls through to AMBIGUOUS instead).
_RESOLVED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\btoday\b", re.IGNORECASE), "today"),
    (re.compile(r"\byesterday\b", re.IGNORECASE), "yesterday"),
    (re.compile(r"\bthis\s+week\b", re.IGNORECASE), "this_week"),
    (re.compile(r"\blast\s+week\b", re.IGNORECASE), "last_week"),
    (re.compile(r"\bthis\s+month\b", re.IGNORECASE), "this_month"),
    (re.compile(r"\blast\s+month\b", re.IGNORECASE), "last_month"),
    (re.compile(r"\bthis\s+quarter\b", re.IGNORECASE), "this_quarter"),
    (re.compile(r"\blast\s+quarter\b", re.IGNORECASE), "last_quarter"),
    (re.compile(r"\bthis\s+year\b", re.IGNORECASE), "this_year"),
    (re.compile(r"\blast\s+year\b", re.IGNORECASE), "last_year"),
]

# Date-shaped language that is NOT one of the resolved phrases above -
# recognized only so it can be safely declined (AMBIGUOUS) instead of
# silently ignored (which would look like "all time" to the caller) or
# guessed at.
_AMBIGUOUS_HINT_RE = re.compile(
    r"\b\w+\s+(days?|weeks?|months?|quarters?|years?)\s+ago\b"
    r"|\b(month|week|quarter|year)\s+before\s+(the\s+)?(last|previous)\b"
    r"|\bwhen\s+i\s+(started|joined|began)\b",
    re.IGNORECASE,
)


def resolve_period(query: str, *, now: datetime | None = None) -> PeriodResult:
    now = now or datetime.now(UTC)
    today = now.date()

    for pattern, keyword in _RESOLVED_PATTERNS:
        if not pattern.search(query):
            continue
        if keyword == "today":
            return PeriodResult(PeriodOutcome.RESOLVED, today, today, "today")
        if keyword == "yesterday":
            d = today - timedelta(days=1)
            return PeriodResult(PeriodOutcome.RESOLVED, d, d, "yesterday")
        if keyword == "this_week":
            start = today - timedelta(days=today.weekday())
            return PeriodResult(PeriodOutcome.RESOLVED, start, start + timedelta(days=6), "this week")
        if keyword == "last_week":
            this_start = today - timedelta(days=today.weekday())
            start = this_start - timedelta(days=7)
            return PeriodResult(PeriodOutcome.RESOLVED, start, start + timedelta(days=6), "last week")
        if keyword == "this_month":
            start, end = _month_bounds(today.year, today.month)
            return PeriodResult(PeriodOutcome.RESOLVED, start, end, start.strftime("%B %Y"))
        if keyword == "last_month":
            year, month = _shift_month(today.year, today.month, -1)
            start, end = _month_bounds(year, month)
            return PeriodResult(PeriodOutcome.RESOLVED, start, end, start.strftime("%B %Y"))
        if keyword == "this_quarter":
            quarter = (today.month - 1) // 3 + 1
            start, end = _quarter_bounds(today.year, quarter)
            return PeriodResult(PeriodOutcome.RESOLVED, start, end, f"Q{quarter} {today.year}")
        if keyword == "last_quarter":
            quarter = (today.month - 1) // 3 + 1
            year, quarter = (today.year - 1, 4) if quarter == 1 else (today.year, quarter - 1)
            start, end = _quarter_bounds(year, quarter)
            return PeriodResult(PeriodOutcome.RESOLVED, start, end, f"Q{quarter} {year}")
        if keyword == "this_year":
            return PeriodResult(PeriodOutcome.RESOLVED, date(today.year, 1, 1), date(today.year, 12, 31), str(today.year))
        if keyword == "last_year":
            year = today.year - 1
            return PeriodResult(PeriodOutcome.RESOLVED, date(year, 1, 1), date(year, 12, 31), str(year))

    if _AMBIGUOUS_HINT_RE.search(query):
        return PeriodResult(PeriodOutcome.AMBIGUOUS)

    return PeriodResult(PeriodOutcome.NONE)
