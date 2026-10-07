"""tanrend.elte.hu - the public timetable, no login.

tanrend is the authority on when and where a class meets. It is not the
authority on how full it is: headcount, ranking and "does not start" live only
in Neptun, and tanrend's Létszám column is a different number from Neptun's
limit. Do not read one as the other.

The endpoint is a PHP page returning an HTML table, so this parses HTML. It has
no API and no JSON.
"""
from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from . import http
from .config import load_profile, profile

BASE = load_profile()["timetable"]["url"]  # compatibility shim
MODES = {
    "code": "keres_kod_azon",       # by subject code, e.g. IP-18fAA2E
    "teacher": "keres_okt",         # by teacher name
    "subject": "keres_tanrend",     # by subject name
}
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def _clean(cell: str) -> str:
    return html.unescape(TAG.sub(" ", cell)).replace("\xa0", " ").strip()


def search(term: str, query: str, *, mode: str = "code") -> list[dict[str, Any]]:
    """term is '2026-2027-1'. Returns one dict per timetable row."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    url = f"{profile()['timetable']['url']}?m={MODES[mode]}&f={term}&k={urllib.parse.quote(query)}"
    page = http.get_text(url)
    rows = [[_clean(c) for c in CELL.findall(block)] for block in ROW.findall(page)]
    rows = [r for r in rows if r]
    if not rows:
        return []
    header, *body = rows
    out = []
    for row in body:
        if len(row) < 2:
            continue
        record = {header[i] if i < len(header) else f"col{i}": value
                  for i, value in enumerate(row)}
        record["_query"] = query
        out.append(record)
    return out


def term_from_label(label: str) -> str:
    """'2026/27/1' -> '2026-2027-1', which is what the query string wants."""
    match = re.match(r"(\d{4})/(\d{2,4})/(\d)", label.strip())
    if not match:
        return label
    start, end, half = match.groups()
    end_full = end if len(end) == 4 else start[:2] + end
    return f"{start}-{end_full}-{half}"
