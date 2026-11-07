"""Deterministic baseline pipeline for DataMate.

Reads:  <input>/rulebook.md, <input>/questions.jsonl, <input>/data/<biz>/ (csv, xlsx, sqlite, README.md)
Writes: <output>                (submission lines: {"id", "type", "answer"} or abstain/unanswerable)
        <log-dir>/decisions.jsonl (one line per question: gates, sources, decision, reason)
        <log-dir>/summary.json

Scope (deliberately small):
  - Intents: revenue (doanh thu) and order count (so don) for an explicit day, an explicit
    month, "thang nay", "hom nay", "hom qua".
  - Everything else is abstained. Abstain scores 0; a wrong answer scores -1.

Decision rule (no probability is estimated; a question is answered only if every gate passes):
  G1 intent and period parsed
  G2 exactly one sales table of the preferred grain (line-level if one exists)
  G3 order id column present for order counts
  G4 status of every row in the period is known (mapped via STATUS_MAP); no unmapped label
  G5 for unanswerable: the data end comes from the README, not from a fallback
  Otherwise the question is abstained with the failing gate in the reason.

Rules implemented (rulebook §1, §2, §7, §8, §9):
  - Revenue = sum of line amounts of orders that are not cancelled and not returned,
    counted on order date. Order-level discounts and fees are not subtracted.
  - Order count = distinct order ids that are not cancelled and not returned.
  - Identical rows within one order count once (§8).
  - Partial overlap with the data range -> computed on the available part (§7).
  - Money and counts: integer answers (§9).

The rulebook is hashed into the decision log. Its rules are implemented by hand here;
the rulebook text is not parsed.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import pickle
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # project folder: contains train/ and public_test/
sys.path.insert(0, HERE)
from table_io import read_business  # noqa: E402


def _code_version():
    h = hashlib.sha256()
    for name in ("table_io.py", os.path.basename(__file__)):
        with open(os.path.join(HERE, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


CODE_VERSION = _code_version()

# ---------------------------------------------------------------- text helpers


def fold(s):
    """Accent-free, lowercase, single-spaced text."""
    s = str(s).replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"\s+", " ", s).strip()


def fold_question(q):
    f = fold(q)
    f = re.sub(r"\bdthu\b", "doanh thu", f)
    f = re.sub(r"\bhnay\b", "hom nay", f)
    f = re.sub(r"\bhqua\b", "hom qua", f)
    f = re.sub(r"\b(bnhiu|bnhieu|bao nhiu|bao nhieu|bn|nhiu)\b", "bao nhieu", f)
    return f


# ---------------------------------------------------------------- value parsing

def parse_date(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, (int, float)):
        if 30000 <= v <= 80000:  # Excel serial day
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))
        return None
    s = fold(v)
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.match(r"^(\d{1,2}) thg (\d{1,2}) (\d{4})", s)
            if not m:
                return None
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def parse_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace("đ", "").replace("VND", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"-?\d+,\d+", s):
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- status

# Folded label -> canonical class. Classes: ok (hoàn thành or đang giao),
# cancel (huỷ), return (trả hàng). Labels not listed are "unknown" and are never guessed.
STATUS_MAP = {
    "xong": "ok", "da xong": "ok", "hoan thanh": "ok", "da hoan thanh": "ok",
    "thanh cong": "ok", "da lay": "ok",
    "dang giao": "ok", "dang giao dich": "ok", "dang giao hang": "ok",
    "huy": "cancel", "da huy": "cancel", "huy don": "cancel", "da huy don": "cancel",
    "doi tra": "return", "tra hang": "return", "da tra hang": "return",
    "hoan tra": "return", "da hoan tra": "return", "tra lai": "return",
    "da hoan hang": "return", "hoan hang": "return", "hang tra ve": "return",
}


def classify_status(v):
    if v is None:
        return "ok"  # blank inside a status column: rulebook §8, same as the group's first row
    key = re.sub(r"[^a-z ]", " ", fold(v))
    key = re.sub(r"\s+", " ", key).strip()
    return STATUS_MAP.get(key, "unknown")


# ---------------------------------------------------------------- header mapping

DATE_EXACT = ["ngay dat hang", "ngay ban", "ngay ban hang", "ngay hoa don", "ngay giao dich",
              "thoi gian", "ngay", "ngay thang", "ngay dat", "thoi gian tao don", "ngay tao"]
DATE_EXCLUDE = ["giao", "nhap", "tra", "thanh toan", "han", "sinh", "dang ky", "cap nhat",
                "het han", "xuat", "phieu", "tao"]
PRODUCT_EXACT = ["ten hang", "ten san pham", "noi dung", "mat hang", "ten mat hang",
                 "ten hang hoa", "ma hang", "ma sp", "id sp", "hang hoa", "san pham"]
LINE_AMOUNT = ["thanh tien", "tong tien", "ttien", "thanh tien sau giam"]
ORDER_AMOUNT = ["tong tien hang", "tong tien", "thanh tien"]
OID_RE = re.compile(r"\b(ma don|ma don hang|ma hoa don|ma bill|ma hd|so hoa don|ma giao dich)\b")
STATUS_HEADER = ("trang thai", "status", "ket qua")


def colmap(headers):
    f = [fold(h).replace(".", "") for h in headers]
    cm = {}
    for pat in DATE_EXACT:
        if pat in f:
            cm["date"] = f.index(pat)
            break
    if "date" not in cm:
        for i, h in enumerate(f):
            if "ngay" in h and not any(x in h for x in DATE_EXCLUDE):
                cm["date"] = i
                break
    for i, h in enumerate(f):
        if (h == "status" or any(k in h for k in STATUS_HEADER)) and "hddt" not in h \
                and "giao" not in h:
            cm["status"] = i
            break
    for i, h in enumerate(f):
        if OID_RE.search(h) and "nhap" not in h and "ncc" not in h:
            cm["oid"] = i
            break
    for i, h in enumerate(f):
        if h in PRODUCT_EXACT:
            cm["product"] = i
            break
    amounts = LINE_AMOUNT if "product" in cm else ORDER_AMOUNT
    for pat in amounts:
        if pat in f:
            cm["amount"] = f.index(pat)
            break
    return cm


def locate_header(table):
    """Find the header row among the first 15 rows (title rows are skipped)."""
    cands = [table["headers"]] + table["rows"][:15]
    best = None
    for k, row in enumerate(cands):
        hs = ["" if h is None else str(h) for h in row]
        cm = colmap(hs)
        score = len(cm) + (10 if ("date" in cm and "amount" in cm) else 0)
        if best is None or score > best[0]:
            best = (score, k, cm)
    score, k, cm = best
    if "date" not in cm or "amount" not in cm:
        return None
    body = table["rows"] if k == 0 else table["rows"][k:]
    headers = [("" if h is None else str(h)) for h in (table["headers"] if k == 0 else cands[k])]
    return cm, body, headers


def find_status_column(cm, headers, body):
    """Return (index, source). Header match first; otherwise a column whose values are all
    known status labels (rulebook §8). Header text inside the body is ignored."""
    if "status" in cm:
        return cm["status"], "header"
    used = {cm.get("date"), cm.get("amount"), cm.get("oid"), cm.get("product")}
    ncols = len(headers)
    for i in range(ncols):
        if i in used:
            continue
        vals = set()
        for r in body:
            if i < len(r) and r[i] is not None:
                vals.add(fold(r[i]))
        vals.discard(fold(headers[i]) if headers[i] else "")
        if 1 <= len(vals) <= 12 and all(classify_status(v) != "unknown" for v in vals):
            return i, "values"
    return None, "none"


# ---------------------------------------------------------------- business facts

def readme_data_end(readme):
    """Data end from a 'đến hết dd/mm/yyyy' phrase in the README. None if absent."""
    f = fold(readme)
    m = re.search(r"den het (\d{1,2})/(\d{1,2})/(\d{4})", f)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def build_facts(folder):
    """Candidate sales tables for one business, grouped by grain and column meaning.

    group = {"kind": "line"|"order", "key": str, "sources": [...], "has_oid": bool,
             "status_source": "header"|"values"|"none", "status_header": str|None,
             "facts": [(date, status_class, amount, order_key), ...]}
    status_class is ok | cancel | return | unknown.
    """
    tables, readme = read_business(folder)
    groups = {}
    for t in tables:
        if t.get("error"):
            continue
        located = locate_header(t)
        if located is None:
            continue
        cm, body, headers = located
        kind = "line" if "product" in cm else "order"
        st_idx, st_src = find_status_column(cm, headers, body)
        key_parts = (kind, fold(headers[cm["date"]]), fold(headers[cm["amount"]]),
                     fold(headers[cm["oid"]]) if "oid" in cm else "",
                     fold(headers[st_idx]) if st_idx is not None else "")
        g = groups.setdefault(key_parts, {
            "kind": kind, "key": " | ".join(key_parts), "sources": [], "has_oid": "oid" in cm,
            "status_source": st_src,
            "status_header": headers[st_idx] if st_idx is not None else None,
            "facts": [], "seen": set()})
        src = t["source"] + "|" + str(t["name"])
        g["sources"].append(src)
        for i, row in enumerate(body):
            date = parse_date(row[cm["date"]]) if cm["date"] < len(row) else None
            if date is None:
                continue
            oid = None
            if "oid" in cm and cm["oid"] < len(row) and row[cm["oid"]] is not None:
                oid = str(row[cm["oid"]]).strip()
            if oid is not None:
                k = (oid, tuple(str(x) for x in row))
                if k in g["seen"]:
                    continue  # identical row inside the same order: count once (§8)
                g["seen"].add(k)
            if st_idx is None:
                cls = "ok"  # no status column: rulebook §8 says the note's default is hoàn thành
            else:
                cls = classify_status(row[st_idx]) if st_idx < len(row) else "ok"
            amount = parse_num(row[cm["amount"]]) if cm["amount"] < len(row) else None
            okey = oid if oid is not None else f"{src}#{i}"
            g["facts"].append((date, cls, amount or 0.0, okey))
    out = []
    for g in groups.values():
        g.pop("seen", None)
        out.append(g)
    return {"groups": out, "data_end": readme_data_end(readme)}


def load_business(split_dir, biz, cache_dir):
    cpath = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cpath = os.path.join(cache_dir, f"{biz}_{CODE_VERSION}.pkl")
        if os.path.exists(cpath):
            with open(cpath, "rb") as fh:
                return pickle.load(fh)
    data = build_facts(os.path.join(split_dir, "data", biz))
    if cpath:
        with open(cpath, "wb") as fh:
            pickle.dump(data, fh)
    return data


# ---------------------------------------------------------------- question handling

BLACKLIST = ["%", "phan tram", "tang", "giam", "ti le", "tyle", "so voi", "chenh", "trung binh",
             "bang bao nhieu", "khac", " hon ", "huy", "tra hang", "cong no", "khach", " lai ",
             "gia von", "nhap", " ton ", "hang nao", "mat hang", "ngay nao", "nhan vien", "gio",
             " quy ", "tuan", " no ", "san pham", " mon ", " lo ", "ban chay", "nhieu nhat",
             "it nhat", "cao nhat", "thap nhat", " loi ", " gia ", "gan nhat", "ngay gan",
             "tong so", "moi ngay", "so ngay",
             # periods or filters this baseline does not parse
             "dau nam", "tu dau", "den het", "het hom nay", "lien nhau", "lien tiep", "lan luot",
             "theo thu tu", "liet ke", "ca sang", "ca chieu", " ca ", "zalo", "cod", "tien mat",
             "chuyen khoan", "ghi no", "tra bang", "tren ", "duoi ", "cac thang",
             "trong nam", "quy "]
ENTITY_RE = re.compile(r"\bnao\b|\bnhat\b")


def parse_period(f, as_of):
    """Return (start, end, relative) or None."""
    m = re.search(r"ngay (\d{1,2})[/-](\d{1,2})[/-](\d{4})", f) or \
        re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])", f)
    if m:
        try:
            d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d, d, False
        except ValueError:
            return None
    m = re.search(r"thang (\d{1,2})\s*[/-]\s*(\d{4})", f) or \
        re.search(r"thang (\d{1,2}) nam (\d{4})", f)
    if m:
        y, mo = int(m.group(2)), int(m.group(1))
    else:
        m2 = re.search(r"thang (\d{1,2})(?![\d/\-])", f)
        if m2 and "thang nay" not in f:
            mo = int(m2.group(1))
            y = as_of.year if mo <= as_of.month else as_of.year - 1
        elif "thang nay" in f:
            return as_of.replace(day=1), as_of, True
        else:
            if "hom nay" in f:
                return as_of, as_of, True
            if "hom qua" in f:
                d = as_of - datetime.timedelta(days=1)
                return d, d, True
            return None
    if not 1 <= mo <= 12:
        return None
    start = datetime.date(y, mo, 1)
    nxt = datetime.date(y + (mo == 12), mo % 12 + 1, 1)
    return start, nxt - datetime.timedelta(days=1), False


PERIOD_RE = re.compile(r"thang \d{1,2}(\s*[/-]\s*\d{4}| nam \d{4})?|\d{1,2}/\d{4}|\d{1,2}/\d{1,2}/\d{4}|"
                       r"ngay \d{1,2}|hom nay|hom qua|thang nay")
INTENT_WORDS = {"doanh", "thu", "dthu", "so", "don", "bao", "nhieu", "bnhiu", "bnhieu", "nhiu"}
TEMPLATE_WORDS = {"cua", "hang", "cho", "toi", "minh", "hoi", "la", "tong", "trong", "nam", "thang",
                  "ngay", "hom", "nay", "qua", "bn", "coi", "giup", "xem", "check", "gium", "biet",
                  "ban", "bieets", "gi", "duoc", "vay", "a", "ah", "nhe", "ko", "khong", "co", "hnay",
                  "tai", "ca", "cac", "may", "the", "ra", "lan", "ve", "den", "tu", "giup", "da",
                  "tinh", "cho", "minh", "biet", "ghi", "nao"}


def template_only(f):
    """True if every word left after removing the period and intent words is a template word.
    Product names, categories, thresholds and other filters are not in the template."""
    g = PERIOD_RE.sub(" ", f)
    words = re.findall(r"[a-z]+", g)
    return all(w in INTENT_WORDS or w in TEMPLATE_WORDS for w in words)


def intent(f):
    if any(b in f for b in BLACKLIST) or ENTITY_RE.search(f) or not template_only(f):
        return None
    if "doanh thu" in f:
        return "revenue"
    if re.search(r"so don|bao nhieu don|bao nhieu hoa don|so hoa don", f):
        return "count"
    return None


def pick_group(groups):
    """Gate G2: one sales table of the preferred grain (line-level if any exists)."""
    if not groups:
        return None, "no sales table with a date and an amount column"
    lines = [g for g in groups if g["kind"] == "line"]
    pool = lines if lines else groups
    if len(pool) > 1:
        return None, f"{len(pool)} candidate sales tables of the same grain"
    return pool[0], None


def answer_question(q, data, as_of):
    """Return (answer dict, decision dict). answer type is number | unanswerable | abstain."""
    gates = {}
    f = fold_question(q["question"])
    kind = intent(f)
    if kind is None:
        return {"type": "abstain"}, {"gates": gates, "reason": "G1: unsupported question"}
    period = parse_period(f, as_of)
    if period is None:
        return {"type": "abstain"}, {"gates": gates, "reason": "G1: no supported period"}
    if len(re.findall(r"thang \d{1,2}|\b\d{1,2}/\d{4}\b", f)) > 1:
        return {"type": "abstain"}, {"gates": gates, "reason": "G1: more than one period"}
    start, end, relative = period
    gates["G1"] = True

    group, why = pick_group(data["groups"])
    if group is None:
        return {"type": "abstain"}, {"gates": gates, "reason": "G2: " + why}
    gates["G2"] = True
    if kind == "count" and not group["has_oid"]:
        return {"type": "abstain"}, {"gates": gates, "reason": "G3: no order id column"}
    gates["G3"] = True

    if data["data_end"] is not None:
        data_end, data_end_src = data["data_end"], "readme"
    else:
        dates = [x[0] for x in group["facts"]]
        data_end, data_end_src = (max(dates) if dates else None), "fallback_max_date"
    info = {"table": group["key"], "sources": group["sources"][:5],
            "status_source": group["status_source"], "status_header": group["status_header"],
            "data_end": data_end.isoformat() if data_end else None,
            "data_end_source": data_end_src}
    if data_end is None:
        return {"type": "abstain"}, {"gates": gates, "info": info, "reason": "no dated rows"}

    dates = [x[0] for x in group["facts"]]
    data_start = min(dates) if dates else None
    info["data_start"] = data_start.isoformat() if data_start else None
    if data_start is not None and end < data_start:
        # rulebook §7: the period lies wholly before the shop's dated records
        return ({"type": "unanswerable",
                 "reason": f"kỳ {start.isoformat()} trước dữ liệu (từ {data_start.isoformat()})"},
                {"gates": gates, "info": info, "reason": "period before first dated row"})

    if start > data_end:
        if data_end_src != "readme":
            return {"type": "abstain"}, {"gates": gates, "info": info,
                                         "reason": "G5: period after data end, but data end is a fallback"}
        return ({"type": "unanswerable",
                 "reason": f"kỳ {start.isoformat()} nằm ngoài dữ liệu (đến {data_end.isoformat()})"},
                {"gates": gates, "info": info, "reason": "period after README data end"})
    gates["G5"] = True

    upper = min(end, data_end)
    if relative:
        upper = min(upper, as_of)
    in_period = [x for x in group["facts"] if start <= x[0] <= upper]
    if any(x[1] == "unknown" for x in in_period):
        return {"type": "abstain"}, {"gates": gates, "info": info,
                                     "reason": "G4: unmapped status label in period"}
    gates["G4"] = True
    rows = [x for x in in_period if x[1] == "ok"]
    if kind == "revenue":
        value = int(math.floor(sum(x[2] for x in rows) + 0.5))
    else:
        value = len({x[3] for x in rows})
    info["rows_in_period"] = len(in_period)
    info["rows_counted"] = len(rows)
    return {"type": "number", "answer": value}, {"gates": gates, "info": info,
                                                 "reason": "all gates passed"}


def run(input_dir, output_path, log_dir, cache_dir, only_biz=None):
    rb_path = os.path.join(input_dir, "rulebook.md")
    with open(rb_path, "rb") as fh:
        rb_sha = hashlib.sha256(fh.read()).hexdigest()
    questions = [json.loads(l) for l in open(os.path.join(input_dir, "questions.jsonl"),
                                             encoding="utf-8")]
    os.makedirs(log_dir, exist_ok=True)
    out_lines = []
    decisions = []
    stats = {"answered": 0, "abstain": 0, "unanswerable": 0, "errors": 0}
    cache = {}
    for q in questions:
        if only_biz and q["business_id"] not in only_biz:
            continue
        as_of = datetime.date.fromisoformat(q["as_of_date"])
        biz = q["business_id"]
        try:
            if biz not in cache:
                cache[biz] = load_business(input_dir, biz, cache_dir)
            ans, dec = answer_question(q, cache[biz], as_of)
        except Exception as exc:  # never drop a question
            ans, dec = {"type": "abstain"}, {"gates": {}, "reason": f"error: {exc}"}
            stats["errors"] += 1
        if ans["type"] == "abstain":
            stats["abstain"] += 1
            line = {"id": q["id"], "type": "abstain"}
        elif ans["type"] == "unanswerable":
            stats["unanswerable"] += 1
            line = {"id": q["id"], "type": "unanswerable", "reason": ans["reason"]}
        else:
            stats["answered"] += 1
            line = {"id": q["id"], "type": "number", "answer": ans["answer"]}
        out_lines.append(line)
        decisions.append({"id": q["id"], "business_id": biz, "question": q["question"],
                          "as_of_date": q["as_of_date"], "decision": line["type"],
                          "answer": ans.get("answer"), **dec})
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    with open(os.path.join(log_dir, "decisions.jsonl"), "w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    summary = {"input": input_dir, "rulebook_sha256": rb_sha, "code_version": CODE_VERSION,
               "questions": len(out_lines), **stats}
    with open(os.path.join(log_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(ROOT, "train"),
                    help="folder with questions.jsonl, rulebook.md and data/ (image: /data)")
    ap.add_argument("--output", default=None,
                    help="submission file (image: /result/submission.jsonl)")
    ap.add_argument("--log-dir", default=None, help="decision logs (image: /result/logs)")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"),
                    help="cache folder for parsed facts; pass '' to disable (image)")
    ap.add_argument("--biz", nargs="*", default=None)
    args = ap.parse_args()
    out = args.output or os.path.join(args.input, "suggested_answers.jsonl")
    logs = args.log_dir or os.path.join(os.path.dirname(os.path.abspath(out)), "logs")
    run(args.input, out, logs, args.cache or None, set(args.biz) if args.biz else None)
