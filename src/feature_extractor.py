#!/usr/bin/env python3
"""Feature extraction: questions.jsonl + dictionary -> Features for shape_router.py.

One question in, one shape_router.Features out. Deterministic, standard library only,
no model calls. It reads only the question, as_of_date, the concept dictionary and the
cue file. It never reads business data, so it cannot decide answerability or bind product
and customer names; leftover words are recorded as free text and block routes that need
"no entity".

CLI:
    python feature_extractor.py --questions questions.jsonl --dictionary dict_ns.json --cues cues.json --rules rules_ns.json --out features.jsonl

Library:
    lex = Lexicon("dict_ns.json", "cues.json", rules_path="rules_ns.json")
    rec = extract_record(question_obj, lex)        # {"features": Features, "spans": [...], ...}
    out = router.process(qid, business_id, as_of_date, rec["features"])
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import shape_router as sr

MULTIPLIERS = {"trieu": 1_000_000, "tr": 1_000_000, "nghin": 1_000, "ngan": 1_000, "k": 1_000}
SUPPORTED_DIRECTIONS = {"desc"}   # templates hardcode sort direction "desc"; asc abstains until a slot exists
SLOT_TOKENS = {"x", "y", "n", "m"}
FUZZY_KINDS = {"concept", "comparator", "answer_cue", "superlative", "direction", "percent_cue", "change_word"}
UNRESOLVED_TIME = {"range", "year"}   # bare 'nam'/'quy' or 'tu X den Y' stay unresolved; explicit forms are parsed below
DEFAULT_METRICS = {"revenue", "order_id", "quantity_base", "debt_balance", "stock_end_qty", "expiry_date"}
ENTITY_GROUPS = {"product_name", "customer_name", "customer_group"}
CUSTOMER_WORDS = {"khach", "customer"}
MONTH_YEAR_RE = r"(\d{1,2})/(20\d{2})"       # '6/2025' is one token after folding
# Debt-movement and age-bucket questions: a balance route would answer a different quantity (C1, step 8).
DEBT_MOVEMENT_PHRASES = ("ghi no", "no them", "nhom tuoi", "tuoi no")
# Single-token aliases that are generic nouns; they claim a span only in their grouping context (C4, C5).
WEAK_SINGLE = {"hang", "mon", "ngay"}
GROUP_PREFIX = {"theo", "moi", "tung"}
# A bare number is a ranking count only when a counted noun follows it ('3 mat hang ...'), never a date part.
LEADING_COUNT_NOUNS = {"mat", "hang", "mon", "san", "sp", "khach", "don", "nhom", "loai"}
TIME_PREFIX = {"thang", "nam", "ngay", "quy", "tu", "den", "t"}
# Per-customer order-count thresholds ('khach mua tu 4 don tro len') need a template that does not exist.
ORDER_COUNT_THRESHOLD_RE = re.compile(r" (?:tu |it nhat |khong duoi )\d+ don | \d+ don tro len ")
# Tokens that are neither entity text nor route-relevant content.
NOISE_RE = re.compile(r".*[\d/%.].*")


# ---------------------------------------------------------------- folding

def fold_tokens(text: str) -> list[str]:
    s = text.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    s = re.sub(r"[^a-z0-9/%.]+", " ", s)
    return [t for t in (x.strip(".") for x in s.split()) if t]


def _edit1(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    i = j = diff = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
            continue
        diff += 1
        if diff > 1:
            return False
        if len(a) == len(b):
            i += 1; j += 1
        elif len(a) > len(b):
            i += 1
        else:
            j += 1
    return diff + (len(a) - i) + (len(b) - j) == 1


# ---------------------------------------------------------------- lexicon

class Lexicon:
    def __init__(self, dictionary_path, cues_path, rules_path=None, allow_proposed=True):
        entries = list(json.loads(Path(dictionary_path).read_text(encoding="utf-8"))["entries"])
        cues = json.loads(Path(cues_path).read_text(encoding="utf-8"))
        entries += cues["entries"]
        self.time_aliases = {tuple(fold_tokens(k)): v for k, v in cues["time_aliases"].items()}
        self.stopwords = set(cues["stopwords"])
        self.filler = set(cues.get("filler", []))
        # Words whose one-edit neighbour is a cue word in train questions (fuzzy collisions, e.g. gian->giam, nhau->nhat).
        self.fuzzy_block = set(cues.get("fuzzy_block", []))
        self.index: dict[tuple, list[dict]] = {}
        for e in entries:
            if e.get("status") == "proposed" and not allow_proposed:
                continue
            for alias in e["aliases"]:
                key = tuple(fold_tokens(alias))
                if not key or SLOT_TOKENS & set(key):
                    continue
                self.index.setdefault(key, []).append(
                    {"canonical": e["canonical"], "kind": e["kind"], "constant": e.get("constant"),
                     "status": e.get("status", "confirmed"), "alias": " ".join(key)})
        # One folded alias must name one canonical id, across the dictionary and the cue file (C7).
        for key, hits in self.index.items():
            owners = {h["canonical"] for h in hits}
            if len(owners) > 1:
                raise ValueError(f"alias {' '.join(key)!r} maps to several canonical ids: {sorted(owners)}")
        for key in cues["time_aliases"]:
            folded = tuple(fold_tokens(key))
            if folded in self.index:
                raise ValueError(f"time alias {key!r} is also a dictionary alias; keep one source")
        self.max_len = max(len(k) for k in self.index)
        self.single = [k[0] for k in self.index if len(k) == 1 and len(k[0]) >= 4]
        self.time_kind_map = {e["canonical"].split(":")[-1]: e["canonical"]
                              for e in entries if e["kind"] == "time_kind"}
        self.group_ids = {e["canonical"] for e in entries if e["kind"] == "grouping"}
        if rules_path:
            rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
            self.metric_ids = {m for ms in rules["shape_metrics"].values() for m in ms}
        else:
            self.metric_ids = set(DEFAULT_METRICS)


# ---------------------------------------------------------------- time helpers

def _month_period(lex, m, y, as_of, to_as_of=False, trace=None):
    if not 1 <= m <= 12:
        return None, f"invalid month {m}"
    if y is None:
        y = as_of.year if m <= as_of.month else as_of.year - 1     # §2: this year unless the month is after today
        if trace is not None:
            trace.append(f"year_rule:{m}->{y}")
    start = date(y, m, 1)
    end = as_of if to_as_of else date(y, m, calendar.monthrange(y, m)[1])
    kind = lex.time_kind_map.get("month")
    if kind is None:
        return None, "time kind 'month' not in dictionary"
    return sr.Period(kind=kind, month=m, year=y, date=None, start=start.isoformat(), end=end.isoformat()), None


def _day_period(lex, d: date):
    kind = lex.time_kind_map.get("day")
    if kind is None:
        return None, "time kind 'day' not in dictionary"
    iso = d.isoformat()
    return sr.Period(kind=kind, month=None, year=None, date=iso, start=iso, end=iso), None


def _parse_amount(tok: str, nxt: Optional[str]):
    """Return (value, tokens_used) or (None, 0). Dots are decimals before a multiplier, thousands otherwise."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(tr|k|trieu|nghin|ngan)", tok)
    if m:
        return int(round(float(m[1]) * MULTIPLIERS[m[2]])), 1
    if re.fullmatch(r"\d+(?:\.\d+)?", tok) and nxt in MULTIPLIERS:
        return int(round(float(tok) * MULTIPLIERS[nxt])), 2
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", tok):
        return int(tok.replace(".", "")), 1
    if re.fullmatch(r"\d{4,}", tok):
        return int(tok), 1
    return None, 0


