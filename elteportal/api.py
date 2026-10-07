"""Stable library facade used by the CLI and external callers."""
from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from . import config, providers

SCHEMA_VERSION = "1.0"


def envelope(command: str, data: Any = None, *, error: str | None = None,
             warnings: list[str] | None = None) -> dict[str, Any]:
    result = {"schema_version": SCHEMA_VERSION, "ok": error is None, "command": command,
              "data": data, "warnings": warnings or []}
    if error is not None:
        result["error"] = error
    return result


class Client:
    """A profile-bound client. `call` returns a versioned JSON-compatible envelope."""

    def __init__(self, profile_path: str | Path | None = None,
                 config_path: str | Path | None = None) -> None:
        self.settings = config.load_settings(config_path)
        selected = profile_path or os.environ.get("ELTE_PROFILE") or self.settings.get("profile")
        if selected and not Path(selected).is_absolute() and self.settings.get("_source"):
            selected = Path(self.settings["_source"]).parent / selected
        self.profile = config.load_profile(selected)

    def data(self, provider: str, action: str, **params: Any) -> Any:
        with config.use(self.profile, self.settings):
            return providers.get(provider).run(action, **params)

    def call(self, provider: str, action: str, **params: Any) -> dict[str, Any]:
        command = f"{provider} {action}"
        try:
            return envelope(command, self.data(provider, action, **params))
        except Exception:
            # Plugins are untrusted code. Their exception text can contain credentials.
            return envelope(command, error=f"{command} failed; check configuration and provider")
