"""Set the owner's Canvas course nicknames, but only after explicit ``--apply``.

The default is a read-only comparison. This is deliberately outside
``elteportal.canvas`` because that client exposes GET requests only.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import TextIO

from . import canvas, http

NICKNAMES: dict[int, str] = {}  # compatibility shim: caller supplies mapping or archive


def archive_nicknames(root: str) -> dict[int, str]:
    """Derive English, term-qualified nicknames from a normalized local archive."""
    course_root = Path(root) / "Course"
    wanted: dict[int, str] = {}
    for path in course_root.rglob("Canvas - Catalog Entry.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        course_id = entry.get("id")
        relative = path.relative_to(course_root).parts
        if not isinstance(course_id, int) or len(relative) < 4:
            raise ValueError(f"invalid archive record: {path}")
        title, term, component = relative[:3]
        group = next((part for part in relative[3:-1] if part.startswith("Group ")), "")
        number = re.fullmatch(r"Group (\d+)", group)
        if number and 1 <= int(number.group(1)) <= 20:
            component = "Pr"
        elif number and int(number.group(1)) >= 90:
            component = "L"
        base = title
        for suffix in (" L+Pr", " Pr", " L"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        label = f"{base} {component}"
        if course_id in wanted:
            raise ValueError(f"duplicate Canvas course ID {course_id} in {root}")
        wanted[course_id] = f"{label} ({term})"
    if not wanted:
        raise ValueError(f"no Canvas catalog records under {course_root}")
    return wanted


def current(tok: str, course_id: int) -> str | None:
    """Read the current per-user nickname, treating an unpublished course as absent."""
    try:
        payload = canvas.get(f"/users/self/course_nicknames/{course_id}", tok)
    except canvas.CanvasError:
        return None
    return payload.get("nickname") if isinstance(payload, dict) else None


def set_nickname(tok: str, course_id: int, nickname: str, *, apply: bool = False) -> str | None:
    """The sole Canvas write, reached only by the caller's explicit ``--apply``."""
    if not apply:
        raise ValueError("nickname write requires apply=True")
    if not nickname or len(nickname) >= 60:
        raise ValueError("Canvas nickname must be nonempty and shorter than 60 characters")
    body = urllib.parse.urlencode({"nickname": nickname}).encode()
    _, raw, _ = http.request(
        f"{canvas.base_url()}/users/self/course_nicknames/{course_id}",
        method="PUT",
        headers={**canvas._auth(tok), "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    payload = json.loads(raw.decode("utf-8", "replace"))
    return payload.get("nickname") if isinstance(payload, dict) else None


def run(*, nicknames: dict[int, str] | None = None, account: str | None = None,
        apply: bool = False, output: TextIO = sys.stdout) -> int:
    """Compare desired labels, writing nothing unless ``apply`` is true."""
    tok = canvas.token(username=account) if account else canvas.token()
    nicknames = NICKNAMES if nicknames is None else nicknames
    if not nicknames:
        raise ValueError("no nicknames supplied; pass an archive or mapping")
    done = pending = blocked = 0
    for course_id, wanted in sorted(nicknames.items()):
        existing = current(tok, course_id)
        if existing == wanted:
            done += 1
            continue
        if not apply:
            print(f"{course_id}  would set {existing or '(none)'!r} -> {wanted!r}", file=output)
            pending += 1
            continue
        try:
            changed = set_nickname(tok, course_id, wanted, apply=True)
        except http.HttpError as exc:
            if exc.status == 401:
                print(f"{course_id}  blocked: course is not published yet ({wanted!r})", file=output)
            else:
                print(f"{course_id}  failed: HTTP {exc.status}", file=output)
            blocked += 1
            continue
        print(f"{course_id}  {existing or '(none)'!r} -> {changed!r}", file=output)
        pending += 1
    verb = "changed" if apply else "to change"
    print(f"\n{done} already correct, {pending} {verb}, {blocked} blocked (unpublished)", file=output)
    if not apply and pending:
        print("re-run with --apply to write", file=output)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="perform Canvas PUT requests")
    parser.add_argument("--account", help="optional legacy vault account selector")
    parser.add_argument("--archive", help="normalized Canvas archive root")
    args = parser.parse_args(argv)
    if args.archive and not args.account:
        parser.error("--archive requires --account")
    names = archive_nicknames(args.archive) if args.archive else None
    return run(nicknames=names, account=args.account, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
