"""Validated institution profiles and user settings. No network or secret reads on load."""
from __future__ import annotations

import contextlib
import contextvars
import os
from importlib.resources import files
from pathlib import Path
import tomllib
from urllib.parse import urlsplit
from typing import Any, Iterator


class ConfigError(ValueError):
    """Configuration was rejected before use."""


_ACTIVE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("elte_profile", default=None)
_SETTINGS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("elte_settings", default=None)
_SECTIONS = {"canvas", "neptun", "timetable", "files", "sites"}
_DENIED = "generatenewlinksforcalendarexport"


def _url(value: Any, label: str, *, allow_http: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a URL string")
    parts = urlsplit(value)
    if parts.scheme not in ({"http", "https"} if allow_http else {"https"}) or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ConfigError(f"{label} must be a full {'HTTP(S)' if allow_http else 'HTTPS'} URL without credentials or fragment")
    return value.rstrip("/")


def validate_profile(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ConfigError("profile schema_version must be 1")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ConfigError("profile name is required")
    if set(data) - (_SECTIONS | {"schema_version", "name"}):
        raise ConfigError("profile has unknown top-level fields")
    for section in _SECTIONS:
        if section not in data or not isinstance(data[section], dict):
            raise ConfigError(f"profile [{section}] is required")
    for section, key in (("canvas", "base_url"), ("neptun", "portal_url"),
                         ("neptun", "api_url"), ("timetable", "url")):
        value = data[section].get(key)
        if value is not None:
            data[section][key] = _url(value, f"{section}.{key}")
    for section in ("canvas", "neptun", "timetable"):
        if data[section].get("enabled", False) and not data[section].get("base_url" if section == "canvas" else "api_url" if section == "neptun" else "url"):
            raise ConfigError(f"enabled [{section}] needs its URL")
    if data["canvas"].get("enabled") and not data["canvas"]["base_url"].endswith("/api/v1"):
        raise ConfigError("canvas.base_url must end in /api/v1")
    if data["neptun"].get("enabled") and not data["neptun"]["api_url"].endswith("/api"):
        raise ConfigError("neptun.api_url must end in /api")
    neptun = data["neptun"]
    if neptun.get("enabled", False):
        if not neptun.get("portal_url") or neptun.get("flow") not in {"elte-portal", "unsupported"}:
            raise ConfigError("enabled [neptun] needs portal_url and flow = 'elte-portal' or 'unsupported'")
        if not isinstance(neptun.get("lcid"), int):
            raise ConfigError("neptun.lcid must be an integer")
        if neptun["flow"] == "elte-portal" and not isinstance(neptun.get("student_host_suffix"), str):
            raise ConfigError("ELTE portal flow needs student_host_suffix")
    endpoints = neptun.get("endpoints", {})
    if not isinstance(endpoints, dict):
        raise ConfigError("neptun.endpoints must be a table")
    for name, path in endpoints.items():
        if not isinstance(name, str) or not isinstance(path, str) or not path or path.startswith("/") or ".." in path or "?" in path or _DENIED in path.casefold():
            raise ConfigError("invalid or forbidden Neptun endpoint")
    for key, url in data["files"].items():
        if not isinstance(key, str):
            raise ConfigError("file server names must be strings")
        data["files"][key] = _url(url, f"files.{key}", allow_http=True)
    sites = data["sites"].get("entries", [])
    if not isinstance(sites, list):
        raise ConfigError("sites.entries must be an array")
    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get("key"), str):
            raise ConfigError("each site needs a key")
        site["url"] = _url(site.get("url"), f"site {site['key']}", allow_http=True)
    return data


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path else Path(str(files("elteportal").joinpath("profiles/elte.toml")))
    try:
        with source.open("rb") as stream:
            return validate_profile(tomllib.load(stream))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read profile {source}: {type(exc).__name__}") from None


def config_path() -> Path | None:
    explicit = os.environ.get("ELTE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    candidate = root / "elteportal" / "config.toml"
    return candidate if candidate.is_file() else None


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path else config_path()
    if source is None:
        return {}
    try:
        with source.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config {source}: {type(exc).__name__}") from None
    if set(data) - {"profile", "secrets"} or not isinstance(data.get("secrets", {}), dict):
        raise ConfigError("config accepts only profile and [secrets]")
    if "profile" in data and not isinstance(data["profile"], str):
        raise ConfigError("config profile must be a path")
    secrets = data.get("secrets", {})
    if secrets.get("backend", "env") not in {"env", "file", "keyring", "command", "legacy", "envvault"}:
        raise ConfigError("unknown secrets backend")
    if secrets.get("backend") == "file" and not isinstance(secrets.get("file"), str):
        raise ConfigError("file backend needs secrets.file")
    if secrets.get("backend") == "command" and not isinstance(secrets.get("commands"), dict):
        raise ConfigError("command backend needs [secrets.commands]")
    data["_source"] = str(source)
    return data


def settings() -> dict[str, Any]:
    return _SETTINGS.get() or load_settings()


def profile() -> dict[str, Any]:
    active = _ACTIVE.get()
    if active is not None:
        return active
    conf = settings()
    path = os.environ.get("ELTE_PROFILE") or conf.get("profile")
    if path and not Path(path).is_absolute() and conf.get("_source"):
        path = str(Path(conf["_source"]).parent / path)
    return load_profile(path)


@contextlib.contextmanager
def use(profile_data: dict[str, Any], settings_data: dict[str, Any] | None = None) -> Iterator[None]:
    a = _ACTIVE.set(validate_profile(profile_data))
    b = _SETTINGS.set(settings_data or {})
    try:
        yield
    finally:
        _SETTINGS.reset(b)
        _ACTIVE.reset(a)
