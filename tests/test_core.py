"""Tests for the parts that must be right without a network or a credential."""
from __future__ import annotations

import base64
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elteportal import archive, canvas, files, http, neptun, nicknames, tanrend, vault  # noqa: E402
from elteportal.reports import canvas_missing  # noqa: E402
from elteportal.vault import totp_now  # noqa: E402

SEED = base64.b32encode(b"12345678901234567890").decode()


def test_totp_rfc6238_vectors() -> None:
    # RFC 6238 appendix B, SHA-1 rows
    assert totp_now(SEED, digits=8, at=59) == "94287082"
    assert totp_now(SEED, digits=8, at=1111111109) == "07081804"
    assert totp_now(SEED, digits=8, at=1234567890) == "89005924"


def test_totp_default_shape() -> None:
    code = totp_now(SEED)
    assert len(code) == 6 and code.isdigit()


def test_empty_credentials_are_refused(monkeypatch) -> None:
    """The failure mode that locks the account: an empty string reaching the POST."""
    monkeypatch.setenv("NEPTUN_USER", "   ")
    monkeypatch.setenv("NEPTUN_PASS", "   ")
    try:
        neptun.credentials()
    except neptun.NeptunLockRisk:
        return
    raise AssertionError("empty credentials were accepted")


def test_missing_credentials_are_refused(monkeypatch) -> None:
    monkeypatch.delenv("NEPTUN_USER", raising=False)
    monkeypatch.delenv("NEPTUN_PASS", raising=False)

    def no_vault(*args, **kwargs):
        raise vault.VaultError("vault is locked")

    monkeypatch.setattr(neptun, "secret", no_vault)
    try:
        neptun.credentials()
    except neptun.NeptunLockRisk:
        return
    raise AssertionError("a missing credential did not refuse")


def test_calendar_regeneration_is_not_reachable() -> None:
    """tc's feed dies if this is ever called, so it must not be in the map."""
    joined = " ".join(neptun.ENDPOINTS.values()).lower()
    assert "generatenewlinks" not in joined


def test_endpoint_override(monkeypatch) -> None:
    monkeypatch.setenv("NEPTUN_ENDPOINTS", '{"grades": "/other/path"}')
    assert neptun.endpoints()["grades"] == "/other/path"
    assert "authenticate" in neptun.endpoints()


def test_term_label_conversion() -> None:
    assert tanrend.term_from_label("2026/27/1") == "2026-2027-1"
    assert tanrend.term_from_label("2025/26/2") == "2025-2026-2"
    assert tanrend.term_from_label("2026-2027-1") == "2026-2027-1"


def test_course_people_include_enrollments(monkeypatch) -> None:
    """A course roster is complete only when Canvas returns its enrolment data."""
    seen = {}

    def fake_paged(path, token, **params):
        seen.update(path=path, token=token, params=params)
        return [{"id": 7, "name": "Example Student"}]

    monkeypatch.setattr(canvas, "paged", fake_paged)
    assert canvas.course_people("token", 42) == [{"id": 7, "name": "Example Student"}]
    assert seen == {
        "path": "/courses/42/users",
        "token": "token",
        "params": {"include": ["enrollments"]},
    }


def test_assignments_include_the_authenticated_users_submission(monkeypatch) -> None:
    seen = {}

    def fake_paged(path, token, **params):
        seen.update(path=path, token=token, params=params)
        return [{"id": 7}]

    monkeypatch.setattr(canvas, "paged", fake_paged)
    assert canvas.assignments_with_submission("token", 42) == [{"id": 7}]
    assert seen == {
        "path": "/courses/42/assignments",
        "token": "token",
        "params": {"include": ["submission"], "order_by": "due_at"},
    }


def test_nickname_check_never_writes_without_apply(monkeypatch) -> None:
    monkeypatch.setattr(nicknames.canvas, "token", lambda: "token")
    monkeypatch.setattr(nicknames, "NICKNAMES", {42: "Example L"})
    monkeypatch.setattr(nicknames, "current", lambda *_: None)
    monkeypatch.setattr(
        nicknames, "set_nickname",
        lambda *_: pytest.fail("dry run attempted a Canvas write"),
    )
    output = io.StringIO()
    assert nicknames.run(output=output) == 0
    assert "would set" in output.getvalue()


