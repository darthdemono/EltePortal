"""Live standing per course, and the gap between what is marked and what is missing.

Neptun only shows a grade once the semester is over. Canvas carries a running
score the whole way through, which is the only in-semester signal there is.

Two things this refuses to do:

**It never converts a percentage into a Neptun 1-5 grade.** ELTE does not map them
in Canvas - every `grade` field comes back null - so any letter or number grade
here would be invented. The percentage is all there is.

**It deduplicates by course id.** Canvas returns one enrolment row per section, so
a course taken as both lecture and practice appears twice with identical scores.
Averaging the raw list silently double-weights those courses.

Read-only. Rewrites its file every run.
"""
from __future__ import annotations

import os
import sys

from .. import canvas as api
from . import common

FILENAME = "Canvas - Grades.md"


def collect(tok: str) -> list[dict]:
    course_list = api.active_courses(tok)
    courses = {c["id"]: c for c in course_list}
    current_term = api.current_term_id(course_list)
    rows: dict[int, dict] = {}
    for e in api.enrollments(tok):
        cid = e.get("course_id")
        if cid is None or cid in rows:
            continue  # one row per course; sections repeat identical scores
        grades = e.get("grades") or {}
        current, final = grades.get("current_score"), grades.get("final_score")
        if current is None and final is None:
            continue
        course = courses.get(cid, {})
        rows[cid] = {
            "course": api.course_label(course) if course else f"course {cid}",
            "term": api.term_name(course) if course else "unknown term",
            "current_term": course.get("enrollment_term_id") == current_term if course else False,
            "current": current,
            "final": final,
            "gap": (current - final) if (current is not None and final is not None) else None,
            "letter": grades.get("current_grade"),
        }
    return sorted(rows.values(), key=lambda r: (not r["current_term"], r["term"], r["course"]))


def render(rows: list[dict]) -> str:
    graded = [r for r in rows if r["current"] is not None]
    # only the live term is actionable - a gap in a finished course is a closed
    # fact, and framing it as "hand something in" would be advice you cannot take
    widest = sorted(
        [r for r in rows if r["gap"] is not None and r["current_term"] and r["gap"] > 0],
        key=lambda r: -r["gap"],
    )[:5]
    past_gaps = sorted(
        [r for r in rows if r["gap"] is not None and not r["current_term"] and r["gap"] > 0],
        key=lambda r: -r["gap"],
    )[:5]

    body = [
        common.frontmatter(
            "Canvas - Grades",
            "Live per-course standing from Canvas: score on graded work against score with unsubmitted work counted as zero.",
            ["Education"],
        ),
        "# Canvas - Grades",
        "",
        f"**{len(graded)} courses carry a score.** Two numbers per course, and the "
        "difference between them is the whole point:",
        "",
        "- **Marked** is Canvas's `current_score` - how the work that has actually "
        "been graded scored.",
        "- **Standing** is Canvas's `final_score` - the same calculation with every "
        "unsubmitted assignment counted as a zero.",
        "",
        "**These are percentages, not official grades.** This Canvas view does not map them to the "
        "1-5 scale in Canvas - the `grade` field is null on every course - so nothing "
        "here converts to a Neptun mark, and it must not be filed as one.",
        "",
        "The current term is listed first. Courses from finished semesters are still "
        "returned by Canvas as active enrolments, so they are marked as past rather "
        "than dropped - their scores are history, and Neptun is the authority on them.",
        "",
        "| Term | Course | Marked | Standing | Gap | |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        cur = f"{r['current']:g}" if r["current"] is not None else "-"
        fin = f"{r['final']:g}" if r["final"] is not None else "-"
        gap = f"{r['gap']:g}" if r["gap"] is not None else "-"
        mark = "current" if r["current_term"] else "past"
        body.append(
            f"| {r['term']} | {r['course']} | {cur} | {fin} | {gap} | {mark} |"
        )
    body.append("")

    body += ["## Where the most is left on the table ^gap", ""]
    if widest:
        body += [
            "A wide gap is unsubmitted work, not bad marks. These are the courses in "
            "the current term where handing something in moves the number furthest:",
            "",
            "| Course | Marked | Standing | Gap |",
            "|---|---|---|---|",
        ]
        for r in widest:
            body.append(
                f"| {r['course']} | {r['current']:g} | {r['final']:g} | {r['gap']:g} |"
            )
    else:
        body.append(
            "Nothing actionable. The current term has no graded work yet, so there is "
            "no gap to close."
        )
    body += [
        "",
        "The itemised list of what is outstanding is in [[Canvas - Missing Work]].",
        "",
    ]

    if past_gaps:
        body += [
            "## What the gaps looked like last term ^history",
            "",
            "Closed, not a to-do list. Kept because it is the clearest record of how "
            "the previous semester actually went: every row is a course where the work "
            "that got marked scored well above the work that got handed in.",
            "",
            "| Course | Marked | Standing | Gap |",
            "|---|---|---|---|",
        ]
        for r in past_gaps:
            body.append(
                f"| {r['course']} | {r['current']:g} | {r['final']:g} | {r['gap']:g} |"
            )
        body.append("")

    body += [
        "## What this is not ^caveats",
        "",
        "Canvas scores are set by each course's own weighting and say nothing about "
        "whether the course is passed - that is Neptun's answer, and it arrives at "
        "the end of the semester. A course can sit at 100 marked and still fail on a "
        "requirement Canvas never sees, such as attendance or a signature. Treat this "
        "as a progress signal, not a transcript.",
        "",
        "Use your institution's official student record for confirmed grades.",
        "",
    ]
    return "\n".join(body)


def run() -> int:
    tok = api.token()
    if not tok:
        print("CANVAS_API_KEY is not set in the environment", file=sys.stderr)
        return 1
    rows = collect(tok)
    path = os.path.join(common.ARCHIVE, FILENAME)
    changed = common.write(path, render(rows))
    print(f"grades: {len(rows)} courses -> {path} ({'updated' if changed else 'unchanged'})")
    return 0
