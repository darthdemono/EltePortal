"""Read-only Canvas LMS client for the configured institution.

Read-only by construction: only GET is exposed. Writing to Canvas - a nickname,
a submission - is a deliberate act and does not belong behind something that
looks like a getter.

Two behaviours are worth knowing before reading a result and concluding
something is missing:

* A course that was never published answers 401 on /files and 404 on /pages
  while still appearing in the course list. That is Canvas agreeing with the web
  UI, not a bad token.
* Concluded courses stay readable. Enrolment state has to be asked for
  explicitly or the previous years are invisible.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Iterator

from . import http
from .config import load_profile, profile
from .vault import from_vault, secret

BASE = load_profile()["canvas"]["base_url"]  # compatibility shim
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
STATES = ("active", "completed", "invited_or_pending")


class CanvasError(RuntimeError):
    """A Canvas call failed. Never carries the token."""


def token(*, username: str | None = None) -> str:
    """Resolve the default token, or an exact Canvas account by vault username."""
    if username:
        return from_vault("Canvas", username=username)
    return secret("CANVAS_API_KEY", provider="Canvas API")


def _auth(tok: str) -> dict[str, str]:
    if not tok or not tok.strip():
        raise CanvasError("Canvas token is empty")
    return {"Authorization": f"Bearer {tok}"}


def base_url() -> str:
    override = os.environ.get("CANVAS_BASE_URL")
    if override:
        from .config import _url
        return _url(override, "CANVAS_BASE_URL")
    return profile()["canvas"]["base_url"]


def _url(path: str) -> str:
    base = base_url()
    if path.startswith("http"):
        if urllib.parse.urlsplit(path).netloc != urllib.parse.urlsplit(base).netloc or not path.startswith(base + "/"):
            raise CanvasError("refusing to send a Canvas token to another origin")
        return path
    if not path.startswith("/") or path.startswith("//"):
        raise CanvasError("Canvas path must start with one slash")
    return base + path


def get(path: str, tok: str, **params: Any) -> Any:
    url = _url(path)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    try:
        payload, _ = http.get_json(url, headers=_auth(tok))
    except http.HttpError as exc:
        raise CanvasError(f"{exc.status} for {exc.path}") from None
    return payload


def paged(path: str, tok: str, **params: Any) -> Iterator[dict[str, Any]]:
    """Walk Link: rel=next. Stops on the first page that is not a list."""
    params.setdefault("per_page", 100)
    url = _url(path)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    next_url: str | None = url
    while next_url:
        try:
            payload, head = http.get_json(next_url, headers=_auth(tok))
        except http.HttpError as exc:
            raise CanvasError(f"{exc.status} for {exc.path}") from None
        if not isinstance(payload, list):
            return
        yield from payload
        match = NEXT_LINK.search(head.get("Link", "") or "")
        next_url = _url(match.group(1)) if match else None


def courses(tok: str, *, states: tuple[str, ...] = STATES) -> list[dict[str, Any]]:
    """Every course the account can see, deduplicated by id, newest term first."""
    seen: dict[int, dict[str, Any]] = {}
    for state in states:
        try:
            rows = list(paged("/courses", tok, enrollment_state=state,
                              include=["term", "teachers", "total_students"]))
        except CanvasError:
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                seen.setdefault(row["id"], row)
    return sorted(seen.values(), key=lambda c: c.get("id", 0), reverse=True)


def active_courses(tok: str) -> list[dict[str, Any]]:
    """Active enrolments, deduplicated by course id, with their Canvas term."""
    return courses(tok, states=("active",))


def term_name(course: dict[str, Any]) -> str:
    """The term as Canvas names it, for example ``2026/27/1``."""
    term = course.get("term") or {}
    name = (term.get("name") or "").strip()
    return name or "unknown term"


def current_term_id(courses_: list[dict[str, Any]]) -> int | None:
    """The newest enrolled term, not the unreliable Canvas term end date."""
    ids = [course.get("enrollment_term_id") for course in courses_]
    return max((value for value in ids if isinstance(value, int)), default=None)


def course_label(course: dict[str, Any]) -> str:
    """Use the account's nickname when present, otherwise Canvas's own name."""
    for key in ("nickname", "name", "course_code"):
        value = (course.get(key) or "").strip()
        if value:
            return re.sub(r"\s*\.\s*$", "", value)
    return f"course {course.get('id', '?')}"


def course_files(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/files", tok))


def course_people(tok: str, course_id: int) -> list[dict[str, Any]]:
    """Every user Canvas exposes in a course, with their course enrolments."""
    return list(paged(f"/courses/{course_id}/users", tok, include=["enrollments"]))


def course_pages(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/pages", tok))


def page_body(tok: str, course_id: int, slug: str) -> str:
    payload = get(f"/courses/{course_id}/pages/{urllib.parse.quote(slug)}", tok)
    return payload.get("body") or "" if isinstance(payload, dict) else ""


def course_page(tok: str, course_id: int, slug: str) -> dict[str, Any]:
    payload = get(f"/courses/{course_id}/pages/{urllib.parse.quote(slug)}", tok)
    return payload if isinstance(payload, dict) else {}


def modules(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/modules", tok, include=["items"]))


def assignments(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/assignments", tok))


def assignments_with_submission(tok: str, course_id: int) -> list[dict[str, Any]]:
    """Every course assignment together with the authenticated user's submission."""
    return list(paged(f"/courses/{course_id}/assignments", tok,
                      include=["submission"], order_by="due_at"))


