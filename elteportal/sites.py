"""Probe the optional sites listed by the selected institution profile."""
from __future__ import annotations

from typing import Any

from . import http
from .config import load_profile, profile

SITES: list[dict[str, Any]] = load_profile()["sites"]["entries"]  # compatibility shim

AUTH_NOTES = {
    "canvas-token": "CANVAS_API_KEY, a personal access token minted in Canvas settings.",
    "neptun+totp": "Neptun code, password and a TOTP code. Locks after repeated failures.",
    "inf-account": "A separate department account; check the institution's instructions.",
    "elte-sso": "Central ELTE single sign-on.",
}


def probe(keys: list[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for site in profile()["sites"]["entries"]:
        if keys and site["key"] not in keys:
            continue
        ok, status = http.reachable(site["url"])
        rows.append({**site, "auth": site.get("auth"), "reachable": ok, "status": status,
                     "auth_note": AUTH_NOTES.get(site.get("auth") or "")})
    return rows
