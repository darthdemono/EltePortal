"""Offline release and safety checks with invented identities only."""
from __future__ import annotations

import copy
import json
import sys

import pytest

from elteportal import Client, canvas, config, http, neptun, nicknames, providers, vault


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version=2),
    lambda p: p["canvas"].update(base_url="http://canvas.example.edu/api/v1"),
    lambda p: p["canvas"].update(base_url="https://user:pass@canvas.example.edu/api/v1"),
    lambda p: p["neptun"].update(endpoints={"bad": "Calendar/GenerateNewLinksForCalendarExport"}),
    lambda p: p["neptun"].update(lcid="en"),
    lambda p: p["sites"].update(entries=[{"key": "bad", "url": "file:///etc/passwd"}]),
])
def test_profile_rejects_bad_data(change):
    profile = copy.deepcopy(config.load_profile())
    change(profile)
    with pytest.raises(config.ConfigError):
        config.validate_profile(profile)


def test_second_institution_is_data_only(monkeypatch):
    profile = copy.deepcopy(config.load_profile())
    profile["name"] = "Example University"
    profile["canvas"]["base_url"] = "https://canvas.example.edu/api/v1"
    profile["timetable"]["url"] = "https://schedule.example.edu/search"
    profile["neptun"]["flow"] = "unsupported"
    with config.use(profile):
        assert canvas.base_url() == "https://canvas.example.edu/api/v1"
        with pytest.raises(neptun.NeptunError, match="unsupported"):
            neptun.portal_login()


def test_secret_file_requires_private_mode(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"CANVAS_API_KEY": "example-token"}), encoding="utf-8")
    monkeypatch.delenv("CANVAS_API_KEY", raising=False)
    with config.use(config.load_profile(), {"secrets": {"backend": "file", "file": str(path)}}):
        path.chmod(0o644)
        with pytest.raises(vault.VaultError, match="0600"):
            vault.secret("CANVAS_API_KEY")
        path.chmod(0o600)
        assert vault.secret("CANVAS_API_KEY") == "example-token"


def test_command_secret_never_echoes_failure_output(monkeypatch):
    monkeypatch.delenv("CANVAS_API_KEY", raising=False)
    conf = {"secrets": {"backend": "command", "commands": {
        "CANVAS_API_KEY": [sys.executable, "-c", "import sys; print('secret-error'); sys.exit(1)"]}}}
    with config.use(config.load_profile(), conf):
        with pytest.raises(vault.VaultError) as caught:
            vault.secret("CANVAS_API_KEY")
    assert "secret-error" not in str(caught.value)


