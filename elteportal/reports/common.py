"""Shared pieces for the Canvas reports: dates, terms, frontmatter, file writing."""
from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("ELTE_REPORT_TIMEZONE", "Europe/Budapest"))
ARCHIVE = os.environ.get("ELTE_REPORT_DIR") or os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "elteportal", "reports")

# Canvas course codes often begin with the ELTE term, e.g. "2026/27/1 IP-... - ..."
TERM = re.compile(r"\b(\d{4}/\d{2}/\d)\b")


def parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local(value: str | None) -> dt.datetime | None:
    stamp = parse_utc(value)
    return stamp.astimezone(TZ) if stamp else None


def human(value: str | None) -> str:
    stamp = local(value)
    return stamp.strftime("%d-%m-%Y %H:%M") if stamp else "no due date"


def day(value: str | None) -> str:
    stamp = local(value)
    return stamp.strftime("%d-%m-%Y") if stamp else "-"


def term_of(course: dict) -> str:
    m = TERM.search(course.get("course_code", "") or "")
    return m.group(1) if m else "unknown term"


def frontmatter(title: str, description: str, tags: list[str]) -> str:
    now = dt.datetime.now(TZ).replace(microsecond=0)
    lines = [
        "---",
        f"title: {title}",
        f"datetime: {now.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"description: {description}",
        "tags:",
        *[f"  - {t}" for t in tags],
        f"generated: elteportal.reports, {now.strftime('%d-%m-%Y %H:%M')}",
        "---",
        "",
    ]
    return "\n".join(lines)


VOLATILE = ("datetime:", "generated:")


def _substance(text: str) -> str:
    """The report minus the lines that change on every run regardless of content."""
    return "\n".join(
        line for line in text.splitlines() if not line.startswith(VOLATILE)
    )


def write(path: str, body: str) -> bool:
    """Write the report atomically. Returns False if nothing actually changed.

    Every run restamps `datetime` and `generated`, so a byte comparison would call
    each run a change and, on a daily timer, spend a Nextcloud file version a day
    saying nothing happened. The comparison therefore ignores those two lines: if
    the substance matches, the file is left alone, mtime included.
    """
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                if _substance(fh.read()) == _substance(body):
                    return False
        except (OSError, UnicodeDecodeError):
            # unreadable or not UTF-8 - fall through and rewrite it. A decode error
            # is not an OSError, and catching only OSError crashed the whole report.
            pass

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True
