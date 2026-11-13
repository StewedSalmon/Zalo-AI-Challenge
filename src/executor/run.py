#!/usr/bin/env python3
"""Run the six-shape value executor on routed questions and compare with gold answers.

Writes, under --out:
  log.jsonl     one line per routed question: id, business, shape, status, value, gold, agree, error, elapsed_ms
  errors.jsonl  only the questions with status error (the per-question error log)
  summary.json  counts by status and by shape, load reports per business

Usage:
  python src/executor/run.py --routes routes.jsonl --answers train/answers.jsonl \
      --questions train/questions.jsonl --data train/data --split src/splits/split_seed20260512.json \
      --part dev --out <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from executor.aggregate import FAMILY, execute  # noqa: E402
from executor.ingest import IngestError, load_orders  # noqa: E402


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--part", choices=["dev", "heldout", "all"], required=True)
    ap.add_argument("--businesses", help="comma-separated business ids to restrict to (optional)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    split = json.loads(Path(a.split).read_text(encoding="utf-8"))
    part_set = {"dev": set(split["dev"]), "heldout": set(split["heldout"]), "all": None}[a.part]
    biz_of = {q["id"]: q["business_id"] for q in read_jsonl(a.questions)}
    gold = {r["id"]: r for r in read_jsonl(a.answers)}
    only = set(a.businesses.split(",")) if a.businesses else None

    routes = []
    for r in read_jsonl(a.routes):
        if r["status"] != "ok" or r["shape"] not in FAMILY:
            continue
        biz = r["business_id"]
        if part_set is not None and biz not in part_set:
            continue
        if only is not None and biz not in only:
            continue
        routes.append(r)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data_root = Path(a.data)
    tables, load_errors, load_reports = {}, {}, {}
    log_rows = []
    for r in sorted(routes, key=lambda x: (x["business_id"], x["question_id"])):
        biz = r["business_id"]
        if biz not in tables and biz not in load_errors:
            try:
                tables[biz] = load_orders(biz, data_root)
                load_reports[biz] = {"report": dict(tables[biz].report), "orders": len(tables[biz].orders),
                                     "data_start": str(tables[biz].data_start), "data_end": str(tables[biz].data_end),
                                     "unmapped_status": dict(tables[biz].unknown_status)}
            except IngestError as exc:
                load_errors[biz] = str(exc)
                load_reports[biz] = {"error": str(exc)}
            except Exception as exc:  # keep the run going; the question log shows the failure
                load_errors[biz] = f"{type(exc).__name__}: {exc}"
                load_reports[biz] = {"error": load_errors[biz]}
        g = gold.get(r["question_id"], {})
        gtype, gval = g.get("type"), g.get("answer")
        row = {"id": r["question_id"], "business_id": biz, "shape": r["shape"], "as_of_date": r["as_of_date"],
               "gold_type": gtype, "gold": gval, "status": None, "value": None, "agree": None, "error": None}
        t0 = time.perf_counter()
        if biz in load_errors:
            row["status"], row["error"] = "error", f"load: {load_errors[biz]}"
        else:
            try:
                res = execute(r["body"], r["shape"], date.fromisoformat(r["as_of_date"]), tables[biz])
                row["status"], row["value"], row["error"] = res.status, res.value, res.error
                row["detail"] = res.detail
            except Exception as exc:
                row["status"], row["error"] = "error", f"executor: {type(exc).__name__}: {exc}"
        row["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        if row["status"] == "ok":
            if gtype == "unanswerable":
                row["agree"] = None
                row["note"] = "gold unanswerable; executor produced a value"
            elif gtype == "number":
                row["agree"] = row["value"] == gval
            else:
                row["agree"] = None
                row["note"] = f"gold type {gtype} is outside the number family"
        elif row["status"] == "unanswerable":
            row["agree"] = gtype == "unanswerable"
        log_rows.append(row)

    with (out / "log.jsonl").open("w", encoding="utf-8") as fh:
        for row in log_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (out / "errors.jsonl").open("w", encoding="utf-8") as fh:
        for row in log_rows:
            if row["status"] == "error":
                fh.write(json.dumps({"id": row["id"], "business_id": row["business_id"], "shape": row["shape"],
                                     "error": row["error"]}, ensure_ascii=False) + "\n")

    by_status = Counter(r["status"] for r in log_rows)
    agree = Counter()
    by_shape = defaultdict(Counter)
    by_biz = defaultdict(Counter)
    for r in log_rows:
        key = "agree" if r["agree"] is True else "disagree" if r["agree"] is False else "not_scored"
        agree[key] += 1
        by_shape[r["shape"]][r["status"]] += 1
        by_shape[r["shape"]][key] += 1
        by_biz[r["business_id"]][r["status"]] += 1
        by_biz[r["business_id"]][key] += 1
    summary = {"part": a.part, "routed_in_family": len(log_rows), "status": dict(by_status),
               "value_check": dict(agree), "by_shape": {k: dict(v) for k, v in sorted(by_shape.items())},
               "by_business": {k: dict(v) for k, v in sorted(by_biz.items())}, "loads": load_reports}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"part={a.part} routed={len(log_rows)} status={dict(by_status)} value_check={dict(agree)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
