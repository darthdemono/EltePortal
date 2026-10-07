"""What has not been handed in, and what it is worth.

Canvas already computes a `missing` flag per submission, but it is only visible one
course at a time and it says nothing about how much of the grade is still sitting
on the table. This pulls every active course into one list, splits it into what is
already overdue and what is still due, and prices both.

The distinction that makes the numbers make sense: `current_score` counts only
graded work, `final_score` counts ungraded work as zero. The gap between them is
exactly the unsubmitted work below.

Read-only. Rewrites its file every run.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

from .. import canvas as api
from . import common

FILENAME = "Canvas - Missing Work.md"


def classify(assignment: dict, now: dt.datetime) -> str | None:
    """Return 'overdue', 'due', or None if it needs no action.

    An assignment counts only if it is published, not excused, and carries no
    submission. Canvas's own `missing` flag is trusted where it is set, because it
    also knows about per-student due-date overrides this code cannot see.
    """
    if not assignment.get("published", True):
        return None
    sub = assignment.get("submission") or {}
    if sub.get("excused"):
        return None
    if sub.get("workflow_state") in ("submitted", "graded", "pending_review"):
        return None
    if sub.get("submitted_at"):
        return None

    due = common.local(assignment.get("due_at"))
    if sub.get("missing"):
        return "overdue"
    if due is None:
        return "due"  # no deadline set - still outstanding, just not late
    return "overdue" if due < now else "due"


def collect(tok: str) -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    courses = api.active_courses(tok)
    current = api.current_term_id(courses)
    for course in courses:
        label = api.course_label(course)
        try:
            assignments = api.assignments_with_submission(tok, course["id"])
        except api.CanvasError as exc:
            problems.append(f"{label}: {exc}")
            continue
        now = dt.datetime.now(common.TZ)
        for a in assignments:
            state = classify(a, now)
            if state is None:
                continue
            rows.append(
                {
                    "course": label,
                    "term": api.term_name(course),
                    "current_term": course.get("enrollment_term_id") == current,
                    "name": (a.get("name") or "(untitled)").strip(),
                    "due_at": a.get("due_at"),
                    "due_sort": common.local(a.get("due_at")) or dt.datetime.max.replace(tzinfo=common.TZ),
                    "points": a.get("points_possible") or 0.0,
                    "state": state,
                }
            )
    return rows, problems


def table(rows: list[dict]) -> list[str]:
    out = ["| Due | Course | Assignment | Points |", "|---|---|---|---|"]
    for r in rows:
        pts = f"{r['points']:g}" if r["points"] else "-"
        out.append(
            f"| {common.human(r['due_at'])} | {r['course']} | {r['name']} | {pts} |"
        )
    return out


def render(rows: list[dict], problems: list[str]) -> str:
    live = [r for r in rows if r["current_term"]]
    past = [r for r in rows if not r["current_term"]]
    overdue = sorted([r for r in live if r["state"] == "overdue"], key=lambda r: r["due_sort"])
    due = sorted([r for r in live if r["state"] == "due"], key=lambda r: r["due_sort"])
    past_overdue = sorted(
        [r for r in past if r["state"] == "overdue"], key=lambda r: r["due_sort"]
    )
    term = next((r["term"] for r in live), "the current term")
    lost = sum(r["points"] for r in overdue)
    ahead = sum(r["points"] for r in due)

    body = [
        common.frontmatter(
            "Canvas - Missing Work",
            "Unsubmitted Canvas assignments in the current term, with the points at stake, and the closed backlog from earlier terms kept separate.",
            ["Education"],
        ),
        "# Canvas - Missing Work",
        "",
        f"**In {term}: {len(overdue)} overdue, {len(due)} still to come.** "
        f"Overdue is worth **{lost:g} points**, with **{ahead:g} points** still ahead.",
        "",
        "An assignment counts here only if it is published, not excused, and has no "
        "submission at all. Where Canvas sets its own `missing` flag that is trusted "
        "over the due date, because Canvas also knows about per-student extensions "
        "this cannot see.",
        "",
        "**Only the current term is actionable.** Some institutions leave finished courses in an "
        f"active enrolment state, so Canvas reports {len(past_overdue)} more unsubmitted "
        "items from earlier semesters. Those are closed - Neptun has already graded "
        "them - and they are listed at the bottom as history, not as a to-do list.",
        "",
    ]

    body += [f"## Overdue in {term} ^overdue", ""]
    if overdue:
        body += [
            "Already counted as zeros in the standing score.",
            "",
        ]
        body += table(overdue)
        by_course: dict[str, float] = {}
        for r in overdue:
            by_course[r["course"]] = by_course.get(r["course"], 0.0) + r["points"]
        body += ["", "### Where the overdue points sit", ""]
        body += ["| Course | Points lost |", "|---|---|"]
        for course, pts in sorted(by_course.items(), key=lambda kv: -kv[1]):
            body.append(f"| {course} | {pts:g} |")
    else:
        body.append("Nothing overdue. The term has not run long enough to fall behind.")
    body.append("")

    body += [f"## Still due in {term} ^due", ""]
    if due:
        body += table(due)
    else:
        body.append("Nothing outstanding with a future deadline.")
    body.append("")

    if past_overdue:
        pts = sum(r["points"] for r in past_overdue)
        by_term: dict[str, int] = {}
        for r in past_overdue:
            by_term[r["term"]] = by_term.get(r["term"], 0) + 1
        body += [
            "## Earlier terms, for the record ^history",
            "",
            f"**{len(past_overdue)} unsubmitted items worth {pts:g} points, across "
            f"{', '.join(sorted(by_term))}.** Nothing here can be acted on. It is kept "
            "because it explains the gap between the marked and standing scores in "
            "[[Canvas - Grades]], and because the large single items are exam sittings "
            "rather than homework - a 100-point row is a missed exam, not a missed "
            "worksheet.",
            "",
        ]
        body += table(past_overdue)
        body.append("")

    if problems:
        body += ["## Courses that could not be read ^problems", ""]
        body += [f"- {p}" for p in problems]
        body.append("")

    body += [
        "## How to read the two scores ^scores",
        "",
        "`current_score` counts only work that has been graded. `final_score` counts "
        "ungraded work as zero. The gap between them for any course is the value of "
        "the rows above. A course showing 83 marked and 17 standing is not a course "
        "being marked badly - it is a course with almost everything unsubmitted.",
        "",
        "See [[Canvas - Grades]] for the per-course standing.",
        "",
    ]
    return "\n".join(body)


def run() -> int:
    tok = api.token()
    if not tok:
        print("CANVAS_API_KEY is not set in the environment", file=sys.stderr)
        return 1
    rows, problems = collect(tok)
    path = os.path.join(common.ARCHIVE, FILENAME)
    changed = common.write(path, render(rows, problems))
    live = [r for r in rows if r["current_term"]]
    overdue = sum(1 for r in live if r["state"] == "overdue")
    state = "updated" if changed else "unchanged"
    print(
        f"missing: current term {overdue} overdue, {len(live) - overdue} still due; "
        f"{len(rows) - len(live)} from earlier terms -> {path} ({state})"
    )
    for p in problems:
        print(f"  could not read {p}", file=sys.stderr)
    return 0