def test_empty_secret_fails_for_every_backend(tmp_path, monkeypatch):
    path = tmp_path / "empty.json"
    path.write_text('{"NEPTUN_PASS":""}', encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.delenv("NEPTUN_PASS", raising=False)
    with config.use(config.load_profile(), {"secrets": {"backend": "file", "file": str(path)}}):
        with pytest.raises(vault.VaultError, match="missing or empty"):
            vault.secret("NEPTUN_PASS")


def test_canvas_pagination_rejects_external_token_destination(monkeypatch):
    monkeypatch.setattr(canvas.http, "get_json", lambda *a, **k: ([{"id": 1}], {"Link": '<https://evil.example/page>; rel="next"'}))
    with pytest.raises(canvas.CanvasError, match="another origin"):
        list(canvas.paged("/courses", "example-token"))


def test_provider_scrubs_signed_file_urls(monkeypatch):
    monkeypatch.setattr(canvas, "token", lambda: "example-token")
    monkeypatch.setattr(canvas, "course_files", lambda *_: [{"url": "https://files.example.edu/a?verifier=secret&X-Amz-Signature=other"}])
    result = providers.CanvasProvider().run("files", course=1)
    assert "secret" not in json.dumps(result) and "other" not in json.dumps(result)


def test_canvas_refuses_empty_token_before_request(monkeypatch):
    monkeypatch.setattr(canvas.http, "get_json", lambda *a, **k: pytest.fail("network reached"))
    with pytest.raises(canvas.CanvasError, match="empty"):
        canvas.get("/courses", " ")


def test_calendar_regeneration_override_is_refused(monkeypatch):
    monkeypatch.setenv("NEPTUN_ENDPOINTS", '{"calendar_links":"Calendar/GenerateNewLinksForCalendarExport"}')
    with pytest.raises(neptun.NeptunError, match="forbidden"):
        neptun.endpoints()


def test_nickname_write_requires_apply_even_for_direct_call(monkeypatch):
    monkeypatch.setattr(nicknames.http, "request", lambda *a, **k: pytest.fail("write reached network"))
    with pytest.raises(ValueError, match="apply=True"):
        nicknames.set_nickname("example-token", 1, "Example")


def test_failed_authentication_is_attempted_once(monkeypatch):
    monkeypatch.setattr(neptun, "credentials", lambda: ("EXAMPLE", "example-password"))
    monkeypatch.setattr(neptun, "current_code", lambda: None)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise http.HttpError(401, "https://example.edu/api/Account/Authenticate")

    monkeypatch.setattr(neptun.http, "post_json", fail)
    with pytest.raises(neptun.NeptunError):
        neptun.authenticate()
    assert calls == [1]


def test_refresh_after_401_happens_once(monkeypatch):
    monkeypatch.setattr(neptun, "bearer", lambda: "example-token")
    counts = {"get": 0, "refresh": 0}

    def get(*args, **kwargs):
        counts["get"] += 1
        if counts["get"] == 1:
            raise http.HttpError(401, "https://example.edu/api/UserInfo")
        return {"data": {"ok": True}}, {}

    monkeypatch.setattr(neptun.http, "get_json", get)
    monkeypatch.setattr(neptun, "refresh", lambda: counts.__setitem__("refresh", counts["refresh"] + 1))
    assert neptun.call("userinfo") == {"ok": True}
    assert counts == {"get": 2, "refresh": 1}


def test_plugin_error_cannot_leak_credential(monkeypatch):
    class BadProvider:
        def run(self, action, **params):
            raise RuntimeError("example-secret")

    monkeypatch.setitem(providers.BUILTINS, "bad", BadProvider())
    result = Client().call("bad", "read")
    assert not result["ok"] and "example-secret" not in json.dumps(result)


def test_provider_entry_point_discovery_without_numeric_index(monkeypatch):
    class Entry:
        def load(self):
            class Example:
                def run(self, action, **params):
                    return {"action": action}
            return Example

    class Entries:
        def __len__(self):
            return 1

        def __iter__(self):
            yield Entry()

        def __getitem__(self, key):
            raise AssertionError("entry points are keyed by name")

    monkeypatch.setattr(providers, "entry_points", lambda **kw: Entries())
    assert providers.get("external").run("ping") == {"action": "ping"}


def test_secret_backend_entry_point_and_empty_refusal(monkeypatch):
    monkeypatch.delenv("CANVAS_API_KEY", raising=False)

    class Entry:
        def load(self):
            return lambda name, settings: settings["value"]

    monkeypatch.setattr(vault, "entry_points", lambda **kw: [Entry()])
    with config.use(config.load_profile(), {"secrets": {"backend": "example", "value": "example-token"}}):
        assert vault.secret("CANVAS_API_KEY") == "example-token"
    with config.use(config.load_profile(), {"secrets": {"backend": "example", "value": ""}}):
        with pytest.raises(vault.VaultError, match="missing or empty"):
            vault.secret("CANVAS_API_KEY")


def test_cli_config_check_envelope(capsys):
    from elteportal.cli import main
    assert main(["--json", "config", "check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "1.0" and result["ok"]


def test_bad_user_config_is_a_clean_json_failure(tmp_path, monkeypatch, capsys):
    from elteportal.cli import main
    broken = tmp_path / "broken.toml"
    broken.write_text("[not valid", encoding="utf-8")
    monkeypatch.setenv("ELTE_CONFIG", str(broken))
    assert main(["--json", "config", "check"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["schema_version"] == "1.0"