# ---------------------------------------------------------------- extraction

def extract_record(q: dict, lex: Lexicon) -> dict:
    qid, text = q["id"], q["question"]
    as_of = date.fromisoformat(q["as_of_date"])
    toks = fold_tokens(text)
    n = len(toks)
    claimed = [False] * n
    spans: list[dict] = []
    trace: list[str] = []
    periods: list[tuple[int, sr.Period]] = []
    state = {"k": None, "top": False, "amounts": [], "problem": None}

    def claim(i, j, category, **kw):
        for t in range(i, j):
            claimed[t] = True
        spans.append({"start": i, "end": j, "surface": " ".join(toks[i:j]), "category": category, **kw})

    def fail(reason, status="unsupported"):
        feats = sr.Features(trace=tuple(trace))
        return envelope(status, reason, feats, spans, [])

    def envelope(status, reason, feats, spans_out, free_out):
        return {"question_id": qid, "business_id": q.get("business_id"), "as_of_date": q["as_of_date"],
                "text": text, "status": status, "reason": reason, "features": feats,
                "spans": spans_out, "free_tokens": free_out}

    if not toks:
        return fail("empty question")
    if text.count("?") >= 2:
        return fail("multiple questions")
    joined_text = " " + " ".join(toks) + " "
    for phrase in DEBT_MOVEMENT_PHRASES:
        if f" {phrase} " in joined_text:
            return fail(f"debt movement or age-bucket question ('{phrase}'): no template")
    if ORDER_COUNT_THRESHOLD_RE.search(joined_text):
        return fail("per-customer order-count threshold: no template")

    free = lambda i: not claimed[i]

    # 1. numeric time and ranking patterns
    for i in range(n):
        if not free(i):
            continue
        if re.fullmatch(r"\d+", toks[i]) and toks[i + 1:i + 4] == ["ngay", "gan", "nhat"] and not any(claimed[i:i + 4]):
            days = int(toks[i])
            if days < 1:
                return fail("last N days needs N >= 1")
            kind = lex.time_kind_map.get("last_n_days")
            if kind is None:
                return fail("time kind 'last_n_days' not in dictionary")
            start = as_of - timedelta(days=days - 1)
            periods.append((i, sr.Period(kind, None, None, None, start.isoformat(), as_of.isoformat())))
            claim(i, i + 4, "time", term_id="last_n_days", value=days)
            continue
        if toks[i] == "top":
            state["top"] = True
            if i + 1 < n and re.fullmatch(r"\d+", toks[i + 1]) and free(i + 1):
                state["k"] = int(toks[i + 1])
                claim(i, i + 2, "ranking", term_id="top_k", value=state["k"])
            else:
                claim(i, i + 1, "ranking", term_id="top")
            continue

    for i in range(n):
        if not free(i):
            continue
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", toks[i])
        if m:
            d_, mo, y = int(m[1]), int(m[2]), int(m[3]) if m[3] else None
            try:
                dd = date(y if y else as_of.year, mo, d_)
                if y is None and dd > as_of:
                    dd = date(as_of.year - 1, mo, d_)
            except ValueError:
                return fail(f"invalid date {toks[i]}")
            back = toks[max(0, i - 3):i]
            kind_name = "as_of" if ("het" in back or "den" in back or "tinh" in back) else "day"
            if kind_name == "as_of":
                kind = lex.time_kind_map.get("as_of")
                if kind is None:
                    return fail("time kind 'as_of' not in dictionary")
                period = sr.Period(kind, None, None, dd.isoformat(), None, dd.isoformat())
            else:
                period, err = _day_period(lex, dd)
                if err:
                    return fail(err)
            periods.append((i, period))
            claim(i, i + 1, "date", term_id=kind_name, value=dd.isoformat())
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
            period, err = _month_period(lex, month, year, as_of, trace=trace)
            if err:
                return fail(err)
            periods.append((i, period))
            claim(i, i + width, "time", term_id="month", value=[month, period.year])

    # 1a. leading count before a ranked noun: '3 mat hang ban chay nhat' -> k=3 (was lost, default k=1)
    if state["k"] is None and "nhat" in toks:
        for i in range(toks.index("nhat")):
            if not free(i) or not re.fullmatch(r"\d{1,2}", toks[i]) or not 1 <= int(toks[i]) <= 50:
                continue
            if i + 1 < n and toks[i + 1] in LEADING_COUNT_NOUNS and (i == 0 or toks[i - 1] not in TIME_PREFIX):
                state["k"] = int(toks[i])
                claim(i, i + 1, "ranking", term_id="top_k", value=state["k"])
                trace.append(f"leading count k={state['k']}")
                break

    # 1b. month-year slash form ('6/2025', 'thang 6/2025'), quarter ('quy 2/2026', 'quy 2 nam 2026'), year ('nam 2025')
    for i in range(n):
        if not free(i):
            continue
        j0 = i + 1 if toks[i] == "thang" and i + 1 < n and free(i + 1) and re.fullmatch(MONTH_YEAR_RE, toks[i + 1]) else i
        mym = re.fullmatch(MONTH_YEAR_RE, toks[j0]) if j0 < n else None
        if mym and free(j0):
            month, year = int(mym[1]), int(mym[2])
            period, err = _month_period(lex, month, year, as_of, trace=trace)
            if err:
                return fail(err)
            periods.append((i, period))
            claim(i, j0 + 1, "time", term_id="month", value=[month, year])
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
            start_month = 3 * (qtr - 1) + 1
            if year is None:
                year = as_of.year if start_month <= as_of.month else as_of.year - 1   # same rule as months (§2)
                trace.append(f"year_rule:quarter {qtr}->{year}")
            kind = lex.time_kind_map.get("range")
            if kind is None:
                return fail("time kind 'range' not in dictionary")
            s_date = date(year, start_month, 1)
            e_date = date(year, start_month + 2, calendar.monthrange(year, start_month + 2)[1])
            periods.append((i, sr.Period(kind, None, year, None, s_date.isoformat(), e_date.isoformat(),
                                         anchor=f"Q{qtr}")))
            claim(i, i + width, "time", term_id="quarter", value=[qtr, year])
            continue
        if toks[i] == "nam" and i + 1 < n and free(i + 1) and re.fullmatch(r"20\d{2}", toks[i + 1]):
            year = int(toks[i + 1])
            kind = lex.time_kind_map.get("year")
            if kind is None:
                return fail("time kind 'year' not in dictionary")
            periods.append((i, sr.Period(kind, None, year, None, f"{year}-01-01", f"{year}-12-31")))
            claim(i, i + 2, "time", term_id="year", value=year)
            continue

    # 2. relative time aliases (cues.json), longest first
    for length in (3, 2, 1):
        for i in range(n - length + 1):
            key = tuple(toks[i:i + length])
            spec = lex.time_aliases.get(key)
            if not spec or any(claimed[i:i + length]):
                continue
            kind_name = spec["kind"]
            if kind_name == "day":
                period, err = _day_period(lex, as_of + timedelta(days=spec["offset_days"]))
            elif kind_name == "week":
                monday = as_of - timedelta(days=as_of.weekday())
                if spec["which"] == "current":
                    s, e = monday, as_of
                else:
                    s, e = monday - timedelta(days=7), monday - timedelta(days=1)
                kind = lex.time_kind_map.get("week")
                period, err = (sr.Period(kind, None, None, None, s.isoformat(), e.isoformat(),
                                        anchor=spec["which"]), None) if kind \
                    else (None, "time kind 'week' not in dictionary")
            else:  # month
                if spec["which"] == "current":
                    period, err = _month_period(lex, as_of.month, as_of.year, as_of, to_as_of=True)
                    trace.append("month_to_date->time:month,end=as_of_date")
                else:
                    pm, py = (as_of.month - 1, as_of.year) if as_of.month > 1 else (12, as_of.year - 1)
                    period, err = _month_period(lex, pm, py, as_of)
            if err:
                return fail(err)
            periods.append((i, period))
            claim(i, i + length, "time", term_id=" ".join(key))

    # 3. amounts
    for i in range(n):
        if free(i):
            val, used = _parse_amount(toks[i], toks[i + 1] if i + 1 < n else None)
            if val is not None and not any(claimed[i:i + used]):
                state["amounts"].append(val)
                claim(i, i + used, "number", term_id="amount", value=val)

    # 4. dictionary and cue phrases, longest first
    proposed_used: list[str] = []
    for length in range(lex.max_len, 0, -1):
        for i in range(n - length + 1):
            key = tuple(toks[i:i + length])
            if key in lex.index and not any(claimed[i:i + length]):
                entries = lex.index[key]
                category = entries[0]["kind"]
                if length == 1 and key[0] in WEAK_SINGLE:
                    prev = toks[i - 1] if i > 0 else ""
                    if not (category == "grouping" and prev in GROUP_PREFIX):
                        continue   # generic noun outside a grouping context: leave free
                claim(i, i + length, category, term_id=entries[0]["canonical"], entries=entries)
                if any(e["status"] == "proposed" for e in entries):
                    proposed_used.append(" ".join(key))

    # 5. fuzzy fallback: edit distance 1, tokens of 4+ chars, one unambiguous candidate, never for entities
    for i in range(n):
        tok = toks[i]
        if claimed[i] or len(tok) < 4 or tok in lex.stopwords or tok.isdigit() or tok in lex.fuzzy_block:
            continue
        cands = {}
        for alias in lex.single:
            if _edit1(tok, alias):
                for e in lex.index[(alias,)]:
                    if e["kind"] in FUZZY_KINDS:
                        cands[(e["kind"], e["canonical"])] = (alias, e)
        if len(cands) == 1:
            alias, e = next(iter(cands.values()))
            claim(i, i + 1, e["kind"], term_id=e["canonical"], entries=[e], fuzzy=True)
            trace.append(f"fuzzy:{tok}->{alias}")
            proposed_used.append(alias) if e["status"] == "proposed" else None

    spans.sort(key=lambda s: s["start"])
    ents = lambda s: s.get("entries", [])

    # 6. unresolved time kinds
    for s in spans:
        for e in ents(s):
            if e["kind"] == "time_kind" and e["canonical"].split(":")[-1] in UNRESOLVED_TIME:
                return fail(f"time expression not resolved: {s['surface']}")

    # 7. metric
    metric_cands = []
    for s in spans:
        for e in ents(s):
            if e["kind"] in ("concept", "constant_term") and e["canonical"] in lex.metric_ids \
                    and e["canonical"] not in metric_cands:
                metric_cands.append(e["canonical"])
    if len(metric_cands) > 1:
        return fail("multiple metric terms: " + ", ".join(metric_cands), status="ambiguous")
    metric = metric_cands[0] if metric_cands else None

    # 8. ranking, direction
    superlative = state["top"]
    sup_term = "top" if state["top"] else None
    rank_pos = [s["start"] for s in spans
                if s["category"] in ("ranking", "superlative") or s["surface"].endswith(" nhat")]
    directions = []
    for s in spans:
        for e in ents(s):
            if e["kind"] == "superlative" or (e["kind"] == "concept" and s["surface"].endswith(" nhat")):
                superlative, sup_term = True, sup_term or "nhat"
                if e["kind"] == "concept":
                    directions.append("desc")
            if e["kind"] == "direction":
                # 'nhieu', 'it', 'cao' are ordinary words without a ranking term beside them (C2)
                if any(abs(s["start"] - p) <= 2 for p in rank_pos):
                    directions.append(e["canonical"])
                else:
                    trace.append(f"direction '{s['surface']}' ignored: no ranking term within 2 tokens")
    has_amount = bool(state["amounts"])
    comparator = None
    for s in spans:
        for e in ents(s):
            if e["kind"] == "comparator":
                if e["alias"] == "it nhat" and not has_amount:
                    superlative, sup_term = True, sup_term or "nhat"
                    directions.append("asc")
                    trace.append("it nhat->ascending superlative (no amount)")
                elif has_amount:
                    comparator = comparator or e["canonical"]
                else:
                    trace.append(f"comparator '{s['surface']}' ignored: no amount")

    if metric is None and "ban" in toks and superlative:
        metric = "quantity_base" if "quantity_base" in lex.metric_ids else None
        trace.append("inferred:quantity_base from 'ban' + superlative (rulebook §3)")
    cues = {e["canonical"] for s in spans for e in ents(s) if e["kind"] == "answer_cue"}
    unit = next((e["canonical"] and s["surface"] for s in spans for e in ents(s) if e["kind"] == "unit_word"), None)
    if metric is None and unit and "bao_nhieu" in cues:
        metric = "quantity_base"
        trace.append("inferred:quantity_base from unit word + bao nhieu")
    if metric is None:
        return fail("no metric term")

    direction = "desc"
    if directions:
        if len(set(directions)) > 1:
            return fail("conflicting ranking directions", status="ambiguous")
        direction = directions[0]
    k = state["k"]
    if superlative and k is None and sup_term == "nhat":
        k = 1
        trace.append("default:k=1 from 'nhat'")
    ranking = {"criterion": metric, "direction": direction, "k": k} if superlative else None
    if ranking and direction not in SUPPORTED_DIRECTIONS:
        return fail(f"ranking direction '{direction}' not supported by templates yet")

    # 9. constants (stock, debt), comparator, threshold
    stock_cmp = stock_val = None
    filter_concepts: list[str] = []
    const_cmp = const_val = None
    for s in spans:
        for e in ents(s):
            if e["kind"] == "constant_term" and e["constant"]:
                if e["canonical"] == "stock_end_qty":
                    stock_cmp, stock_val = e["constant"]["comparator"], e["constant"]["value"]
                    filter_concepts.append("stock_end_qty")
                else:
                    const_cmp, const_val = e["constant"]["comparator"], e["constant"]["value"]
    # The threshold comes only from a question amount. 'con no' (balance gt 0) is a filter, never a threshold (B4, C3).
    threshold = state["amounts"][0] if comparator and state["amounts"] else None
    cues_early = {e["canonical"] for s in spans for e in ents(s) if e["kind"] == "answer_cue"}
    if const_cmp:
        if "bao_nhieu" in cues_early:
            trace.append(f"constant:{const_cmp} {const_val} implied: question asks for an amount")
        else:
            filter_concepts.append("debt_balance")
            trace.append(f"constant:{const_cmp} {const_val} recorded as debt filter")
    status_filter = next((e["canonical"] for s in spans for e in ents(s) if e["kind"] == "status"), None)
    if status_filter:
        filter_concepts.append("order_status")
        trace.append(f"status:{status_filter}")
    if len(set(state["amounts"])) > 1 and comparator:
        return fail("multiple amounts with one comparator", status="ambiguous")

    # 10. grouping
    groups = []
    for s in spans:
        for e in ents(s):
            if e["kind"] == "grouping" and e["canonical"] in lex.group_ids:
                if superlative or s["surface"].split()[0] in ("theo", "moi", "tung"):
                    if e["canonical"] not in groups:
                        groups.append(e["canonical"])
    if superlative and "ngay nao" in " ".join(toks) and "group:day" in lex.group_ids and "group:day" not in groups:
        groups.append("group:day")
        trace.append("context:'ngay nao'->group:day")
    if len(groups) > 1:
        return fail("multiple groupings: " + ", ".join(groups), status="ambiguous")
    group = groups[0] if groups else None

    # 11. change
    joined = " ".join(toks)
    has_pct = any(e["kind"] == "percent_cue" for s in spans for e in ents(s))
    has_change = any(e["kind"] == "change_word" for s in spans for e in ents(s))
    periods.sort(key=lambda p: p[0])
    plist = [p for _, p in periods]
    change = None
    if has_change and has_pct:
        change = "pct"
        if len(plist) == 2:
            a, b = plist[0], plist[1]
            prev = (a.year, a.month - 1) if a.month and a.month > 1 else ((a.year - 1, 12) if a.month else None)
            if prev == (b.year, b.month):
                plist = [a]
                trace.append("pct: comparison period is the previous month, kept implicit")
            else:
                return fail("pct comparison against a non-adjacent period is not supported")
    elif has_change and len(plist) == 2:
        change = "diff"

    # 12. debt default period (§4: balance as of the question date)
    if metric == "debt_balance" and not plist:
        kind = lex.time_kind_map.get("as_of")
        if kind is None:
            return fail("time kind 'as_of' not in dictionary")
        plist = [sr.Period(kind, None, None, as_of.isoformat(), None, as_of.isoformat())]
        trace.append("default:as_of_date for debt balance")

    # 13. answer type (a router discriminator, derived from cues)
    ans = None
    if ranking and k and k > 1:
        ans = "list"
    elif ranking and group == "group:day":
        ans = "date"
    elif ranking and "nao" in cues:
        ans = "string"
    elif ranking and "bao_nhieu" in cues:
        ans = "number"
    elif change == "pct":
        ans = "percent"
    elif change == "diff":
        ans = "number"
    elif has_pct:
        ans = "percent"   # 'tỉ lệ', 'phần trăm', '%' without a change word (train gold: percent)
        trace.append("default:answer_type=percent from percent cue")
    elif "liet_ke" in cues or (metric == "debt_balance" and comparator and threshold is not None):
        ans = "list"
    elif "bao_nhieu" in cues or (metric == "debt_balance" and not ranking):
        ans = "number"
    elif not ranking and metric in ("revenue", "order_id", "quantity_base") and "nao" not in cues:
        ans = "number"
        trace.append("default:answer_type=number")

    # 14. leftover words that may name an entity. Status, unit and time words are already claimed above;
    # filler words and tokens with digits or symbols (dates, amounts, '10ml', '%') are not entity text (G1).
    leftover = [t for i, t in enumerate(toks)
                if not claimed[i] and t not in lex.stopwords and t not in lex.filler
                and not t.isdigit() and not NOISE_RE.fullmatch(t)]
    entity_concepts: tuple = ()
    if leftover:
        concept = "product_name"
        for s in spans:
            if s["category"] == "grouping" and any(e["canonical"] == "customer_name" for e in ents(s)):
                concept = "customer_name"
        entity_concepts = (concept,)
        trace.append("free:" + " ".join(leftover))
    for alias in sorted(set(proposed_used)):
        trace.append(f"proposed:{alias}")

    first = plist[0] if plist else None
    feats = sr.Features(
        metric=metric, answer_type=ans, group=group, ranking=ranking, k=k,
        superlative_term=sup_term, comparator=comparator, threshold=threshold, periods=tuple(plist),
        time_kind=first.kind if first else None,
        date=first.date if first else None, start=first.start if first else None, end=first.end if first else None,
        change=change, unit=unit, stock_comparator=stock_cmp, stock_value=stock_val,
        entity_concepts=entity_concepts, filter_concepts=tuple(sorted(set(filter_concepts))),
        status_filter=status_filter, trace=tuple(trace))
    return envelope("ok", None, feats, spans, leftover)