def test_missing_report_uses_the_shared_canvas_assignment_reader(monkeypatch) -> None:
    course = {"id": 42, "enrollment_term_id": 9, "name": "Example"}
    seen = {}
    monkeypatch.setattr(canvas_missing.api, "active_courses", lambda _: [course])
    monkeypatch.setattr(canvas_missing.api, "current_term_id", lambda _: 9)
    monkeypatch.setattr(canvas_missing.api, "course_label", lambda _: "Example")
    monkeypatch.setattr(canvas_missing.api, "term_name", lambda _: "2026/27/1")

    def fake_assignments(token, course_id):
        seen.update(token=token, course_id=course_id)
        return []

    monkeypatch.setattr(canvas_missing.api, "assignments_with_submission", fake_assignments)
    assert canvas_missing.collect("token") == ([], [])
    assert seen == {"token": "token", "course_id": 42}


def test_mini_yaml() -> None:
    parsed = files._mini_yaml(
        '- name: Web programming\n  details: Computer Science BSc\n'
        '  url: "#!/subjects/webprog-eng"\n\n- name: Web engineering\n  url: "#!/x"\n'
    )
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Web programming"
    assert parsed[0]["url"] == "#!/subjects/webprog-eng"


def test_exact_provider_wins_over_substring() -> None:
    """`envv entry get neptun` also matches ELTE_NEPTUN_ICS. Pick the real one."""
    entries = [{"provider": "ELTE_NEPTUN_ICS", "api_key": "feed"},
               {"provider": "neptun", "api_key": "pass", "username": "code"}]
    assert vault._select(entries, "neptun")["username"] == "code"


def test_key_id_disambiguates() -> None:
    entries = [{"provider": "elte", "key_id": "inf", "api_key": "a"},
               {"provider": "elte", "key_id": "wornox", "api_key": "b"}]
    assert vault._select(entries, "elte:inf")["api_key"] == "a"


def test_ambiguous_provider_is_an_error_not_a_guess() -> None:
    entries = [{"provider": "canvas", "api_key": "a"}, {"provider": "canvas", "api_key": "b"}]
    with pytest.raises(vault.VaultError):
        vault._select(entries, "canvas")
    with pytest.raises(vault.VaultError):
        vault._select(entries, "nothing-like-this")


def test_username_disambiguates_a_shared_provider() -> None:
    entries = [
        {"provider": "Canvas", "username": "OWN001", "api_key": "own"},
        {"provider": "Canvas", "account_name": "DTIGPV", "api_key": "friend"},
    ]
    assert vault._select(entries, "Canvas", username="DTIGPV")["api_key"] == "friend"


def test_canvas_account_token_uses_the_vault_username(monkeypatch) -> None:
    seen = {}

    def fake_from_vault(provider, **kwargs):
        seen.update(provider=provider, **kwargs)
        return "token"

    monkeypatch.setattr(canvas, "from_vault", fake_from_vault)
    assert canvas.token(username="DTIGPV") == "token"
    assert seen == {"provider": "Canvas", "username": "DTIGPV"}


def test_canvas_archive_term_and_component_are_stable() -> None:
    course = {"name": "IP-18fAN1E Analysis I (2024/25/2)", "course_code": "IP-18fAN1E"}
    assert archive.term_label(course) == "Feb 2025"
    assert archive.course_title(course) == "Analysis I"
    assert archive.component(course) == "L"


def test_canvas_archive_scrubs_signed_file_urls() -> None:
    assert archive._scrub("https://canvas.elte.hu/files/1/download?verifier=secret&x=1") == (
        "https://canvas.elte.hu/files/1/download?verifier=<redacted>&x=1"
    )


def test_canvas_archive_safe_read_passes_query_parameters() -> None:
    assert archive._get(lambda value, *, include: {"value": value, "include": include}, [],
                        "example", 7, include=["detail"]) == {"value": 7, "include": ["detail"]}


def test_archive_nicknames_include_component_term_and_group(tmp_path) -> None:
    root = tmp_path / "archive" / "Course"
    catalog = root / "Analysis I L+Pr" / "Feb 2025" / "L" / "Canvas - Catalog Entry.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{"id": 42}', encoding="utf-8")
    grouped = (root / "Python L+Pr" / "Sep 2025" / "L+Pr" / "Group 8" /
               "Canvas - Catalog Entry.json")
    grouped.parent.mkdir(parents=True)
    grouped.write_text('{"id": 43}', encoding="utf-8")
    assert nicknames.archive_nicknames(str(root.parent)) == {
        42: "Analysis I L (Feb 2025)",
        43: "Python Pr (Sep 2025)",
    }