def enrollments(tok: str) -> list[dict[str, Any]]:
    """The account's enrolment rows, including live Canvas scores."""
    return list(paged("/users/self/enrollments", tok))


def announcements(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/discussion_topics", tok, only_announcements=True))


def discussions(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/discussion_topics", tok))


def discussion_view(tok: str, course_id: int, topic_id: int) -> dict[str, Any]:
    payload = get(f"/courses/{course_id}/discussion_topics/{topic_id}/view", tok)
    return payload if isinstance(payload, dict) else {}


def quizzes(tok: str, course_id: int) -> list[dict[str, Any]]:
    """Legacy Canvas quizzes; New Quizzes is an external-tool URL only."""
    return list(paged(f"/courses/{course_id}/quizzes", tok))


def quiz_questions(tok: str, course_id: int, quiz_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/quizzes/{quiz_id}/questions", tok))


def quiz_submissions(tok: str, course_id: int, quiz_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/quizzes/{quiz_id}/submissions", tok))


def conversation_list(tok: str, scope: str) -> list[dict[str, Any]]:
    """Read inbox without Canvas marking any message as read."""
    return list(paged("/conversations", tok, scope=scope, auto_mark_as_read=False))


def conversation(tok: str, conversation_id: int) -> dict[str, Any]:
    payload = get(f"/conversations/{conversation_id}", tok, auto_mark_as_read=False)
    return payload if isinstance(payload, dict) else {}


def external_tools(tok: str, course_id: int) -> list[dict[str, Any]]:
    return list(paged(f"/courses/{course_id}/external_tools", tok))


def upcoming(tok: str) -> list[dict[str, Any]]:
    payload = get("/users/self/upcoming_events", tok)
    return payload if isinstance(payload, list) else []


def download_file(entry: dict[str, Any], target_dir: str) -> str:
    """Fetch one file entry into target_dir. The download URL is pre-signed."""
    name = entry.get("display_name") or entry.get("filename") or "unnamed"
    safe = name.replace("/", "_")
    safe = safe.replace("\\", "_")
    if safe in ("", ".", ".."):
        raise CanvasError("invalid download filename")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, safe)
    _, raw, _ = http.request(entry["url"], timeout=180)
    with open(path, "wb") as handle:
        handle.write(raw)
    return path


LINK = re.compile(r'https?://[^\s"\'<>)\]]+')
SKIP_HOSTS = ("instructure.com",)


def outbound_links(tok: str, course_id: int) -> list[dict[str, str]]:
    """Every non-Canvas URL in a course's prose, with where it was found.

    This is what finds the systems nothing advertises: Panopto recordings,
    progenv, TMS, the faculty document store.
    """
    found: list[dict[str, str]] = []

    def harvest(where: str, html: str | None) -> None:
        for url in LINK.findall(html or ""):
            url = url.rstrip(".,;")
            host = urllib.parse.urlsplit(url).netloc
            if not host or host == urllib.parse.urlsplit(base_url()).netloc or any(skip in host for skip in SKIP_HOSTS):
                continue
            found.append({"host": host, "url": url, "where": where})

    course = get(f"/courses/{course_id}", tok, include=["syllabus_body"])
    if isinstance(course, dict):
        harvest("syllabus", course.get("syllabus_body"))
    for page in _safe(course_pages, tok, course_id):
        slug = page.get("url")
        if slug:
            harvest(f"page:{page.get('title', slug)}", page_body(tok, course_id, slug))
    for item in _safe(assignments, tok, course_id):
        harvest(f"assignment:{item.get('name')}", item.get("description"))
    for item in _safe(announcements, tok, course_id):
        harvest(f"announcement:{item.get('title')}", item.get("message"))
    for module in _safe(modules, tok, course_id):
        for item in module.get("items", []):
            if item.get("type") == "ExternalUrl" and item.get("external_url"):
                found.append({"host": urllib.parse.urlsplit(item["external_url"]).netloc,
                              "url": item["external_url"],
                              "where": f"module:{item.get('title')}"})
    return found


def _safe(fn, tok: str, course_id: int) -> list[dict[str, Any]]:
    try:
        return fn(tok, course_id)
    except CanvasError:
        return []
