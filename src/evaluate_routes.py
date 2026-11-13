#!/usr/bin/env python3
"""Coverage and answer-type report for Query Spec v1.

This replaces the old shape-route evaluator. It does not score values and it does
not require templates. It reports how many questions produced each operation,
which fields are populated, and whether answer_type agrees with the gold type when
answers.jsonl is supplied.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--query-specs", required=True)
    ap.add_argument("--answers")
    ap.add_argument("--split")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    questions = {q["id"]: q for q in read_jsonl(a.questions)}
    specs = {r["question_id"]: r for r in read_jsonl(a.query_specs)}
    answers = {r["id"]: r for r in read_jsonl(a.answers)} if a.answers else {}
    split = json.loads(Path(a.split).read_text(encoding="utf-8")) if a.split else None

    def bucket(qid):
        if not split:
            return "all"
        biz = questions.get(qid, {}).get("business_id")
        if biz in set(split.get("heldout", [])):
            return "heldout"
        return "dev"

    def add_report(target, qids):
        status = Counter()
        operations = Counter()
        answer_types = Counter()
        reasons = Counter()
        notes = Counter()
        populated = Counter()
        type_match = 0
        type_total = 0
        missing = 0
        for qid in qids:
            rec = specs.get(qid)
            if rec is None:
                missing += 1
                continue
            status[rec.get("status", "missing")] += 1
            if rec.get("reason"):
                reasons[rec["reason"]] += 1
            query = rec.get("query") or {}
            operations[query.get("operation")] += 1
            answer_types[query.get("answer_type")] += 1
            for note in query.get("notes", []):
                notes[note.split(":")[0]] += 1
            for field in ("metric", "operation", "answer_type", "group_by", "sort_by", "unit", "per"):
                if query.get(field) is not None:
                    populated[field] += 1
            if query.get("time") and query["time"].get("periods"):
                populated["time"] += 1
            if query.get("filters"):
                populated["filters"] += 1
            if query.get("entities"):
                populated["entities"] += 1
            gold = answers.get(qid)
            if gold:
                expected = gold.get("type")
                got = query.get("answer_type")
                if expected == "unanswerable":
                    continue
                if got is not None:
                    type_total += 1
                    if got == expected:
                        type_match += 1
        target.update({
            "questions": len(qids),
            "missing_specs": missing,
            "status": dict(status),
            "operations": dict(operations.most_common()),
            "answer_types": dict(answer_types.most_common()),
            "populated": dict(populated.most_common()),
            "reasons": dict(reasons.most_common(20)),
            "notes": dict(notes.most_common(20)),
            "answer_type_agreement": {
                "matched": type_match,
                "compared": type_total,
                "rate": round(type_match / type_total, 4) if type_total else None,
            },
        })

    qids = list(questions)
    report = {"all": {}, "dev": {}, "heldout": {}}
    add_report(report["all"], qids)
    if split:
        add_report(report["dev"], [q for q in qids if bucket(q) == "dev"])
        add_report(report["heldout"], [q for q in qids if bucket(q) == "heldout"])
    else:
        report.pop("dev")
        report.pop("heldout")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["all"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())