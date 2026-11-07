"""Score suggested answers against gold answers (rulebook §9).

Per question: correct +1, wrong -1, abstain or missing line 0.
Penalized Accuracy = (sum of scores) / (total gold questions).

Matching rules (rulebook §9):
  - Type must match exactly; wrong JSON type for the value is wrong (e.g. "4820000" for number).
  - number, percent: exact (float tolerance 1e-9).
  - date: exact string.
  - boolean: exact.
  - string: case, whitespace and punctuation ignored; Vietnamese diacritics are NOT ignored.
    Any alias in answers.jsonl is also accepted (same normalization).
  - list: same length and same order; each element matched by the same rules.
  - table: columns matched by name (normalized as above); if a gold column name is not in the
    prediction, the prediction column at the same position is used. Row order is ignored.
  - unanswerable: correct if the prediction is unanswerable (reason is not scored).

Usage:
  python evaluate.py                       (defaults: train/answers.jsonl vs train/suggested_answers.jsonl)
  python evaluate.py --gold PATH --pred PATH
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def norm_str(s):
    """Case, whitespace and punctuation are ignored. Diacritics are kept (NFC)."""
    s = unicodedata.normalize("NFC", str(s)).lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def value_eq(g, p):
    """Compare a gold value with a prediction value (no alias handling)."""
    if isinstance(g, bool) or isinstance(p, bool):
        return isinstance(g, bool) and isinstance(p, bool) and g == p
    if is_num(g) or is_num(p):
        return is_num(g) and is_num(p) and abs(float(g) - float(p)) < 1e-9
    if isinstance(g, str) or isinstance(p, str):
        return isinstance(g, str) and isinstance(p, str) and norm_str(g) == norm_str(p)
    if isinstance(g, list) or isinstance(p, list):
        return (isinstance(g, list) and isinstance(p, list) and len(g) == len(p)
                and all(value_eq(a, b) for a, b in zip(g, p)))
    return g is None and p is None


def table_eq(g, p):
    if not (isinstance(g, dict) and isinstance(p, dict)):
        return False
    gc, pc = g.get("columns", []), p.get("columns", [])
    grows, prows = g.get("rows", []), p.get("rows", [])
    if len(gc) != len(pc):
        return False
    pnames = [norm_str(c) for c in pc]
    # column j of gold -> column index in prediction
    col_map = []
    for j, name in enumerate(gc):
        n = norm_str(name)
        col_map.append(pnames.index(n) if n in pnames else j)
    if len(set(col_map)) != len(col_map) or any(i >= len(pc) for i in col_map):
        return False
    if len(grows) != len(prows):
        return False

    gkeys = sorted(repr([canon_val(r[j] if j < len(r) else None) for j in range(len(gc))])
                   for r in grows)
    pkeys = sorted(repr([canon_val(r[i] if i < len(r) else None) for i in col_map])
                   for r in prows)
    return gkeys == pkeys


def canon_val(v):
    if is_num(v):
        return round(float(v), 9)
    if isinstance(v, str):
        return norm_str(v)
    return v


def is_correct(gold, pred):
    gt, pt = gold["type"], pred.get("type")
    if gt != pt:
        return False
    if gt == "unanswerable":
        return True
    gv, pv = gold.get("answer"), pred.get("answer")
    if gt in ("number", "percent"):
        return is_num(pv) and value_eq(gv, pv)
    if gt == "date":
        return isinstance(pv, str) and str(gv).strip() == pv.strip()
    if gt == "boolean":
        return isinstance(pv, bool) and gv == pv
    if gt == "string":
        if not isinstance(pv, str):
            return False
        cands = [gv] + [a for a in (gold.get("alias") or []) if isinstance(a, str)]
        return any(norm_str(c) == norm_str(pv) for c in cands)
    if gt == "list":
        return isinstance(pv, list) and value_eq(gv, pv)
    if gt == "table":
        return table_eq(gv, pv)
    return False


def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=os.path.join(ROOT, "train", "answers.jsonl"))
    ap.add_argument("--pred", default=os.path.join(ROOT, "train", "suggested_answers.jsonl"))
    args = ap.parse_args()

    gold = load_jsonl(args.gold)
    pred = {}
    dup = 0
    for p in load_jsonl(args.pred):
        if p["id"] in pred:
            dup += 1
        pred[p["id"]] = p
    if dup:
        print(f"warning: {dup} duplicate prediction ids; last line kept")

    total = len(gold)
    score_sum = 0
    counts = Counter()
    by_type = {}
    for g in gold:
        p = pred.get(g["id"])
        bucket = by_type.setdefault(g["type"], Counter())
        if p is None or p.get("type") == "abstain":
            counts["abstain"] += 1
            bucket["abstain"] += 1
            continue
        if is_correct(g, p):
            score_sum += 1
            counts["correct"] += 1
            bucket["correct"] += 1
        else:
            score_sum -= 1
            counts["wrong"] += 1
            bucket["wrong"] += 1

    pa = score_sum / total if total else 0.0
    attempted = counts["correct"] + counts["wrong"]
    print(f"gold={args.gold}")
    print(f"pred={args.pred}")
    print(f"questions={total}")
    print(f"correct={counts['correct']} wrong={counts['wrong']} abstain={counts['abstain']}")
    print(f"sum of scores={score_sum}")
    print(f"Penalized Accuracy = {score_sum}/{total} = {pa:.4f}")
    if attempted:
        print(f"Precision on attempted = {counts['correct'] / attempted:.4f} ({attempted} attempted)")
    print("by gold type:")
    for t, b in sorted(by_type.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {t:14s} correct={b['correct']:4d} wrong={b['wrong']:4d} abstain={b['abstain']:4d}")


if __name__ == "__main__":
    main()
