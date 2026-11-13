#!/usr/bin/env python3
"""Time-expression resolution for Query Spec v1.

The resolver works on tokens that keep both a folded text (for matching) and the
original accented text (for the `raw` field). It reads `as_of_date` and the
dictionary/cue files only; it never reads business data.

The caller supplies `claim(i, j, category, **kw)` and `free(i)` so this module can
share the span table with the parser without owning it.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Callable, Optional

from query_spec import Period

MONTH_YEAR_RE = r"(\d{1,2})/(20\d{2})"


def surface(tokens: list[dict], i: int, j: int) -> str:
    return " ".join(t["raw"] for t in tokens[i:j])


def _kind(lex, name: str) -> Optional[str]:
    return lex.time_kind_map.get(name)


def month_period(lex, month: int, year: Optional[int], as_of: date,
                 to_as_of: bool = False, trace: Optional[list[str]] = None,
                 raw: Optional[str] = None, kind_name: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    if not 1 <= month <= 12:
        return None, f"invalid month {month}"
    if year is None:
        year = as_of.year if month <= as_of.month else as_of.year - 1
        if trace is not None:
            trace.append(f"year_rule:{month}->{year}")
    start = date(year, month, 1)
    end = as_of if to_as_of else date(year, month, calendar.monthrange(year, month)[1])
    canonical_kind = _kind(lex, kind_name or "month")
    if canonical_kind is None:
        return None, f"time kind '{kind_name or 'month'}' not in dictionary"
    return Period(kind=canonical_kind, month=month, year=year, date=None,
                  start=start.isoformat(), end=end.isoformat(), raw=raw), None


def day_period(lex, day: date, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    canonical_kind = _kind(lex, "day")
    if canonical_kind is None:
        return None, "time kind 'day' not in dictionary"
    iso = day.isoformat()
    return Period(kind=canonical_kind, month=None, year=None, date=iso,
                  start=iso, end=iso, raw=raw), None


def year_period(lex, year: int, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    canonical_kind = _kind(lex, "year")
    if canonical_kind is None:
        return None, "time kind 'year' not in dictionary"
    return Period(kind=canonical_kind, month=None, year=year, date=None,
                  start=f"{year}-01-01", end=f"{year}-12-31", raw=raw), None


def quarter_period(lex, quarter: int, year: Optional[int], as_of: date,
                   trace: Optional[list[str]] = None, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    if not 1 <= quarter <= 4:
        return None, f"invalid quarter {quarter}"
    start_month = 3 * (quarter - 1) + 1
    if year is None:
        year = as_of.year if start_month <= as_of.month else as_of.year - 1
        if trace is not None:
            trace.append(f"year_rule:quarter {quarter}->{year}")
    canonical_kind = _kind(lex, "range")
    if canonical_kind is None:
        return None, "time kind 'range' not in dictionary"
    s_date = date(year, start_month, 1)
    e_date = date(year, start_month + 2, calendar.monthrange(year, start_month + 2)[1])
    return Period(kind=canonical_kind, month=None, year=year, date=None,
                  start=s_date.isoformat(), end=e_date.isoformat(),
                  anchor=f"Q{quarter}", raw=raw), None


def week_period(lex, as_of: date, which: str, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    canonical_kind = _kind(lex, "week")
    if canonical_kind is None:
        return None, "time kind 'week' not in dictionary"
    monday = as_of - timedelta(days=as_of.weekday())
    if which == "current":
        s, e = monday, as_of
    else:
        s, e = monday - timedelta(days=7), monday - timedelta(days=1)
    return Period(kind=canonical_kind, month=None, year=None, date=None,
                  start=s.isoformat(), end=e.isoformat(), anchor=which, raw=raw), None


def year_to_date_period(lex, as_of: date, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    canonical_kind = _kind(lex, "year_to_date")
    if canonical_kind is None:
        return None, "time kind 'year_to_date' not in dictionary"
    return Period(kind=canonical_kind, month=None, year=as_of.year, date=None,
                  start=f"{as_of.year}-01-01", end=as_of.isoformat(), raw=raw), None


def tet_period(lex, raw: Optional[str] = None) -> tuple[Optional[Period], Optional[str]]:
    canonical_kind = _kind(lex, "tet")
    if canonical_kind is None:
        return None, "time kind 'tet' not in dictionary"
    return Period(kind=canonical_kind, raw=raw), None


def resolve_periods(tokens: list[dict], as_of: date, lex,
                    claim: Callable, free: Callable, trace: list[str]) -> tuple[list[tuple[int, Period]], Optional[str]]:
    """Claim all recognized time expressions and return (periods, error)."""
    toks = [t["text"] for t in tokens]
    n = len(tokens)
    periods: list[tuple[int, Period]] = []

    def add(i: int, j: int, period: Period, term_id: str, value=None):
        periods.append((i, period))
        claim(i, j, "time", term_id=term_id, value=value)

    # 1. Numeric relative windows and explicit dates.
    for i in range(n):
        if not free(i):
            continue
        if re.fullmatch(r"\d+", toks[i]) and toks[i + 1:i + 4] == ["ngay", "gan", "nhat"] \
                and all(free(x) for x in range(i, min(i + 4, n))):
            days = int(toks[i])
            if days < 1:
                return periods, "last N days needs N >= 1"
            kind = _kind(lex, "last_n_days")
            if kind is None:
                return periods, "time kind 'last_n_days' not in dictionary"
            start = as_of - timedelta(days=days - 1)
            add(i, i + 4, Period(kind, None, None, None, start.isoformat(), as_of.isoformat(),
                                 raw=surface(tokens, i, i + 4)), "last_n_days", days)
            continue

        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", toks[i])
        if m:
            d_, mo, y = int(m[1]), int(m[2]), int(m[3]) if m[3] else None
            try:
                dd = date(y if y else as_of.year, mo, d_)
                if y is None and dd > as_of:
                    dd = date(as_of.year - 1, mo, d_)
            except ValueError:
                return periods, f"invalid date {toks[i]}"
            back = toks[max(0, i - 3):i]
            kind_name = "as_of" if ("het" in back or "den" in back or "tinh" in back) else "day"
            raw = surface(tokens, i, i + 1)
            if kind_name == "as_of":
                canonical_kind = _kind(lex, "as_of")
                if canonical_kind is None:
                    return periods, "time kind 'as_of' not in dictionary"
                period = Period(canonical_kind, None, None, dd.isoformat(), None, dd.isoformat(), raw=raw)
            else:
                period, err = day_period(lex, dd, raw=raw)
                if err:
                    return periods, err
            add(i, i + 1, period, kind_name, dd.isoformat())
            continue

        mm = re.fullmatch(r"t(\d{1,2})", toks[i])
        width = 1
        if not mm and toks[i] == "thang" and i + 1 < n and re.fullmatch(r"\d{1,2}", toks[i + 1]) and free(i + 1):
            mm, width = re.fullmatch(r"(\d{1,2})", toks[i + 1]), 2
        if mm:
            month, year = int(mm[1]), None
            j = i + width
            if j < n and re.fullmatch(r"20\d{2}", toks[j]) and free(j):
                year, width = int(toks[j]), width + 1
            elif j + 1 < n and toks[j] == "nam" and re.fullmatch(r"20\d{2}", toks[j + 1]) and free(j) and free(j + 1):
                year, width = int(toks[j + 1]), width + 2
            period, err = month_period(lex, month, year, as_of, trace=trace,
                                       raw=surface(tokens, i, i + width))
            if err:
                return periods, err
            add(i, i + width, period, "month", [month, period.year])

    # 2. Slash forms, quarters and explicit years.
    for i in range(n):
        if not free(i):
            continue
        j0 = i + 1 if toks[i] == "thang" and i + 1 < n and free(i + 1) and re.fullmatch(MONTH_YEAR_RE, toks[i + 1]) else i
        mym = re.fullmatch(MONTH_YEAR_RE, toks[j0]) if j0 < n else None
        if mym and free(j0):
            month, year = int(mym[1]), int(mym[2])
            period, err = month_period(lex, month, year, as_of, trace=trace,
                                       raw=surface(tokens, i, j0 + 1))
            if err:
                return periods, err
            add(i, j0 + 1, period, "month", [month, year])
            continue

        if toks[i] == "quy" and i + 1 < n and free(i + 1):
            qm = re.fullmatch(r"([1-4])/(20\d{2})", toks[i + 1])
            year, width = None, 2
            if qm:
                qtr, year = int(qm[1]), int(qm[2])
            elif re.fullmatch(r"[1-4]", toks[i + 1]):
                qtr = int(toks[i + 1])
                if i + 3 < n and toks[i + 2] == "nam" and re.fullmatch(r"20\d{2}", toks[i + 3]) \
                        and free(i + 2) and free(i + 3):
                    year, width = int(toks[i + 3]), 4
            else:
                continue
            period, err = quarter_period(lex, qtr, year, as_of, trace=trace,
                                         raw=surface(tokens, i, i + width))
            if err:
                return periods, err
            add(i, i + width, period, "quarter", [qtr, period.year])
            continue

        if toks[i] == "nam" and i + 1 < n and free(i + 1) and re.fullmatch(r"20\d{2}", toks[i + 1]):
            year = int(toks[i + 1])
            period, err = year_period(lex, year, raw=surface(tokens, i, i + 2))
            if err:
                return periods, err
            add(i, i + 2, period, "year", year)
            continue

    # 2b. "từ đầu tháng này [đến hết hôm nay]" is month-to-date, not a day.
    for i in range(n):
        if toks[i:i + 3] != ["tu", "dau", "thang"] or not all(free(x) for x in range(i, min(i + 4, n))) \
                or i + 3 >= n or toks[i + 3] not in ("nay", "den"):
            continue
        j = i + 3
        if j < n and toks[j] == "nay" and free(j):
            j += 1
        if toks[j:j + 2] == ["den", "het"] and all(free(x) for x in range(j, min(j + 4, n))):
            j += 2
        if toks[j:j + 2] == ["hom", "nay"] and all(free(x) for x in range(j, j + 2)):
            j += 2
        period, err = month_period(lex, as_of.month, as_of.year, as_of, to_as_of=True,
                                   trace=trace, raw=surface(tokens, i, j), kind_name="month_to_date")
        if err:
            return periods, err
        add(i, j, period, "month_to_date")
        break

    # 3. Relative aliases, longest first.
    max_len = max((len(k) for k in lex.time_aliases), default=1)
    for length in range(max_len, 0, -1):
        for i in range(n - length + 1):
            key = tuple(toks[i:i + length])
            spec = lex.time_aliases.get(key)
            if not spec or any(not free(x) for x in range(i, i + length)):
                continue
            kind_name = spec["kind"]
            raw = surface(tokens, i, i + length)
            if kind_name == "day":
                period, err = day_period(lex, as_of + timedelta(days=spec["offset_days"]), raw=raw)
            elif kind_name == "week":
                period, err = week_period(lex, as_of, spec.get("which", "current"), raw=raw)
            elif kind_name == "month":
                if spec.get("which") == "current":
                    period, err = month_period(lex, as_of.month, as_of.year, as_of,
                                               to_as_of=spec.get("to_as_of", False), trace=trace,
                                               raw=raw, kind_name="month_to_date" if spec.get("to_as_of") else "month")
                else:
                    pm, py = (as_of.month - 1, as_of.year) if as_of.month > 1 else (12, as_of.year - 1)
                    period, err = month_period(lex, pm, py, as_of, raw=raw)
            elif kind_name == "month_to_date":
                period, err = month_period(lex, as_of.month, as_of.year, as_of, to_as_of=True,
                                           trace=trace, raw=raw, kind_name="month_to_date")
            elif kind_name == "year_to_date":
                period, err = year_to_date_period(lex, as_of, raw=raw)
            elif kind_name == "year":
                year = spec.get("year", as_of.year + spec.get("offset_years", 0))
                period, err = year_period(lex, year, raw=raw)
            elif kind_name == "quarter":
                which = spec.get("which", "current")
                q = (as_of.month - 1) // 3 + 1
                if which == "previous":
                    q -= 1
                    if q == 0:
                        q, y = 4, as_of.year - 1
                    else:
                        y = as_of.year
                else:
                    y = as_of.year
                period, err = quarter_period(lex, q, y, as_of, trace=trace, raw=raw)
            elif kind_name == "tet":
                period, err = tet_period(lex, raw=raw)
            else:
                continue
            if err:
                return periods, err
            add(i, i + length, period, " ".join(key))

    periods.sort(key=lambda p: p[0])
    return periods, None