def test_entries_shapes() -> None:
    one = {"provider": "neptun"}
    assert vault._entries({"data": {"entries": [one]}}) == [one]
    assert vault._entries({"data": [one]}) == [one]
    assert vault._entries({"data": one}) == [one]
    assert vault._entries({"data": None}) == []


def test_pluck_prefers_the_named_field() -> None:
    entry = {"api_key": "the-password", "totp_secret": "SEED", "username": "code"}
    assert vault._pluck(entry, "totp_secret") == "SEED"
    assert vault._pluck(entry, "username") == "code"
    assert vault._pluck(entry, None) == "the-password"


def test_credential_status_sends_nothing(monkeypatch) -> None:
    """The dry run must not describe a credential it cannot see, and must not post."""
    monkeypatch.setenv("NEPTUN_USER", "ABC123")
    monkeypatch.setenv("NEPTUN_PASS", "hunter2hunter2")
    monkeypatch.setenv("NEPTUN_TOTP_SECRET", base64.b32encode(b"12345678901234567890").decode())
    monkeypatch.setattr(neptun.http, "post_json",
                        lambda *a, **k: pytest.fail("credential_status posted to Neptun"))
    monkeypatch.setattr(neptun, "_read_cached", lambda: None)
    report = neptun.credential_status()
    assert report["ready"] is True
    assert report["username"] == {"resolved": True}
    assert report["password"] == {"resolved": True}
    assert report["totp"]["digits"] == 6
    assert "hunter2hunter2" not in json.dumps(report)
    assert "ABC123" not in json.dumps(report)


def test_otp_field_is_not_neptuncode() -> None:
    """NeptunCode and CodePrefix both contain "code" and both come first."""
    form = {"Phase": "RequestTOTP", "NeptunCode": "ABC123", "Key": "1", "CodePrefix": "",
            "TOTPCode": "", "__RequestVerificationToken": "x"}
    chosen = next((k for k in neptun.OTP_FIELD_EXACT if k in form), None)
    assert chosen == "TOTPCode"


def test_guid_is_read_from_a_url_or_a_page() -> None:
    url = "https://hallgato1.neptun.elte.hu/outerlogin?GUID=2f9f53cb-6355-4221-ae79-33151e6be818&languageid=1033"
    assert neptun._guid_from(url) == "2f9f53cb-6355-4221-ae79-33151e6be818"
    assert neptun._guid_from("nothing here") is None


def test_form_values_are_unescaped() -> None:
    """The portal will not parse its own timestamp back with &#x2B; in it."""
    html = ('<form action="/Account/Login2FA" method="post">'
            '<input name="Rendered" value="2026-09-11T09:00:46.22&#x2B;02:00" />'
            '<input name="TOTPCode" value="" /></form>')
    action, fields = http.forms(html)[0]
    assert action == "/Account/Login2FA"
    assert fields["Rendered"] == "2026-09-11T09:00:46.22+02:00"


def test_endpoints_are_relative_to_the_api_root() -> None:
    assert neptun.url_for("authenticate") == \
        "https://hallgato1.neptun.elte.hu/api/Account/Authenticate"


def test_envelope_unwraps_and_refuses_junk() -> None:
    assert neptun._envelope({"data": 1, "notification": []}, "x")["data"] == 1
    with pytest.raises(neptun.NeptunError):
        neptun._envelope("<!DOCTYPE html>", "x")


