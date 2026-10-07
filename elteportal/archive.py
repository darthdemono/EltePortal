"""A resumable, read-only local archive of a Canvas account."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Any

from . import canvas, http

TERM = re.compile(r"(?P<first>20\d{2})\s*/\s*(?P<second>\d{2,4})\s*/\s*(?P<semester>[12])")
COURSE_CODE = re.compile(r"\b[A-Z]{2}-\d{2}f[A-Z0-9]+\b", re.I)
VERIFIER = re.compile(r"([?&](?:verifier|access_token|token|signature|AWSAccessKeyId|X-Amz-[^=&#]+)=)[^&#\"'\s]+", re.I)


def term_label(course: dict[str, Any]) -> str:
    """Turn Canvas's `2024/25/1` into the offering's Sep/Feb label."""
    text = " ".join(str(course.get(key) or "") for key in
                    ("name", "original_name", "course_code"))
    term = course.get("term") or {}
    text = f"{term.get('name') or ''} {text}"
    match = TERM.search(text)
    if not match:
        return "Unknown term"
    first, second, semester = match.group("first", "second", "semester")
    end_year = int(second) if len(second) == 4 else int(first[:2] + second)
    return f"Sep {first}" if semester == "1" else f"Feb {end_year}"


def course_title(course: dict[str, Any]) -> str:
    """A readable base title; raw Canvas fields remain in course.json."""
    text = str(course.get("original_name") or course.get("name") or course.get("course_code") or "Course")
    text = TERM.sub("", text)
    text = COURSE_CODE.sub("", text)
    text = re.sub(r"[\[\](){}|:_-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return safe_part(text or "Course")


def component(course: dict[str, Any]) -> str:
    code = str(course.get("course_code") or "").upper()
    if code.endswith("EG"):
        return "L+Pr"
    if code.endswith("E"):
        return "L"
    if code.endswith("G"):
        return "Pr"
    name = " ".join(str(course.get(key) or "") for key in ("name", "original_name")).casefold()
    if "practice" in name or "gyakorlat" in name:
        return "Pr"
    if "lecture" in name or "előadás" in name:
        return "L"
    return "L+Pr"


def safe_part(value: str) -> str:
    return re.sub(r"[\\/\x00]", "_", value).strip()[:160] or "unnamed"


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return VERIFIER.sub(r"\1<redacted>", value)
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(_scrub(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _get(call, errors: list[str], label: str, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except (canvas.CanvasError, http.HttpError) as exc:
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return []


def _paths(root: Path, course: dict[str, Any]) -> tuple[Path, Path]:
    relative = Path(course_title(course)) / term_label(course) / component(course)
    return root / "Course" / relative, root / "personal" / relative


def _download(entry: dict[str, Any], target: Path, errors: list[str]) -> bool:
    file_id = entry.get("id")
    name = safe_part(str(entry.get("display_name") or entry.get("filename") or "unnamed"))
    path = target / f"{file_id or 'file'} - {name}"
    if path.exists() and path.stat().st_size:
        return False
    try:
        _, raw, _ = http.request(entry["url"], timeout=180)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".part")
        temp.write_bytes(raw)
        os.replace(temp, path)
        return True
    except (KeyError, http.HttpError, OSError) as exc:
        errors.append(f"file {file_id or name}: {type(exc).__name__}: {exc}")
        return False


def _course_archive(tok: str, root: Path, course: dict[str, Any]) -> dict[str, Any]:
    course_id = int(course["id"])
    public, personal = _paths(root, course)
    errors: list[str] = []
    detail = _get(canvas.get, errors, "course", f"/courses/{course_id}", tok,
                  include=["syllabus_body", "teachers", "total_students"])
    _write(public / "course.json", detail or course)
    _write(public / "catalog-entry.json", course)

    files = _get(canvas.course_files, errors, "files", tok, course_id)
    _write(public / "files.json", files)
    downloaded = sum(_download(entry, public / "files", errors) for entry in files)

    people = _get(canvas.course_people, errors, "people", tok, course_id)
    _write(public / "people.json", people)
    modules = _get(canvas.modules, errors, "modules", tok, course_id)
    _write(public / "modules.json", modules)
    tools = _get(canvas.external_tools, errors, "external-tools", tok, course_id)
    _write(public / "external-tools.json", tools)
    discussions = _get(canvas.discussions, errors, "discussions", tok, course_id)
    _write(public / "discussions.json", discussions)
    for topic in discussions:
        topic_id = topic.get("id")
        if topic_id:
            _write(public / "discussion-views" / f"{topic_id}.json",
                   _get(canvas.discussion_view, errors, f"discussion {topic_id}", tok, course_id, topic_id))

    pages = _get(canvas.course_pages, errors, "pages", tok, course_id)
    _write(public / "pages.json", pages)
    for page in pages:
        slug = page.get("url")
        if slug:
            _write(public / "pages" / f"{safe_part(str(slug))}.json",
                   _get(canvas.course_page, errors, f"page {slug}", tok, course_id, slug))

    assignments = _get(canvas.assignments_with_submission, errors, "assignments", tok, course_id)
    _write(personal / "assignments-and-submissions.json", assignments)
    quizzes = _get(canvas.quizzes, errors, "quizzes", tok, course_id)
    _write(personal / "quizzes.json", quizzes)
    for quiz in quizzes:
        quiz_id = quiz.get("id")
        if quiz_id:
            quiz_dir = personal / "quizzes" / str(quiz_id)
            _write(quiz_dir / "questions.json", _get(canvas.quiz_questions, errors,
                   f"quiz {quiz_id} questions", tok, course_id, quiz_id))
            _write(quiz_dir / "submissions.json", _get(canvas.quiz_submissions, errors,
                   f"quiz {quiz_id} submissions", tok, course_id, quiz_id))

    _write(public / "errors.json", errors)
    return {"course_id": course_id, "path": str(public), "files_downloaded": downloaded,
            "errors": len(errors)}


def archive_account(tok: str, into: str, *, workers: int = 4) -> dict[str, Any]:
    """Archive every enrolled course and the account-only Canvas data locally."""
    root = Path(into)
    root.mkdir(parents=True, exist_ok=True)
    status = root / "archive-status.json"
    courses = canvas.courses(tok)
    _write(status, {"state": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                    "course_total": len(courses), "completed": []})
    account_errors: list[str] = []
    _write(root / "personal" / "profile.json", _get(canvas.get, account_errors, "profile", "/users/self/profile", tok))
    _write(root / "personal" / "enrollments.json", _get(canvas.enrollments, account_errors, "enrollments", tok))
    for scope in ("inbox", "sent", "archived"):
        rows = _get(canvas.conversation_list, account_errors, f"inbox {scope}", tok, scope)
        _write(root / "personal" / "inbox" / f"{scope}.json", rows)
        for row in rows:
            conversation_id = row.get("id")
            if conversation_id:
                _write(root / "personal" / "inbox" / scope / f"{conversation_id}.json",
                       _get(canvas.conversation, account_errors, f"conversation {conversation_id}", tok, conversation_id))
    _write(root / "personal" / "account-errors.json", account_errors)

    completed: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_course_archive, tok, root, course) for course in courses]
        for future in as_completed(futures):
            completed.append(future.result())
            _write(status, {"state": "running", "course_total": len(courses), "completed": completed})
    result = {"state": "complete", "course_total": len(courses), "courses": completed,
              "account_errors": len(account_errors)}
    _write(status, result)
    return result
