"""Read csv, xlsx and sqlite files of one business into plain row dicts. Standard library only.

Each source table is returned as Sheet(file, sheet, header, rows). Rows are dicts keyed by the header text
(stripped). Cell values are str, int, float or None. Excel date cells stay numeric; ingest converts them.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


@dataclass
class Sheet:
    file: str
    sheet: str
    header: list[str]
    rows: list[dict] = field(default_factory=list)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode csv")


def _number(text: str):
    try:
        if text.strip() == "":
            return None
        f = float(text)
        return int(f) if f.is_integer() else f
    except ValueError:
        return text


def read_csv(path: Path) -> list[Sheet]:
    text = _decode(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    header = [h.strip() for h in rows[0]]
    sheet = Sheet(path.name, "", header)
    for r in rows[1:]:
        sheet.rows.append({h: (_number(c) if c.strip() != "" else None)
                           for h, c in zip(header, r + [""] * (len(header) - len(r)))})
    return [sheet]


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(path: Path) -> list[Sheet]:
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in sst.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    out: list[Sheet] = []
    for s in wb.find("m:sheets", NS):
        rid = s.get(f"{{{NS['r']}}}id")
        part = target[rid].lstrip("/")
        if not part.startswith("xl/"):
            part = "xl/" + part
        root = ET.fromstring(z.read(part))
        grid: list[list] = []
        for row in root.iter(f"{{{NS['m']}}}row"):
            cells: dict[int, object] = {}
            for c in row.findall("m:c", NS):
                idx = _col_index(c.get("r"))
                kind = c.get("t")
                v = c.find("m:v", NS)
                if kind == "s" and v is not None:
                    val = shared[int(v.text)]
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                elif v is not None and v.text is not None:
                    val = v.text if kind in ("str", "b", "e") else _number(v.text)
                else:
                    val = None
                cells[idx] = val
            if cells:
                width = max(cells) + 1
                grid.append([cells.get(i) for i in range(width)])
        grid = [r for r in grid if any(c not in (None, "") for c in r)]
        if not grid:
            out.append(Sheet(path.name, s.get("name"), []))
            continue
        header = [str(h).strip() if h is not None else "" for h in grid[0]]
        sheet = Sheet(path.name, s.get("name"), header)
        for r in grid[1:]:
            r = r + [None] * (len(header) - len(r))
            sheet.rows.append({h: r[i] for i, h in enumerate(header) if h})
        out.append(sheet)
    return out


def read_sqlite(path: Path) -> list[Sheet]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out: list[Sheet] = []
        for name in names:
            cur = con.execute(f'SELECT * FROM "{name}"')
            header = [d[0] for d in cur.description]
            sheet = Sheet(path.name, name, header)
            for r in cur:
                sheet.rows.append(dict(zip(header, r)))
            out.append(sheet)
        return out
    finally:
        con.close()


def read_business(folder: Path, files: list[str] | None = None) -> list[Sheet]:
    """Read every data file in a business folder (README excluded), or only the named files."""
    sheets: list[Sheet] = []
    for p in sorted(folder.iterdir()):
        if files is not None and p.name not in files:
            continue
        suffix = p.suffix.lower()
        if suffix == ".csv":
            sheets += read_csv(p)
        elif suffix == ".xlsx":
            sheets += read_xlsx(p)
        elif suffix in (".sqlite", ".db"):
            sheets += read_sqlite(p)
    return sheets
