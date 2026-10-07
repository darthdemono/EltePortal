"""Small provider contract shared by built-ins and installed entry points."""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Protocol

from . import archive, canvas, config, files, neptun, sites, tanrend


class Provider(Protocol):
    def run(self, action: str, **params: Any) -> Any: ...


class CanvasProvider:
    def run(self, action: str, **params: Any) -> Any:
        actions = {
            "courses": lambda: canvas.courses(canvas.token()),
            "files": lambda: archive._scrub(canvas.course_files(canvas.token(), int(params["course"]))),
            "assignments": lambda: canvas.assignments_with_submission(canvas.token(), int(params["course"])),
            "upcoming": lambda: canvas.upcoming(canvas.token()),
        }
        if action not in actions:
            raise ValueError(f"unknown Canvas action {action}")
        return actions[action]()


class NeptunProvider:
    def run(self, action: str, **params: Any) -> Any:
        actions = {
            "probe": lambda: neptun.probe(),
            "creds": lambda: neptun.credential_status(),
            "login": lambda: neptun.outer_login(neptun.portal_login()),
            "logout": lambda: neptun.forget_token(),
            "get": lambda: neptun.call(str(params["endpoint"]), **params.get("query", {})),
        }
        if action not in actions:
            raise ValueError(f"unknown Neptun action {action}")
        return actions[action]()


class TimetableProvider:
    def run(self, action: str, **params: Any) -> Any:
        if action != "search":
            raise ValueError(f"unknown timetable action {action}")
        return tanrend.search(str(params["term"]), str(params["query"]), mode=str(params.get("mode", "code")))


class FilesProvider:
    def run(self, action: str, **params: Any) -> Any:
        if action != "walk":
            raise ValueError(f"unknown files action {action}")
        return list(files.walk(params.get("root")))


class SitesProvider:
    def run(self, action: str, **params: Any) -> Any:
        if action != "probe":
            raise ValueError(f"unknown sites action {action}")
        return sites.probe(params.get("keys"))


class ConfigProvider:
    def run(self, action: str, **params: Any) -> Any:
        if action != "check":
            raise ValueError(f"unknown config action {action}")
        selected = config.profile()
        return {"profile": selected["name"], "schema_version": selected["schema_version"],
                "canvas": bool(selected["canvas"].get("enabled")),
                "neptun_flow": selected["neptun"].get("flow"),
                "secrets_backend": config.settings().get("secrets", {}).get("backend", "env")}


BUILTINS: dict[str, Provider] = {
    "canvas": CanvasProvider(), "neptun": NeptunProvider(),
    "timetable": TimetableProvider(), "files": FilesProvider(), "sites": SitesProvider(),
    "config": ConfigProvider(),
}


def get(name: str) -> Provider:
    if name in BUILTINS:
        return BUILTINS[name]
    matches = entry_points(group="elteportal.providers", name=name)
    if len(matches) != 1:
        raise ValueError(f"unknown provider {name}")
    provider = next(iter(matches)).load()()
    if not callable(getattr(provider, "run", None)):
        raise ValueError(f"provider {name} has no run method")
    return provider
