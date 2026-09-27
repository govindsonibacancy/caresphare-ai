"""app/services/admin_periods.py: pure unit tests, no DB, no LLM - every
period is computed by plain calendar arithmetic against a fixed `now`, so
these tests are deterministic regardless of when they run."""

from datetime import UTC, date, datetime

from app.services.admin_periods import PeriodOutcome, resolve_period

# A fixed Wednesday, well inside a month/quarter/year, so week/month/quarter
# boundaries are unambiguous.
_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def test_no_period_phrase_resolves_to_none():
    result = resolve_period("How many appointments does Cardiology have?", now=_NOW)
    assert result.outcome == PeriodOutcome.NONE
    assert result.start is None
    assert result.end is None


def test_today():
    result = resolve_period("appointments today", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == result.end == date(2026, 9, 16)


def test_yesterday():
    result = resolve_period("appointments yesterday", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == result.end == date(2026, 9, 15)


def test_this_week_is_monday_to_sunday():
    result = resolve_period("appointments this week", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 9, 14)  # Monday
    assert result.end == date(2026, 9, 20)  # Sunday


def test_last_week():
    result = resolve_period("appointments last week", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 9, 7)
    assert result.end == date(2026, 9, 13)


def test_this_month():
    result = resolve_period("Summarize appointment activity for this month.", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 9, 1)
    assert result.end == date(2026, 9, 30)
    assert result.label == "September 2026"


def test_last_month():
    result = resolve_period("appointments last month", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 8, 1)
    assert result.end == date(2026, 8, 31)


def test_last_month_across_a_year_boundary():
    result = resolve_period("appointments last month", now=datetime(2026, 1, 15, tzinfo=UTC))
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2025, 12, 1)
    assert result.end == date(2025, 12, 31)


def test_this_quarter():
    result = resolve_period("appointments this quarter", now=_NOW)  # September -> Q3
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 7, 1)
    assert result.end == date(2026, 9, 30)


def test_last_quarter_across_a_year_boundary():
    result = resolve_period("appointments last quarter", now=datetime(2026, 1, 15, tzinfo=UTC))  # Q1 -> Q4 last year
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2025, 10, 1)
    assert result.end == date(2025, 12, 31)


def test_this_year():
    result = resolve_period("appointments this year", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 1, 1)
    assert result.end == date(2026, 12, 31)


def test_last_year():
    result = resolve_period("appointments last year", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2025, 1, 1)
    assert result.end == date(2025, 12, 31)


def test_unresolvable_relative_phrase_is_ambiguous_not_guessed():
    result = resolve_period("appointments two months ago", now=_NOW)
    assert result.outcome == PeriodOutcome.AMBIGUOUS
    assert result.start is None
    assert result.end is None


def test_month_before_previous_is_ambiguous():
    result = resolve_period("the month before the previous one", now=_NOW)
    assert result.outcome == PeriodOutcome.AMBIGUOUS


def test_when_i_started_is_ambiguous():
    result = resolve_period("appointments from when I started here", now=_NOW)
    assert result.outcome == PeriodOutcome.AMBIGUOUS


def test_phrase_embedded_in_a_longer_sentence_still_resolves():
    result = resolve_period("Show this week's appointment activity, thanks.", now=_NOW)
    assert result.outcome == PeriodOutcome.RESOLVED
    assert result.start == date(2026, 9, 14)
    assert result.end == date(2026, 9, 20)
