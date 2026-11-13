#!/usr/bin/env python3
"""Question Understanding: Vietnamese question -> Query Spec v1.

The parser is deterministic, standard-library only and has no model calls. It reads
only the question, `as_of_date`, `concept_dictionary.json` and `cues.json`. It never
reads business data and it never chooses a template or a route.

The output is a query spec. Empty fields are left empty for Person 4 to skip. Raw
entity text is preserved from the original question; no entity id is resolved here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import date_resolver
from query_spec import (
    STATUSES,
    Comparison,
    Component,
    Entity,
    Filter,
    Having,
    Period,
    Query,
    QueryRecord,
    TimeSpec,
    record_to_dict,
)

MULTIPLIERS = {"trieu": 1_000_000, "tr": 1_000_000, "nghin": 1_000, "ngan": 1_000, "k": 1_000}
SLOT_TOKENS = {"x", "y", "n", "m"}
FUZZY_KINDS = {"concept", "comparator", "answer_cue", "superlative", "direction", "percent_cue", "change_word"}
DEFAULT_METRICS = {"revenue", "order_id", "quantity_base", "debt_balance", "stock_end_qty", "expiry_date"}
CUSTOMER_WORDS = {"khach", "customer"}
PRODUCT_WORDS = {"mat", "hang", "mon", "san", "pham", "sp", "loai"}
WEAK_SINGLE = {"hang", "mon", "ngay"}
GROUP_PREFIX = {"theo", "moi", "tung"}
LEADING_COUNT_NOUNS = {"mat", "hang", "mon", "san", "sp", "khach", "nhom", "loai"}
TIME_PREFIX = {"thang", "nam", "ngay", "quy", "tu", "den", "t"}
TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹĐđ%./]+")


# ---------------------------------------------------------------- tokenization

def fold_token(text: str) -> str:
    s = text.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    s = re.sub(r"[^a-z0-9/%.]+", "", s)
    return s.strip("./")


def tokenize(text: str) -> list[dict]:
    """Return tokens with folded text plus the original accented surface."""
    out: list[dict] = []
    for m in TOKEN_RE.finditer(text):
        raw = m.group(0)
        folded = fold_token(raw)
        if not folded:
            continue
        out.append({"text": folded, "raw": raw, "start": m.start(), "end": m.end()})
    return out


def fold_phrase(text: str) -> tuple:
    return tuple(t["text"] for t in tokenize(text))


def _edit1(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    i = j = diff = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        diff += 1
        if diff > 1:
            return False
        if len(a) == len(b):
            i += 1
            j += 1
        elif len(a) > len(b):
            i += 1
        else:
            j += 1
    return diff + (len(a) - i) + (len(b) - j) == 1


# ---------------------------------------------------------------- lexicon

class Lexicon:
    def __init__(self, dictionary_path, cues_path, rules_path=None, allow_proposed=True):
        self.dictionary_path = Path(dictionary_path)
        self.cues_path = Path(cues_path)
        dictionary = json.loads(self.dictionary_path.read_text(encoding="utf-8"))
        cues = json.loads(self.cues_path.read_text(encoding="utf-8"))
        entries = list(dictionary["entries"]) + list(cues["entries"])
        self.time_aliases = {fold_phrase(k): v for k, v in cues["time_aliases"].items()}
        self.stopwords = set(cues["stopwords"])
        self.filler = set(cues.get("filler", []))
        self.fuzzy_block = set(cues.get("fuzzy_block", []))
        self.index: dict[tuple, list[dict]] = {}
        for e in entries:
            if e.get("status") == "proposed" and not allow_proposed:
                continue
            for alias in e["aliases"]:
                key = fold_phrase(alias)
                if not key or SLOT_TOKENS & set(key):
                    continue
                self.index.setdefault(key, []).append(
                    {"canonical": e["canonical"], "kind": e["kind"], "constant": e.get("constant"),
                     "status": e.get("status", "confirmed"), "alias": " ".join(key)})
        for key, hits in self.index.items():
            owners = {h["canonical"] for h in hits}
            if len(owners) > 1:
                raise ValueError(f"alias {' '.join(key)!r} maps to several canonical ids: {sorted(owners)}")
        for key in cues["time_aliases"]:
            folded = fold_phrase(key)
            if folded in self.index:
                raise ValueError(f"time alias {key!r} is also a dictionary alias; keep one source")
        self.max_len = max(len(k) for k in self.index)
        self.single = [k[0] for k in self.index if len(k) == 1 and len(k[0]) >= 4]
        self.time_kind_map = {e["canonical"].split(":")[-1]: e["canonical"]
                              for e in entries if e["kind"] == "time_kind"}
        self.group_ids = {e["canonical"] for e in entries if e["kind"] == "grouping"}
        self.metric_ids = set(dictionary.get("metric_ids", [])) or set(DEFAULT_METRICS)
        # rules_path is accepted only so the old pipeline/run_pipeline.py does not crash.
        _ = rules_path


# ---------------------------------------------------------------- amount helpers

def parse_amount(tok: str, nxt: Optional[str]):
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


# ---------------------------------------------------------------- parser

def extract_record(q: dict, lex: Lexicon, debug: bool = False) -> dict:
    qid, text = q["id"], q["question"]
    as_of = date.fromisoformat(q["as_of_date"])
    tokens = tokenize(text)
    toks = [t["text"] for t in tokens]
    n = len(tokens)
    claimed = [False] * n
    spans: list[dict] = []
    trace: list[str] = []
    notes: list[str] = []
    periods: list[tuple[int, Period]] = []
    state = {"k": None, "top": False, "amounts": []}

    def surface(i: int, j: int) -> str:
        return " ".join(t["raw"] for t in tokens[i:j])

    def claim(i: int, j: int, category: str, **kw):
        for t in range(i, min(j, n)):
            claimed[t] = True
        spans.append({"start": i, "end": j, "surface": surface(i, j), "raw": surface(i, j),
                      "category": category, **kw})

    def free(i: int) -> bool:
        return 0 <= i < n and not claimed[i]

    def finish(record: QueryRecord) -> dict:
        out = record_to_dict(record)
        if debug:
            out["debug"] = {
                "spans": [span_json(s) for s in spans],
                "free_tokens": [tokens[i]["raw"] for i in range(n) if free(i)],
                "trace": trace,
            }
        return out

    def fail(reason: str, status: str = "unsupported") -> dict:
        return finish(QueryRecord(
            question_id=qid, business_id=q.get("business_id"), as_of_date=q["as_of_date"],
            question=text, status=status, reason=reason, query=Query(notes=notes),
        ))

    if not toks:
        return fail("empty question")
    if text.count("?") >= 2:
        return fail("multiple questions")

    # 1. Time. The resolver claims its spans before amount/entity extraction.
    periods, err = date_resolver.resolve_periods(tokens, as_of, lex, claim, free, trace)
    if err:
        return fail(err)

    # 2. Ranking words and leading counts. These are not time.
    for i in range(n):
        if not free(i):
            continue
        if toks[i] == "top":
            state["top"] = True
            if i + 1 < n and re.fullmatch(r"\d+", toks[i + 1]) and free(i + 1):
                state["k"] = int(toks[i + 1])
                claim(i, i + 2, "ranking", term_id="top_k", value=state["k"])
            else:
                claim(i, i + 1, "ranking", term_id="top")
            continue

    if state["k"] is None and "nhat" in toks:
        for i in range(toks.index("nhat")):
            if not free(i) or not re.fullmatch(r"\d{1,2}", toks[i]) or not 1 <= int(toks[i]) <= 50:
                continue
            threshold_context = (i > 0 and toks[i - 1] in {"it", "nhat", "tu", "tren", "duoi"}) \
                or "tro len" in toks[i + 2:i + 5]
            if i + 1 < n and toks[i + 1] in LEADING_COUNT_NOUNS \
                    and (i == 0 or toks[i - 1] not in TIME_PREFIX) and not threshold_context:
                state["k"] = int(toks[i])
                claim(i, i + 1, "ranking", term_id="top_k", value=state["k"])
                trace.append(f"leading count k={state['k']}")
                break

    # 3. Amounts.
    for i in range(n):
        if free(i):
            val, used = parse_amount(toks[i], toks[i + 1] if i + 1 < n else None)
            if val is not None and not any(not free(x) for x in range(i, min(i + used, n))):
                state["amounts"].append(val)
                claim(i, i + used, "number", term_id="amount", value=val)

    # 4. Dictionary and cue phrases, longest first.
    proposed_used: list[str] = []
    for length in range(lex.max_len, 0, -1):
        for i in range(n - length + 1):
            key = tuple(toks[i:i + length])
            if key in lex.index and all(free(x) for x in range(i, i + length)):
                entries = lex.index[key]
                category = entries[0]["kind"]
                if length == 1 and key[0] in WEAK_SINGLE:
                    prev = toks[i - 1] if i > 0 else ""
                    if not (category == "grouping" and prev in GROUP_PREFIX):
                        continue
                claim(i, i + length, category, term_id=entries[0]["canonical"], entries=entries)
                if any(e["status"] == "proposed" for e in entries):
                    proposed_used.append(" ".join(key))

    # 5. Fuzzy fallback: edit distance 1, tokens of 4+ chars, one unambiguous candidate, never for entities.
    for i in range(n):
        tok = toks[i]
        if not free(i) or len(tok) < 4 or tok in lex.stopwords or tok.isdigit() or tok in lex.fuzzy_block:
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
            if e["status"] == "proposed":
                proposed_used.append(alias)

    spans.sort(key=lambda s: s["start"])

    def ents(s):
        return s.get("entries", [])

    # 6. Unresolved time kinds are notes, not parser failures.
    for s in spans:
        for e in ents(s):
            if e["kind"] == "time_kind" and e["canonical"].split(":")[-1] in {"range", "year"}:
                if not any(p[1].kind == e["canonical"] for p in periods):
                    notes.append(f"unresolved time: {s['surface']}")

    # 7. Metrics. Multiple candidates are a feature, not an error.
    metric_cands: list[str] = []
    for s in spans:
        for e in ents(s):
            if e["kind"] in ("concept", "constant_term") and e["canonical"] in lex.metric_ids \
                    and e["canonical"] not in metric_cands:
                metric_cands.append(e["canonical"])
    if len(metric_cands) > 1:
        notes.append("metric candidates: " + ", ".join(metric_cands))

    # 8. Direction and superlative.
    superlative = state["top"]
    sup_term = "top" if state["top"] else None
    rank_pos = [s["start"] for s in spans
                if s["category"] in ("ranking", "superlative") or " ".join(fold_phrase(s["surface"])).endswith(" nhat")]
    directions: list[str] = []
    for s in spans:
        for e in ents(s):
            if e["kind"] == "superlative" or (e["kind"] == "concept" and " ".join(fold_phrase(s["surface"])).endswith(" nhat")):
                superlative, sup_term = True, sup_term or "nhat"
                if e["kind"] == "concept":
                    directions.append("desc")
            if e["kind"] == "direction":
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

    # 9. Cues and context flags.
    cues = {e["canonical"] for s in spans for e in ents(s) if e["kind"] == "answer_cue"}
    has_pct = any(e["kind"] == "percent_cue" for s in spans for e in ents(s))
    has_change = any(e["kind"] == "change_word" for s in spans for e in ents(s))
    has_average = any(e["kind"] == "concept" and e["canonical"] == "average" for s in spans for e in ents(s))
    has_list = "liet_ke" in cues
    joined = " ".join(toks)
    count_cue = "bao_nhieu" in cues or "may" in toks or "bao nhieu" in joined
    order_count_cue = bool(re.search(r"(?:bao nhieu|bao nhiu|bnhiu|so|may) (?:don|bill|hoa don)", joined))
    customer_count_cue = bool(re.search(r"(?:bao nhieu|bao nhiu|bnhiu|so|may) khach", joined))
    debt_movement = "no" in toks and ("ghi no" in joined or "no them" in joined)

    unit = next((s["surface"] for s in spans for e in ents(s) if e["kind"] == "unit_word"), None)

    # 10. Metric choice. It is deliberately conservative; null is allowed.
    metric = choose_metric(metric_cands, toks, cues, has_average, has_pct, has_change,
                           state["top"], unit, order_count_cue, customer_count_cue)
    if metric is None and has_average and metric_cands:
        metric = metric_cands[0]
    if metric is None and (state["top"] or superlative) and any(t in toks for t in ("ban", "chay", "hot")):
        metric = "quantity_base"
        notes.append("inferred metric: quantity_base from ranking/sales cue")

    # 11. Grouping. Multiple candidates are resolved by the question cue.
    groups: list[str] = []
    for s in spans:
        for e in ents(s):
            if e["kind"] == "grouping" and e["canonical"] in lex.group_ids:
                if superlative or s["surface"].split()[0] in ("theo", "moi", "tung") or "ngay nao" in joined:
                    if e["canonical"] not in groups:
                        groups.append(e["canonical"])
    if "ngay nao" in joined and "group:day" in lex.group_ids and "group:day" not in groups:
        groups.append("group:day")
        trace.append("context:'ngay nao'->group:day")
    if (state["top"] or superlative) and not groups:
        if "khach" in toks:
            groups.append("customer_name")
        elif any(t in toks for t in PRODUCT_WORDS):
            groups.append("product_name")
        elif "ngay" in toks:
            groups.append("group:day")
        elif "thang" in toks:
            groups.append("group:month")
    group = None
    if groups:
        if "group:day" in groups:
            group = "group:day"
        else:
            group = groups[0]
        if len(groups) > 1:
            notes.append("group candidates: " + ", ".join(groups))
    if group is None and has_average and product_question(toks):
        group = "product_name"

    # 12. Change periods and comparison.
    change_direction = None
    has_increase = any(e["canonical"] == "change" and "tang" in " ".join(fold_phrase(s["surface"]))
                       for s in spans for e in ents(s))
    has_decrease = any(e["canonical"] == "change" and "giam" in " ".join(fold_phrase(s["surface"]))
                       for s in spans for e in ents(s))
    if has_increase and has_decrease:
        change_direction = "either"
    elif has_increase:
        change_direction = "increase"
    elif has_decrease:
        change_direction = "decrease"

    same_last_year = "cung ky" in joined and ("nam truoc" in joined or "nam ngoai" in joined)
    same_last_month = "cung ky" in joined and ("thang truoc" in joined or "thang roi" in joined)

    asked_periods, comparison = build_comparison(periods, has_change, has_pct, same_last_year,
                                                 same_last_month, change_direction, as_of)

    # 13. Operation.
    operation = infer_operation(
        has_rank=state["top"] or superlative,
        has_average=has_average,
        has_pct=has_pct,
        has_change=has_change,
        metric=metric,
        group=group,
        periods=asked_periods,
        cues=cues,
        order_count_cue=order_count_cue,
        customer_count_cue=customer_count_cue,
        joined=joined,
    )
    if debt_movement:
        operation = "delta"
        if metric is None:
            metric = "debt_balance"
    if operation is None:
        operation = "unknown"

    # 14. Filters, having, shift.
    filters: list[Filter] = []
    shift_value = None
    for s in spans:
        for e in ents(s):
            if e["kind"] == "status":
                add_filter(filters, "order_status", "eq", e["canonical"])
            if e["kind"] == "filter" and e["canonical"].startswith("shift:"):
                shift_value = e["canonical"].split(":", 1)[1]
            if e["kind"] == "constant_term" and e["constant"]:
                if comparator and state["amounts"]:
                    continue
                add_filter(filters, e["canonical"], e["constant"]["comparator"], e["constant"]["value"])
    if comparator and state["amounts"]:
        field = None
        if "no" in toks and "debt_balance" in metric_cands:
            field = "debt_balance"
        elif "ton" in toks and "stock_end_qty" in metric_cands:
            field = "stock_end_qty"
        elif "revenue" in metric_cands and any(t in toks for t in ("dthu", "doanh", "thu", "tien")):
            field = "revenue"
        elif metric_cands:
            field = metric_cands[0]
        if field:
            add_filter(filters, field, comparator, state["amounts"][0])
            if len(set(state["amounts"])) > 1:
                notes.append("multiple amounts with one comparator")

    having = extract_having(toks, joined)
    if having:
        if "khach" in toks:
            group = "customer_name"
        if customer_count_cue:
            metric = "customer_name"
            operation = "count"
        elif has_list:
            operation = "rank"
        elif operation in ("unknown", "sum", "rank"):
            operation = "count"
        for i, tok in enumerate(toks):
            if free(i) and tok == str(having.value):
                claim(i, i + 1, "number", term_id="having", value=having.value)
                break

    # 15. Per denominator for averages.
    per = None
    if has_average:
        per = infer_per(toks, metric)

    # 16. Ratio/share components.
    numerator = denominator = None
    if operation in ("ratio", "share"):
        if metric is None and any(f.field == "order_status" for f in filters):
            metric = "order_id"
            notes.append("inferred ratio metric: order_id from status filter")
        num_filters = [f for f in filters if f.field != "order_status"]
        status_filters = [f for f in filters if f.field == "order_status"]
        if status_filters:
            num_filters = status_filters
        else:
            num_filters = list(filters)
        denominator_filters = [f for f in filters if f.field != "order_status"]
        numerator = Component(metric=metric, filters=num_filters,
                              group_by=group if operation == "share" else None,
                              scope="group" if operation == "share" else None)
        denominator = Component(metric=metric, filters=denominator_filters,
                                group_by=None,
                                scope="total" if operation == "share" else None)
        filters = []

    # 17. Entities: preserve the original accented surface.
    leftover = [tokens[i]["raw"] for i in range(n)
                if free(i) and toks[i] not in lex.stopwords and toks[i] not in lex.filler
                and toks[i] not in {"%"}]
    entities: list[Entity] = []
    if leftover:
        concept = None
        if "khach" in toks or group == "customer_name":
            concept = "customer_name"
        elif any(t in toks for t in PRODUCT_WORDS) or group == "product_name":
            concept = "product_name"
        entities.append(Entity(text=" ".join(leftover), concept=concept, role="filter"))
        notes.append("free: " + " ".join(leftover))

    # 18. Ranking fields.
    sort_by = None
    sort_order = None
    limit = None
    if operation in ("rank", "argmax", "argmin") and (state["top"] or superlative):
        sort_by = metric or group
        limit = state["k"]
        if limit is None:
            limit = 1
        if directions:
            unique = sorted(set(directions))
            if len(unique) > 1:
                notes.append("ranking directions: " + ", ".join(unique))
            sort_order = "asc" if unique == ["asc"] else "desc"
        else:
            sort_order = "desc"
        if state["top"] and state["k"] is not None:
            limit = state["k"]

    # 19. Answer type.
    answer_type = infer_answer_type(operation, metric, group, limit, cues, has_list, has_pct, joined)

    # 20. Time object.
    time_spec = None
    if asked_periods or shift_value or comparison is not None:
        time_spec = TimeSpec(
            kind=asked_periods[0].kind if asked_periods else None,
            periods=asked_periods,
            shift=shift_value,
            comparison=comparison,
        )

    query = Query(
        operation=operation,
        metric=metric,
        distinct=True if operation == "count" and metric in ("order_id", "customer_name", "product_name") else None,
        per=per,
        filters=filters,
        having=having,
        numerator=numerator,
        denominator=denominator,
        entities=entities,
        time=time_spec,
        group_by=group,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        unit=unit,
        answer_type=answer_type,
        notes=notes,
    )
    for alias in sorted(set(proposed_used)):
        trace.append(f"proposed:{alias}")
    return finish(QueryRecord(
        question_id=qid, business_id=q.get("business_id"), as_of_date=q["as_of_date"],
        question=text, status="ok", reason=None, query=query,
    ))


# ---------------------------------------------------------------- inference helpers

def product_question(toks: list[str]) -> bool:
    return any(t in toks for t in PRODUCT_WORDS)


def choose_metric(metric_cands: list[str], toks: list[str], cues: set[str], has_average: bool,
                  has_pct: bool, has_change: bool, has_rank: bool, unit: Optional[str],
                  order_count_cue: bool, customer_count_cue: bool) -> Optional[str]:
    if metric_cands:
        if has_average:
            if "revenue" in metric_cands:
                return "revenue"
            if "quantity_base" in metric_cands:
                return "quantity_base"
        if "debt_balance" in metric_cands and "no" in toks:
            return "debt_balance"
        if customer_count_cue and "khach" in toks:
            return "customer_name"
        if order_count_cue and "order_id" in metric_cands:
            return "order_id"
        if has_pct and not has_change:
            if "debt_balance" in metric_cands and "no" in toks:
                return "debt_balance"
            if "order_id" in metric_cands and ("don" in toks or "huy" in toks):
                return "order_id"
            if "revenue" in metric_cands:
                return "revenue"
        if "debt_balance" in metric_cands and "no" in toks:
            return "debt_balance"
        if "expiry_date" in metric_cands and "han" in toks:
            return "expiry_date"
        if "stock_end_qty" in metric_cands:
            return "stock_end_qty"
        if "revenue" in metric_cands:
            return "revenue"
        if "quantity_base" in metric_cands:
            return "quantity_base"
        return metric_cands[0]
    if order_count_cue and "don" in toks:
        return "order_id"
    if customer_count_cue and "khach" in toks:
        return "customer_name"
    if unit and ("bao_nhieu" in cues or "may" in toks):
        return "quantity_base"
    if "tien" in toks:
        return "revenue"
    if "doanh thu" in " ".join(toks) or "dthu" in toks:
        return "revenue"
    if has_average:
        return "quantity_base"
    return None


def infer_operation(has_rank: bool, has_average: bool, has_pct: bool, has_change: bool,
                    metric: Optional[str], group: Optional[str], periods: list[Period],
                    cues: set[str], order_count_cue: bool, customer_count_cue: bool,
                    joined: str) -> str:
    if has_rank:
        if group == "group:day" and ("ngay nao" in joined or "ngay" in joined):
            return "argmax"
        return "rank"
    if has_average:
        return "average"
    if has_pct and has_change:
        return "percent_change"
    if has_pct:
        if any(x in joined for x in ("chiem", "trong tong", "phan cua", "ty trong")):
            return "share"
        return "ratio"
    if has_change and len(periods) >= 2:
        return "difference"
    if "liet_ke" in cues:
        return "rank" if group else "series"
    if metric == "expiry_date":
        return "lookup"
    if group in ("group:day", "group:month") and (len(periods) > 1 or "theo" in joined):
        return "series"
    if len(periods) > 1:
        return "series"
    if metric in ("order_id", "customer_name", "product_name"):
        return "count"
    if metric:
        return "sum"
    if periods:
        return "sum"
    return "unknown"


def infer_per(toks: list[str], metric: Optional[str]) -> Optional[str]:
    joined = " ".join(toks)
    if any(x in joined for x in ("moi don", "mot don", "tren mot don", "tren moi don", "moi khach")):
        if "khach" in toks:
            return "customer_name"
        return "order_id"
    if "moi ngay" in joined or "tren ngay" in joined:
        return "group:day"
    if "moi thang" in joined or "tren thang" in joined:
        return "group:month"
    if metric in ("revenue", "quantity_base"):
        return "order_id"
    return None


def infer_answer_type(operation: str, metric: Optional[str], group: Optional[str], limit: Optional[int],
                      cues: set[str], has_list: bool, has_pct: bool, joined: str) -> Optional[str]:
    if operation == "trend" or "co phai" in joined or "dung khong" in joined:
        return "boolean"
    if "bang" in joined and ("cot" in joined or "bang" in joined):
        return "table"
    if operation in ("ratio", "share", "percent_change"):
        return "percent"
    if has_pct and operation in ("unknown", "sum", "count"):
        return "percent"
    if operation in ("argmax", "argmin") or (operation == "lookup" and metric == "expiry_date"):
        return "date"
    if operation == "rank":
        if limit and limit > 1:
            return "list"
        if "nao" in cues:
            return "string"
        if "bao_nhieu" in cues:
            return "number"
        return "string"
    if has_list:
        return "list"
    if group in ("group:day", "group:month"):
        return "list"
    if operation in ("count", "sum", "average", "difference", "delta", "bucket"):
        return "number"
    if metric in ("revenue", "quantity_base", "order_id", "debt_balance", "stock_end_qty", "customer_name"):
        return "number"
    if operation == "series":
        return "list"
    if operation == "lookup":
        return "date" if metric == "expiry_date" else "string"
    return None


def add_filter(filters: list[Filter], field: str, op: str, value) -> None:
    for f in filters:
        if f.field == field and f.op == op and f.value == value:
            return
    filters.append(Filter(field=field, op=op, value=value))


def extract_having(toks: list[str], joined: str) -> Optional[Having]:
    if "khach" not in toks and "don" not in toks:
        return None
    patterns = [
        (r"(?:tu |it nhat |khong duoi )(\d+) don ", "gte"),
        (r"(\d+) don tro len ", "gte"),
        (r"tren (\d+) don ", "gt"),
        (r"duoi (\d+) don ", "lt"),
        (r"toi da (\d+) don ", "lte"),
        (r"mua (\d+) don ", "eq"),
    ]
    text = " " + joined + " "
    for pattern, op in patterns:
        m = re.search(pattern, text)
        if m:
            return Having(aggregation="count", field="order_id", op=op, value=int(m[1]), distinct=True)
    return None


# ---------------------------------------------------------------- comparison helpers

def _shift_month(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def shift_period(period: Period, years: int = 0, months: int = 0) -> Period:
    def shift_iso(iso: Optional[str]) -> Optional[str]:
        if not iso:
            return None
        d = date.fromisoformat(iso)
        if years:
            try:
                d = d.replace(year=d.year + years)
            except ValueError:
                d = d.replace(year=d.year + years, day=28)
        if months:
            d = _shift_month(d, months)
        return d.isoformat()

    year = period.year + years if period.year else period.year
    month = period.month
    if month and months:
        m = month - 1 + months
        year = (year or date.today().year) + m // 12
        month = m % 12 + 1
    return Period(kind=period.kind, month=month, year=year, date=shift_iso(period.date),
                  start=shift_iso(period.start), end=shift_iso(period.end),
                  anchor=period.anchor, raw=period.raw)


def build_comparison(periods: list[tuple[int, Period]], has_change: bool, has_pct: bool,
                     same_last_year: bool, same_last_month: bool, direction: Optional[str],
                     as_of: date) -> tuple[list[Period], Optional[Comparison]]:
    plist = [p for _, p in sorted(periods, key=lambda x: x[0])]
    if not (has_change or has_pct):
        return plist, None
    def period_key(p: Period) -> str:
        if p.start:
            return p.start
        if p.date:
            return p.date
        if p.year and p.month:
            return f"{p.year:04d}-{p.month:02d}-01"
        if p.year:
            return f"{p.year:04d}-01-01"
        return "0000-00-00"

    chronological = sorted(plist, key=period_key)
    asked = chronological[-1:] if chronological else []
    comparison = None
    if same_last_year and asked:
        base_period = shift_period(asked[0], years=-1)
        base_period.raw = None
        comparison = Comparison(base="same_period_last_year",
                                periods=[base_period], direction=direction)
    elif same_last_month and asked:
        base_period = shift_period(asked[0], months=-1)
        base_period.raw = None
        comparison = Comparison(base="same_period_last_month",
                                periods=[base_period], direction=direction)
    elif len(chronological) >= 2:
        comparison = Comparison(base="explicit", periods=[chronological[0]], direction=direction)
    return asked, comparison


# ---------------------------------------------------------------- serialization

def span_json(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k != "entries"}
    out["terms"] = [{"canonical": e["canonical"], "kind": e["kind"], "status": e["status"]}
                    for e in s.get("entries", [])]
    return out


def parse_question(q: dict, lex: Lexicon, debug: bool = False) -> dict:
    return extract_record(q, lex, debug=debug)


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--dictionary", required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--rules", help="ignored; kept for old pipeline compatibility")
    ap.add_argument("--out", required=True)
    ap.add_argument("--confirmed-only", action="store_true", help="ignore entries marked proposed")
    ap.add_argument("--debug", action="store_true", help="include spans/free tokens/trace")
    a = ap.parse_args(argv)
    lex = Lexicon(a.dictionary, a.cues, a.rules, allow_proposed=not a.confirmed_only)
    status, reasons, notes, free_tokens, n = Counter(), Counter(), Counter(), Counter(), 0
    lines = []
    for line in Path(a.questions).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        q = json.loads(line)
        try:
            rec = extract_record(q, lex, debug=a.debug)
        except Exception as exc:  # never drop a question
            rec = record_to_dict(QueryRecord(
                question_id=q.get("id", ""), business_id=q.get("business_id"),
                as_of_date=q.get("as_of_date", ""), question=q.get("question", ""),
                status="error", reason=f"parser error: {type(exc).__name__}: {exc}", query=Query(),
            ))
        status[rec["status"]] += 1
        if rec.get("reason"):
            reasons[rec["reason"]] += 1
        for note in rec.get("query", {}).get("notes", []):
            notes[note.split(":")[0]] += 1
        if a.debug:
            free_tokens.update(rec.get("debug", {}).get("free_tokens", []))
        n += 1
        lines.append(json.dumps(rec, ensure_ascii=False))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"questions: {n}  status: {dict(status)}", file=sys.stderr)
    for reason, c in reasons.most_common(10):
        print(f"  {c:5d}  {reason}", file=sys.stderr)
    if notes:
        print("notes:", file=sys.stderr)
        for note, c in notes.most_common(10):
            print(f"  {c:5d}  {note}", file=sys.stderr)
    if a.debug:
        print("most frequent leftover words:", file=sys.stderr)
        for tok, c in free_tokens.most_common(15):
            print(f"  {c:5d}  {tok}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())