# ---------------------------------------------------------------- serialization

def features_to_json(f: sr.Features) -> dict:
    return asdict(f)


def features_from_json(d: dict) -> sr.Features:
    """Inverse of features_to_json. Only Features fields are accepted; status and reason live in the envelope."""
    return sr.features_from_dict(d)


def span_json(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k != "entries"}
    out["terms"] = [{"canonical": e["canonical"], "kind": e["kind"], "status": e["status"]} for e in s.get("entries", [])]
    return out


def iter_features(path):
    """Yield (question_id, business_id, as_of_date, status, reason, Features) from a features.jsonl file."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            yield (rec["question_id"], rec["business_id"], rec["as_of_date"], rec["status"], rec["reason"],
                   features_from_json(rec["features"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--dictionary", required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--rules", help="shape_rules json; its shape_metrics define which concepts count as metrics")
    ap.add_argument("--out", required=True)
    ap.add_argument("--confirmed-only", action="store_true", help="ignore entries marked proposed")
    a = ap.parse_args()
    lex = Lexicon(a.dictionary, a.cues, a.rules, allow_proposed=not a.confirmed_only)
    status, reasons, free_tokens, n = Counter(), Counter(), Counter(), 0
    lines = []
    for line in Path(a.questions).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        q = json.loads(line)
        try:
            rec = extract_record(q, lex)
        except Exception as exc:  # never drop a question
            rec = {"question_id": q["id"], "business_id": q.get("business_id"), "as_of_date": q["as_of_date"],
                   "text": q["question"], "status": "unsupported",
                   "reason": f"extractor error: {type(exc).__name__}: {exc}",
                   "features": sr.Features(), "spans": [], "free_tokens": []}
        status[rec["status"]] += 1
        if rec["reason"]:
            reasons[rec["reason"]] += 1
        free_tokens.update(rec["free_tokens"])
        n += 1
        lines.append(json.dumps({
            "question_id": rec["question_id"], "business_id": rec["business_id"],
            "as_of_date": rec["as_of_date"], "text": rec["text"],
            "status": rec["status"], "reason": rec["reason"],
            "features": features_to_json(rec["features"]), "spans": [span_json(s) for s in rec["spans"]],
            "free_tokens": rec["free_tokens"]}, ensure_ascii=False))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"questions: {n}  status: {dict(status)}", file=sys.stderr)
    for reason, c in reasons.most_common(10):
        print(f"  {c:5d}  {reason}", file=sys.stderr)
    print("most frequent leftover words (tune stopwords/dictionary from these):", file=sys.stderr)
    for tok, c in free_tokens.most_common(15):
        print(f"  {c:5d}  {tok}", file=sys.stderr)


if __name__ == "__main__":
    main()
