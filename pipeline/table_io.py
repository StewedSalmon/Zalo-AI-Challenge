"""Read business data files (csv, xlsx, sqlite) into simple tables.

A table is a dict: {"source": str, "name": str, "headers": [str], "rows": [[value, ...]]}
Values are str, float, int, bool or None. Standard library only.
"""
import csv
import io
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _read_text(path):
    """Decode a text file. BOM first, then utf-8, then cp1258, latin-1 as last resort.

    UTF-16 is only used when a UTF-16 BOM is present: a cp1258 file with an even
    byte count decodes as UTF-16 without error, so it must not be tried blindly.
    """
    raw = open(path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp1258"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read_csv(path):
    text = _read_text(path)
    sample = text[:5000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    body = [[_coerce(c) for c in r] for r in rows[1:]]
    return [{"source": os.path.basename(path), "name": "", "headers": headers, "rows": body}]


def _coerce(v):
    if v is None:
        return None
    s = v.strip() if isinstance(v, str) else v
    if s == "":
        return None
    return s


def _col_index(ref):
    letters = re.match(r"([A-Z]+)", ref).group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _ref_rc(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    return int(m.group(2)), _col_index(ref)


def _merged_ranges(root):
    out = []
    mc = root.find("m:mergeCells", NS)
    if mc is None:
        return out
    for m in mc.findall("m:mergeCell", NS):
        a, b = m.get("ref").split(":")
        r1, c1 = _ref_rc(a)
        r2, c2 = _ref_rc(b)
        out.append((r1, c1, r2, c2))
    return out


def read_xlsx(path):
    """Read every sheet. Merged ranges are filled with their top-left value;
    cells that are blank in the file stay blank (rulebook §8 fills only merged cells)."""
    tables = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for rel in rels:
            target = rel.get("Target").lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rel_map[rel.get("Id")] = target
        for sheet in wb.find("m:sheets", NS):
            sname = sheet.get("name")
            target = rel_map.get(sheet.get("{%s}id" % NS["r"]))
            if target is None or target not in names:
                continue
            root = ET.fromstring(z.read(target))
            cells = {}  # (row, col) -> value
            for row in root.iter("{%s}row" % NS["m"]):
                for c in row.findall("m:c", NS):
                    r, idx = _ref_rc(c.get("r"))
                    t = c.get("t")
                    v = c.find("m:v", NS)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                    elif t in ("str", "e") and v is not None:
                        val = v.text
                    elif t == "b" and v is not None:
                        val = v.text == "1"
                    elif v is not None and v.text is not None:
                        try:
                            f = float(v.text)
                            val = int(f) if f.is_integer() else f  # date cells stay serial numbers
                        except ValueError:
                            val = v.text
                    else:
                        val = None
                    val = _coerce(val)
                    if val is not None:
                        cells[(r, idx)] = val
            for r1, c1, r2, c2 in _merged_ranges(root):
                top = cells.get((r1, c1))
                if top is None:
                    continue
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        cells.setdefault((r, c), top)
            if not cells:
                continue
            max_r = max(r for r, _ in cells)
            max_c = max(c for _, c in cells)
            grid = []
            for r in range(1, max_r + 1):
                row = [cells.get((r, c)) for c in range(max_c + 1)]
                if any(v is not None for v in row):
                    grid.append(row)
            if not grid:
                continue
            headers = [str(h).strip() if h is not None else "" for h in grid[0]]
            tables.append({"source": os.path.basename(path), "name": sname,
                           "headers": headers, "rows": grid[1:]})
    return tables


def read_sqlite(path):
    tables = []
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for tname in names:
            cur = con.execute('SELECT * FROM "%s"' % tname.replace('"', '""'))
            headers = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
            tables.append({"source": os.path.basename(path), "name": tname,
                           "headers": headers, "rows": rows})
    finally:
        con.close()
    return tables


def read_business(folder):
    """Return all tables found in a business folder (recursive), plus README text."""
    tables = []
    readme = ""
    for dirpath, _, files in os.walk(folder):
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            low = fn.lower()
            if low == "readme.md":
                readme = _read_text(p)
                continue
            try:
                if low.endswith(".csv"):
                    tables += read_csv(p)
                elif low.endswith(".xlsx"):
                    tables += read_xlsx(p)
                elif low.endswith((".sqlite", ".db", ".sqlite3")):
                    tables += read_sqlite(p)
            except Exception as exc:  # keep going; the failure is recorded
                tables.append({"source": fn, "name": "", "headers": [],
                               "rows": [], "error": str(exc)})
    return tables, readme