def test_handoff_reports_full_student_web_without_logging_in_again(monkeypatch):
    from elteportal import neptun

    full = ('<p>A Neptun hallgatói webes felülete betelt. Kérjük, később próbálja újra!</p>'
            '<form id="FormToNeptun" action="/ToNeptunWeb/ToNeptunHWeb" method="post">'
            '<input name="NeptunWebType" type="hidden" value="HWeb" />'
            '<input name="NeptunWebIndex" type="hidden" value="" /></form>'
            '<script>var delay = 1500;</script>')

    class FakeSession:
        posts = 0

        def open(self, url, data=None):
            if data is not None:
                FakeSession.posts += 1
            return 200, full, "https://neptun.elte.hu/ToNeptunWeb/ToNeptunHWeb"

    clock = [0.0]
    monkeypatch.setattr(neptun.time, "time", lambda: clock[0])
    monkeypatch.setattr(neptun.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(neptun, "HANDOFF_WAIT", 30.0)
    try:
        neptun._handoff(FakeSession())
    except neptun.NeptunFull as exc:
        assert "betelt" in str(exc)
    else:
        raise AssertionError("a full student web must raise NeptunFull")
    assert 1 <= FakeSession.posts <= 7  # 5 s floor between posts, not the page's 1.5 s


def test_handoff_returns_guid_once_the_redirect_arrives():
    from elteportal import neptun

    guid = "0f8fad5b-d9cb-469f-a165-70867728950e"

    class FakeSession:
        def open(self, url, data=None):
            return 200, "", f"https://hallgato1.neptun.elte.hu/outerlogin?GUID={guid}"

    assert neptun._handoff(FakeSession()) == guid


def test_handoff_queue_page_is_polled_politely_and_named_on_timeout(monkeypatch):
    from elteportal import neptun

    queue = ('<form id="FormToNeptun" action="/ToNeptunWeb/ToNeptunHWeb" method="post">'
             '<input name="NeptunWebType" type="hidden" value="HWeb" />'
             '<input name="NeptunWebIndex" type="hidden" value="" />'
             '<p>Please wait while Neptun student web loads...</p></form>'
             '<script>var delay = 1500;</script><script>window.setTimeout(DoSubmit, 100);</script>')

    class FakeSession:
        posts = 0

        def open(self, url, data=None):
            if data is not None:
                FakeSession.posts += 1
            return 200, queue, "https://neptun.elte.hu/ToNeptunWeb/ToNeptunHWeb"

    clock = [0.0]
    monkeypatch.setattr(neptun.time, "time", lambda: clock[0])
    monkeypatch.setattr(neptun.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(neptun, "HANDOFF_WAIT", 30.0)
    with pytest.raises(neptun.NeptunError, match="queue page"):
        neptun._handoff(FakeSession())
    assert FakeSession.posts <= 7


def test_guid_url_selects_the_student_web_server_that_issued_it(monkeypatch):
    from elteportal import neptun

    monkeypatch.delenv("NEPTUN_BASE_URL", raising=False)
    monkeypatch.setattr(neptun, "HANDOFF_BASE", None)
    guid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    url = f"https://hallgato3.neptun.elte.hu/outerlogin?GUID={guid}&languageid=1033"
    assert neptun.guid_from_input(url) == guid
    assert neptun.base_url() == "https://hallgato3.neptun.elte.hu"
    assert neptun.url_for("outer_login").startswith("https://hallgato3.neptun.elte.hu/api/")
    assert neptun.guid_from_input(guid) == guid


def test_expired_bearer_redirect_is_a_401_not_a_parse_error(monkeypatch):
    head = {"WWW-Authenticate": "Bearer", "Location": "https://neptun.elte.hu/"}
    monkeypatch.setattr(http, "request", lambda url, **kw: (
        302, b'{"message":"Authorization has been denied for this request."}', head))
    with pytest.raises(http.HttpError) as caught:
        http.get_json("https://hallgato3.neptun.elte.hu/api/UserInfo")
    assert caught.value.status == 401


def test_cookie_merge_keeps_every_cookie_and_honours_deletion():
    merged = neptun._merge_cookies("a=1; b=2", "b=3; path=/; httponly\nc=4; secure\na=; expires=Thu, 01 Jan 1970")
    assert merged == "b=3; c=4"


def test_bearer_never_logs_in_on_its_own(monkeypatch, tmp_path):
    monkeypatch.setattr(neptun, "TOKEN_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(neptun, "authenticate", lambda **kw: (_ for _ in ()).throw(AssertionError("login attempted")))
    with pytest.raises(neptun.NeptunError, match="elte neptun login"):
        neptun.bearer()


def test_refresh_reads_the_unwrapped_body_and_keeps_the_rotated_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr(neptun, "TOKEN_PATH", str(tmp_path / "t.json"))
    monkeypatch.setattr(neptun, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(neptun, "HANDOFF_BASE", "https://hallgato3.neptun.elte.hu")
    neptun._write_cached("old", 0.0, base="https://hallgato3.neptun.elte.hu", cookies="s=1")
    body = b'{"accessToken":"new","sessionTimeoutInMinutes":15}'
    monkeypatch.setattr(http, "request", lambda url, **kw: (200, body, {"Set-Cookie": "s=2; path=/"}))
    result = neptun.refresh()
    cached = neptun._read_cached()
    assert result["refreshable"] and cached["token"] == "new" and cached["cookies"] == "s=2"
