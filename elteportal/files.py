"""Open directory and static course file readers for profile-listed servers."""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Iterator

from . import http
from .config import load_profile, profile

WORNOX = load_profile()["files"]["wornox"] + "/"  # compatibility shim
WEBPROG = load_profile()["files"]["webprog"]
HREF = re.compile(r'href="([^"]+)"', re.I)
SORT_LINKS = ("?C=", "/Materials/")


def walk(root: str | None = None, *, max_depth: int = 5) -> Iterator[dict[str, Any]]:
    """Depth-first walk of an Apache autoindex. Yields one dict per file."""
    root = root or profile()["files"]["wornox"] + "/"
    seen: set[str] = set()

    def visit(url: str, depth: int) -> Iterator[dict[str, Any]]:
        if url in seen or depth > max_depth:
            return
        seen.add(url)
        try:
            page = http.get_text(url)
        except http.HttpError:
            return
        for href in HREF.findall(page):
            if href.startswith("?") or href.startswith("/"):
                continue
            full = urllib.parse.urljoin(url, href)
            if not full.startswith(root):
                continue
            if full.endswith("/"):
                yield from visit(full, depth + 1)
            else:
                yield {
                    "url": full,
                    "path": urllib.parse.unquote(full[len(root):]),
                    "name": urllib.parse.unquote(full.rsplit("/", 1)[-1]),
                    "dir": urllib.parse.unquote(full[len(root):].rsplit("/", 1)[0]),
                }

    yield from visit(root, 0)


def fetch(url: str, target_dir: str, *, name: str | None = None) -> str:
    os.makedirs(target_dir, exist_ok=True)
    filename = name or urllib.parse.unquote(url.rsplit("/", 1)[-1])
    safe = filename.replace("/", "_").replace("\\", "_")
    if safe in ("", ".", ".."):
        raise ValueError("invalid download filename")
    path = os.path.join(target_dir, safe)
    _, raw, _ = http.request(url, timeout=180)
    with open(path, "wb") as handle:
        handle.write(raw)
    return path


# ------------------------------------------------------------------ webprogramozas

YAML_ITEM = re.compile(r"^-\s+name:\s*(.+)$", re.M)


def webprog_subjects() -> list[dict[str, str]]:
    return _mini_yaml(http.get_text(f"{profile()['files']['webprog']}/data/subjects.yaml"))


def webprog_materials() -> list[dict[str, str]]:
    return _mini_yaml(http.get_text(f"{profile()['files']['webprog']}/data/materials.yaml"))


def webprog_page(name: str) -> str:
    """e.g. 'subjects/webprog-eng' -> the markdown behind #!/subjects/webprog-eng."""
    return http.get_text(f"{profile()['files']['webprog']}/pages/{name}.md")


def webprog_lectures(prefix: str = "webprog/lectures", *, limit: int = 20) -> list[dict[str, Any]]:
    """Enumerate the reveal.js decks. `?print-pdf` on any of these prints."""
    out = []
    for index in range(1, limit + 1):
        url = f"{profile()['files']['webprog']}/{prefix}/{index:02d}/index.eng.html"
        ok, status = http.reachable(url)
        if not ok or status != "200":
            continue
        title = ""
        try:
            match = re.search(r"<title>(.*?)</title>", http.get_text(url), re.S | re.I)
            title = match.group(1).strip() if match else ""
        except http.HttpError:
            pass
        out.append({"n": index, "url": url, "print_pdf": url + "?print-pdf", "title": title})
    return out


def _mini_yaml(text: str) -> list[dict[str, str]]:
    """These two files are flat lists of `key: value` blocks. Nothing deeper."""
    items: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("- "):
            if current:
                items.append(current)
            current = {}
            line = line[2:]
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        current[key.strip().lstrip("- ")] = value.strip().strip('"')
    if current:
        items.append(current)
    return [i for i in items if i]
