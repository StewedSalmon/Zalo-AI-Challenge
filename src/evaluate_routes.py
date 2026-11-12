#!/usr/bin/env python3
"""Evaluate routes.jsonl against train/answers.jsonl, split by business.

Split: businesses are sorted, shuffled with a recorded seed, and the first 30% become the held-out set.
The split is written once to --split (JSON) and reused on later runs, so it can be reproduced.
Tuning should use dev only; held-out numbers are reported separately.

Value comparison: runs only when --predictions is given (one line per question: {"question_id", "value"}).
No executor exists under src/, so this script never produces values by itself.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

SEED = 20260512
HELDOUT_FRACTION = 0.30


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def get_split(questions, split_path):
    path = Path(split_path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    businesses = sorted({q["business_id"] for q in questions if q.get("business_id")})
    shuffled = list(businesses)
    random.Random(SEED).shuffle(shuffled)
    k = round(len(businesses) * HELDOUT_FRACTION)
    split = {
        "seed": SEED,
        "method": "sorted business ids, random.Random(seed).shuffle, first round(0.30*N) are held out",
        "heldout_fraction": HELDOUT_FRACTION,
        "businesses": businesses,
        "heldout": sorted(shuffled[:k]),
        "dev": sorted(shuffled[k:]),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, ensure_ascii=False, indent=2), encoding="utf-8")
    return split


def fold(text):
    return " ".join(unicodedata.normalize("NFC", str(text)).casefold().split())


def normalize_value(kind, value):
    """Comparable form per answer type. Returns None when the value is not comparable."""
    if value is None:
        return None
    if kind == "number":
        return round(float(value), 2)
    if kind == "percent":
        return round(float(value), 2)
    if kind == "boolean":
        return bool(value)
    if kind == "date":
        return str(value)[:10]
    if kind == "string":
        return fold(value)
    if kind == "list":
        return [fold(v) if isinstance(v, str) else v for v in value]
    if kind == "table":
        return [[fold(c) if isinstance(c, str) else c for c in row] for row in value]
    return None


def compare_values(answers, predictions):
    """answers: qid -> {type, answer}; predictions: qid -> value. Returns counts per answer type."""
    out = defaultdict(Counter)
    for qid, pred in predictions.items():
        gold = answers.get(qid)
        if gold is None or gold["type"] == "unanswerable":
            out["_skipped"]["no comparable gold"] += 1
            continue
        g = normalize_value(gold["type"], gold["answer"])
        p = normalize_value(gold["type"], pred)
        out[gold["type"]]["match" if g == p else "mismatch"] += 1
    return {k: dict(v) for k, v in out.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--predictions")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    questions = load_jsonl(a.questions)
    answers = {r["id"]: r for r in load_jsonl(a.answers)}
    routes = load_jsonl(a.routes)
    templates = {t["shape"]: t for t in load_jsonl(a.templates)}
    rules = json.loads(Path(a.rules).read_text(encoding="utf-8"))
    split = get_split(questions, a.split)
    heldout = set(split["heldout"])
    biz = {q["id"]: q["business_id"] for q in questions}
    rule_shape = {r["id"]: r.get("shape") for r in rules["rules"]}

    report = {"split": {"seed": split["seed"], "heldout_businesses": len(heldout),
                        "dev_businesses": len(split["dev"]), "heldout_file": a.split}}
    for name, keep in (("all", lambda q: True), ("dev", lambda q: biz[q] not in heldout),
                       ("heldout", lambda q: biz[q] in heldout)):
        rows = [r for r in routes if keep(r["question_id"])]
        status = Counter(r["status"] for r in rows)
        reasons = Counter(f"{r['status']}: {r['reason']}" for r in rows if r["status"] != "ok")
        per_rule = defaultdict(lambda: {"matched": 0, "agree": 0, "mismatch_ids": []})
        overall = Counter()
        for r in rows:
            if r["status"] != "ok":
                continue
            gold = answers.get(r["question_id"], {}).get("type")
            agree = templates[r["shape"]]["answer"]["type"] == gold
            overall["agree" if agree else ("gold_unanswerable" if gold == "unanswerable" else "disagree")] += 1
            pr = per_rule[r["rule_id"]]
            pr["matched"] += 1
            pr["agree"] += int(agree)
            if not agree:
                pr["mismatch_ids"].append(r["question_id"])
        report[name] = {
            "questions": len(rows),
            "status": dict(status),
            "ok_type_agreement": dict(overall),
            "per_rule": {k: {"shape": rule_shape.get(k), "matched": v["matched"], "agree": v["agree"],
                             "mismatch_ids": v["mismatch_ids"]} for k, v in sorted(per_rule.items())},
            "top_reasons": reasons.most_common(12),
        }

    if a.predictions:
        preds = {r["question_id"]: r["value"] for r in load_jsonl(a.predictions)}
        ok_ids = {r["question_id"] for r in routes if r["status"] == "ok"}
        report["value_check"] = compare_values(answers, {q: v for q, v in preds.items() if q in ok_ids})
    else:
        report["value_check"] = "not run: no predictions file (no executor under src/)"

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    for name in ("all", "dev", "heldout"):
        r = report[name]
        print(f"[{name}] questions={r['questions']} status={r['status']} type_agreement={r['ok_type_agreement']}",
              file=sys.stderr)
    print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
