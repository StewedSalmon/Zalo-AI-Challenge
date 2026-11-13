#!/usr/bin/env python3
"""Bridge Query Spec v1 records into the six-shape aggregate executor and score against gold.

adapt() maps a parser record to (shape, body) for executor.aggregate.execute when the question is
inside the aggregate family: operation sum/count, metric revenue/order_id, no filters/having/
entities, one resolved period with concrete start/end, no group/sort/limit/unit/distinct/shift/
comparison, answer_type number. Everything else is unsupported with a reason; nothing is guessed.

main() runs the adapted family on train data, executes each question, and writes:
  value_run.jsonl      one line per question: id, business, bucket, period, status, value, gold, agree, score, error
  value_run_summary.json  counts by status/agreement/score, per business and per dev/heldout bucket

Scoring follows the agreed matrix: correct value +1, wrong value -1, abstain on answerable 0,
abstain (incl. unanswerable or error) on unanswerable +1, any value on unanswerable -1.

Usage:
  python src/spec_adapter.py --questions train/questions.jsonl --query-specs src/analysis_out/query_specs_train.jsonl \
      --answers train/answers.jsonl --data train/data --split src/splits/split_seed20260512.json \
      --out src/analysis_out
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from executor.aggregate import execute  # noqa: E402
from executor.ingest import IngestError, load_orders  # noqa: E402

NULLABLE_QUERY_FIELDS = ("group_by", "sort_by", "sort_order", "limit", "unit", "per",
                         "numerator", "denominator")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def adapt(record):
    """Return (status, reason, shape, body). status is ok | unsupported; never a guess."""
    if record.get("status") != "ok":
        return "unsupported", f"parser status {record.get('status')!r}", None, None
    query = record.get("query") or {}
    operation = query.get("operation")
    metric = query.get("metric")
    if operation not in ("sum", "count"):
        return "unsupported", f"operation {operation!r} not in family", None, None
    wanted = "revenue" if operation == "sum" else "order_id"
    if metric != wanted:
        return "unsupported", f"metric {metric!r} does not match operation {operation!r}", None, None
    if query.get("filters") or query.get("having") or query.get("entities"):
        return "unsupported", "filters/having/entities present", None, None
    if query.get("distinct") is not None:
        if not (operation == "count" and metric == "order_id"
                and query["distinct"] in (True, "order_id")):
            return "unsupported", f"distinct {query['distinct']!r} not supported", None, None
    for field in NULLABLE_QUERY_FIELDS:
        if query.get(field) is not None:
            return "unsupported", f"{field} {query[field]!r} not supported", None, None
    if query.get("answer_type") != "number":
        return "unsupported", f"answer_type {query.get('answer_type')!r} not number", None, None
    time = query.get("time") or {}
    if time.get("shift") or time.get("comparison"):
        return "unsupported", "shift/comparison present", None, None
    periods = time.get("periods") or []
    if len(periods) != 1:
        return "unsupported", f"{len(periods)} periods, need exactly 1", None, None
    period = periods[0]
    start, end = period.get("start"), period.get("end")
    if not start or not end:
        return "unsupported", "period has no concrete start/end", None, None
    if operation == "sum":
        shape = "aggregate_day" if start == end else "aggregate_range"
    else:
        shape = "count_day" if start == end else "count_range"
    slots = {"date": start} if start == end else {"start": start, "end": end}
    body = {"common": {"metric": wanted, "filters": [],
                       "time": {"kind": "day" if start == end else "range", "slots": slots}},
            "shape_fields": {}}
    return "ok", None, shape, body


def score(status, value, gold_type, gold):
    """Submission-semantics score for one executed question: +1, 0 or -1."""
    if status == "ok":
        if gold_type == "unanswerable":
            return -1, "wrong", "value given on unanswerable gold"
        if gold_type == "number" and isinstance(gold, (int, float)):
            agree = abs(value - gold) < 1e-9
            return (1, "correct", None) if agree else (-1, "wrong", "value mismatch")
        return -1, "wrong", f"gold type {gold_type!r} outside number family"
    if gold_type == "unanswerable":
        return 1, "correct", None
    return 0, "abstain", "no answer on answerable gold"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--query-specs", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--split")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    questions = {q["id"]: q for q in read_jsonl(a.questions)}
    answers = {r["id"]: r for r in read_jsonl(a.answers)}
    split = json.loads(Path(a.split).read_text(encoding="utf-8")) if a.split else None
    heldout = set(split["heldout"]) if split else set()

    def bucket(biz):
        if not split:
            return "all"
        return "heldout" if biz in heldout else "dev"

    family = []
    unsupported = Counter()
    for rec in read_jsonl(a.query_specs):
        status, reason, shape, body = adapt(rec)
        if status != "ok":
            unsupported[reason] += 1
            continue
        family.append({"question_id": rec["question_id"], "business_id": rec["business_id"],
                       "as_of_date": rec["as_of_date"], "shape": shape, "body": body,
                       "period": (rec["query"]["time"]["periods"][0])})

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data_root = Path(a.data)
    tables, load_errors, load_reports = {}, {}, {}
    rows = []
    for item in sorted(family, key=lambda x: (x["business_id"], x["question_id"])):
        biz = item["business_id"]
        if biz not in tables and biz not in load_errors:
            try:
                tables[biz] = load_orders(biz, data_root)
                load_reports[biz] = {"orders": len(tables[biz].orders),
                                     "data_start": str(tables[biz].data_start),
                                     "data_end": str(tables[biz].data_end),
                                     "report": dict(tables[biz].report),
                                     "unknown_status": dict(tables[biz].unknown_status)}
            except (IngestError, Exception) as exc:  # keep the run going; the log shows the failure
                load_errors[biz] = f"{type(exc).__name__}: {exc}"
                load_reports[biz] = {"error": load_errors[biz]}
        gold = answers.get(item["question_id"], {})
        gold_type, gold_value = gold.get("type"), gold.get("answer")
        period = item["period"]
        row = {"id": item["question_id"], "business_id": biz, "bucket": bucket(biz),
               "shape": item["shape"], "time_kind": period.get("kind"),
               "start": period.get("start"), "end": period.get("end"),
               "as_of_date": item["as_of_date"], "gold_type": gold_type, "gold": gold_value,
               "status": None, "value": None, "agree": None, "score": None, "verdict": None,
               "note": None, "error": None}
        if biz in load_errors:
            row["status"], row["error"] = "error", f"load: {load_errors[biz]}"
            row["score"], row["verdict"], row["note"] = score("error", None, gold_type, gold_value)
        else:
            try:
                res = execute(item["body"], item["shape"],
                              date.fromisoformat(item["as_of_date"]), tables[biz])
                row["status"], row["value"], row["error"], row["detail"] = res.status, res.value, res.error, res.detail
                row["score"], row["verdict"], row["note"] = score(res.status, res.value, gold_type, gold_value)
                if res.status == "ok" and gold_type == "number" and isinstance(gold_value, (int, float)):
                    row["agree"] = abs(res.value - gold_value) < 1e-9
                elif res.status == "unanswerable" and gold_type == "unanswerable":
                    row["agree"] = True
            except Exception as exc:
                row["status"], row["error"] = "error", f"executor: {type(exc).__name__}: {exc}"
                row["score"], row["verdict"], row["note"] = score("error", None, gold_type, gold_value)
        rows.append(row)

    with (out / "value_run.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summarize(part_rows):
        stat = Counter(r["status"] for r in part_rows)
        verdict = Counter(r["verdict"] for r in part_rows)
        agree = Counter(r["agree"] for r in part_rows if r["agree"] is not None)
        by_biz = defaultdict(Counter)
        by_kind = defaultdict(Counter)
        by_gold = defaultdict(Counter)
        for r in part_rows:
            by_biz[r["business_id"]][r["verdict"]] += 1
            by_kind[r["time_kind"]][r["verdict"]] += 1
            by_gold[r["gold_type"]][r["verdict"]] += 1
        total = len(part_rows)
        points = sum(r["score"] for r in part_rows if r["score"] is not None)
        return {"questions": total,
                "status": dict(stat),
                "verdicts": dict(verdict),
                "agree": {"true": agree.get(True, 0), "false": agree.get(False, 0)},
                "family_pa": round(points / total, 4) if total else None,
                "by_business": {k: dict(v) for k, v in sorted(by_biz.items())},
                "by_time_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
                "by_gold_type": {k: dict(v) for k, v in sorted(by_gold.items())}}

    summary = {"family_size": len(family), "unsupported_reasons": dict(unsupported.most_common()),
               "loads": load_reports, "all": summarize(rows)}
    if split:
        summary["dev"] = summarize([r for r in rows if r["bucket"] == "dev"])
        summary["heldout"] = summarize([r for r in rows if r["bucket"] == "heldout"])
    mismatch_ids = [r["id"] for r in rows if r["verdict"] == "wrong"]
    summary["mismatch_ids"] = mismatch_ids
    (out / "value_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    print(json.dumps(summary["all"], ensure_ascii=False, indent=2))
    print(f"unsupported questions outside family: {sum(unsupported.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
