"""Secret resolution and local RFC 6238 TOTP generation.

Environment values take precedence over one configured backend. Blank values
are refused. The legacy vault reader is optional and selected explicitly.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import shutil
import stat
import struct
import subprocess
import time
from importlib.metadata import entry_points
from pathlib import Path

from .config import settings

class VaultError(RuntimeError):
    """A secret could not be resolved. Never carries the secret."""


def envv_bin() -> str | None:
    for candidate in (os.environ.get("ENVV_BIN", ""), "envv"):
        if not candidate:
            continue
        found = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
        if found:
            return found
    return None


def from_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def from_vault(provider: str, field: str | None = None, *, username: str | None = None,
               timeout: int = 30) -> str:
    """Read one secret out of EnvVault, right now, without exporting it.

    `provider` is either a bare provider name or `provider:key_id`. envv matches
    a bare name as a substring - `neptun` also hits `ELTE_NEPTUN_ICS` - so the
    selection is redone here on an exact, case-insensitive match and an ambiguous
    name is an error rather than a coin toss.
    """
    binary = envv_bin()
    if not binary:
        raise VaultError("envv not found - set ENVV_BIN or build EnvVault")
    name = provider.split(":", 1)[0]
    cmd = [binary, "entry", "get", name, "--reveal", "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise VaultError("envv timed out - is the vault locked?") from None
    if proc.returncode != 0:
        raise VaultError(f"envv exited {proc.returncode} reading {provider}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise VaultError("envv returned output this version cannot parse") from None
    entry = _select(_entries(payload), provider, username=username)
    value = _pluck(entry, field)
    if not value:
        raise VaultError(f"{provider} has no usable value for {field or 'key'}")
    return value


def _entries(payload: object) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return [e for e in data["entries"] if isinstance(e, dict)]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _select(entries: list[dict], provider: str, *, username: str | None = None) -> dict:
    name, _, key_id = provider.partition(":")
    hits = [
        e for e in entries
        if str(e.get("provider", "")).lower() == name.lower()
        and (not key_id or str(e.get("key_id") or "").lower() == key_id.lower())
    ]
    if username:
        hits = [entry for entry in hits if str(
            entry.get("username") or entry.get("account_name") or ""
        ).casefold() == username.casefold()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        suffix = f" with username {username}" if username else ""
        raise VaultError(f"no vault entry is exactly {provider}{suffix}")
    selector = f" username={username}" if username else ""
    raise VaultError(f"{provider}{selector} matches {len(hits)} entries - address it as provider:key_id")


def _pluck(data: object, field: str | None) -> str | None:
    """The named field first, then the fields EnvVault actually uses.

    A revealed entry carries the password in `api_key` whatever its secretType
    says, and a TOTP seed in `totp_secret`.
    """
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        order = ([field] if field else []) + ["value", "key", "secret", "password", "api_key"]
        for key in order:
            if key and isinstance(data.get(key), str) and data[key]:
                return data[key]
    return None


def secret(env_name: str, provider: str | None = None, field: str | None = None) -> str:
    """Environment overrides one configured backend. Empty always fails closed."""
    if env_name in os.environ and not os.environ[env_name].strip():
        raise VaultError(f"{env_name} is set but empty")
    value = from_env(env_name)
    if value and value.strip():
        return value
    conf = settings().get("secrets", {})
    backend = os.environ.get("ELTE_SECRET_BACKEND") or conf.get("backend", "env")
    try:
        if backend == "env":
            value = None
        elif backend == "file":
            path = Path(conf["file"]).expanduser()
            info = path.stat()
            mode = stat.S_IMODE(info.st_mode)
            if not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
                raise VaultError("secret file must be a regular file owned by this user")
            if mode & 0o077:
                raise VaultError("secret file must be readable only by its owner (mode 0600)")
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = payload.get(env_name)
        elif backend == "keyring":
            import keyring  # type: ignore[import-not-found]
            value = keyring.get_password(conf.get("service", "elteportal"), env_name)
        elif backend == "command":
            args = conf["commands"].get(env_name)
            if not isinstance(args, list) or not args or not all(isinstance(x, str) and x for x in args):
                raise VaultError(f"no command configured for {env_name}")
            proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
            if proc.returncode:
                raise VaultError(f"secret command for {env_name} failed; check the manager directly")
            value = proc.stdout.rstrip("\r\n")
        elif backend in {"legacy", "envvault"}:
            if not provider:
                raise VaultError(f"no vault entry configured for {env_name}")
            value = from_vault(provider, field)
        else:
            matches = entry_points(group="elteportal.secrets", name=backend)
            if len(matches) != 1:
                raise VaultError(f"unknown secret backend {backend}")
            value = next(iter(matches)).load()(env_name, conf)
    except VaultError:
        raise
    except (OSError, KeyError, ValueError, ImportError, subprocess.TimeoutExpired):
        raise VaultError(f"{backend} could not resolve {env_name}; check its configuration") from None
    except Exception:
        raise VaultError(f"{backend} failed resolving {env_name}; check its manager") from None
    if not isinstance(value, str) or not value.strip():
        raise VaultError(f"{env_name} is missing or empty in {backend}")
    return value


def totp_now(seed_b32: str, *, digits: int = 6, period: int = 30, at: int | None = None) -> str:
    """RFC 6238, Google Authenticator defaults: 6 digits, 30 seconds, SHA-1."""
    cleaned = seed_b32.strip().replace(" ", "").upper()
    cleaned += "=" * (-len(cleaned) % 8)
    try:
        key = base64.b32decode(cleaned, casefold=True)
    except Exception:
        raise VaultError("TOTP seed is not valid base32") from None
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def seconds_left(period: int = 30, at: int | None = None) -> int:
    now = int(at if at is not None else time.time())
    return period - (now % period)
