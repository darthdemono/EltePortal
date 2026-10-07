"""ELTE's observed Neptun portal flow, guarded against account lockout.

Other institutions use different portals. The profile selects this flow only
when it has been checked for that institution. Credentials are refused if empty,
failed authentication is attempted once, and the calendar-link regeneration
endpoint is forbidden because it invalidates existing subscriptions.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any

from . import http
from .config import load_profile, profile
from .vault import VaultError, seconds_left, secret, totp_now

BASE_DEFAULT = load_profile()["neptun"]["api_url"].removesuffix("/api")
PORTAL_DEFAULT = load_profile()["neptun"]["portal_url"]
API_ROOT = "/api/"
LCID_EN = load_profile()["neptun"]["lcid"]

#: Legacy public name. Active paths come from the selected profile.
ENDPOINTS: dict[str, str] = load_profile()["neptun"]["endpoints"]  # compatibility shim

#: Paging and filtering these need, taken from the captured requests.
DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "exam_results": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 100},
    "exams_registered": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 9999},
    "exams_available": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 9999},
    "invoices": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 100,
                 "sortAndPage.creationDate": "desc"},
    "to_pay": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 100},
    "scholarships": {"sortAndPage.firstRow": 0, "sortAndPage.lastRow": 100,
                     "sortAndPage.termId": "desc"},
    "publications": {"sortAndPaging.firstRow": 0, "sortAndPaging.lastRow": 100},
    "dormitory": {"sort": "desc", "sortablePropertyName": "selectedPeriodFrom"},
}

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "elteportal"
)
TOKEN_PATH = os.path.join(CACHE_DIR, "neptun-token.json")
SKEW = 60  # treat a token as dead this many seconds before its own expiry


class NeptunError(RuntimeError):
    """A Neptun call failed. Never carries a credential."""


class NeptunFull(NeptunError):
    """Signed in, but the student web turned the hand-off away for capacity."""


class NeptunLockRisk(NeptunError):
    """Refused before sending. Raised instead of doing something account-lethal."""


# The student web can choose one of several hosts per hand-off.
# A GUID is only good on the server that issued it, and so is its token.
HANDOFF_BASE: str | None = None


def base_url() -> str:
    override = os.environ.get("NEPTUN_BASE_URL")
    if override:
        from .config import _url
        return _url(override, "NEPTUN_BASE_URL")
    if HANDOFF_BASE:
        return HANDOFF_BASE
    cached = _read_cached()
    if cached and isinstance(cached.get("base"), str):
        return cached["base"]
    return profile()["neptun"]["api_url"].removesuffix("/api")


def portal_url() -> str:
    """The ASP.NET portal that owns the password, as opposed to the JSON API."""
    from .config import _url
    return _url(os.environ.get("NEPTUN_PORTAL_URL") or profile()["neptun"]["portal_url"], "NEPTUN_PORTAL_URL")


def endpoints() -> dict[str, str]:
    raw = os.environ.get("NEPTUN_ENDPOINTS")
    if not raw:
        return dict(profile()["neptun"]["endpoints"])
    try:
        override = json.loads(raw)
    except json.JSONDecodeError:
        raise NeptunError("NEPTUN_ENDPOINTS is not valid JSON") from None
    merged = dict(profile()["neptun"]["endpoints"])
    if not isinstance(override, dict) or any(not isinstance(v, str) or not v or v.startswith("//") or
                                           "://" in v or ".." in v or "?" in v or "generatenewlinksforcalendarexport" in v.casefold()
                                           for v in override.values()):
        raise NeptunError("NEPTUN_ENDPOINTS contains an invalid or forbidden path")
    merged.update(override)
    return merged


def url_for(name: str) -> str:
    table = endpoints()
    if name not in table:
        raise NeptunError(f"no endpoint named {name}")
    return base_url() + API_ROOT + table[name].lstrip("/")


def _require_flow() -> None:
    if profile()["neptun"].get("flow") != "elte-portal":
        raise NeptunError("this Neptun login flow is unsupported; install a tested provider")


# --------------------------------------------------------------------------- creds


def credentials() -> tuple[str, str]:
    """Neptun code and password, or a refusal. Empty counts as missing."""
    try:
        user = secret("NEPTUN_USER", provider="neptun", field="username")
        password = secret("NEPTUN_PASS", provider="neptun", field="api_key")
    except VaultError as exc:
        raise NeptunLockRisk(f"refusing to log in: {exc}") from None
    if not user.strip() or not password.strip():
        raise NeptunLockRisk("refusing to log in: credential resolved to an empty string")
    return user.strip(), password


def current_code() -> str | None:
    """A live TOTP code, generated now from the seed. None when no seed is set."""
    try:
        seed = secret("NEPTUN_TOTP_SECRET", provider="neptun", field="totp_secret")
    except VaultError:
        return None
    return totp_now(seed)


def credential_status() -> dict[str, Any]:
    """Everything a login would need, resolved and described. Sends nothing.

    This is the dry run for rule 1. An empty or missing credential is exactly
    what locks the account, so it is worth knowing that the vault answers before
    anything is posted to Neptun.
    """
    from .vault import seconds_left

    report: dict[str, Any] = {"base_url": base_url(),
                              "authenticate_url": url_for("authenticate"),
                              "ready": False}
    try:
        user, password = credentials()
    except NeptunError as exc:
        report["error"] = str(exc)
        return report
    # Resolution state is enough for a dry run. Lengths and hashes are still
    # credential-derived metadata, so keep them out of output too.
    del user, password
    report["username"] = {"resolved": True}
    report["password"] = {"resolved": True}
    code = current_code()
    report["totp"] = ({"digits": len(code), "valid_for": seconds_left()} if code
                      else {"digits": 0, "note": "no seed resolved; login would go without one"})
    cached = _read_cached()
    report["token_cache"] = ({"present": True, "seconds_left": int(cached["expires_at"] - time.time())}
                             if cached else {"present": False})
    report["ready"] = True
    return report


# --------------------------------------------------------------------------- token


def _read_cached(*, ignore_expiry: bool = False) -> dict[str, Any] | None:
    try:
        with open(TOKEN_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("token"):
        return None
    expected = profile()["neptun"]["api_url"]
    if data.get("profile_api") not in (expected, None if profile()["name"] == "ELTE" else expected):
        return None
    if not ignore_expiry and float(data.get("expires_at", 0)) - SKEW <= time.time():
        return None
    return data


def _write_cached(token: str, expires_at: float, note: str = "",
                  base: str | None = None, cookies: str | None = None) -> None:
    """0600, in the cache dir, not the vault.

    The vault holds what is owned; a bearer token is derived and short-lived.
    Writing every refresh back into EnvVault would spend a human-scale audit log
    on machine churn. A corrupted cache is one `rm` from fixed.
    """
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    payload = {"token": token, "expires_at": expires_at, "note": note,
               "profile_api": profile()["neptun"]["api_url"],
               "written": int(time.time())}
    if base:
        payload["base"] = base
    if cookies:
        payload["cookies"] = cookies  # what Account/GetNewTokens needs; same 0600 file
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_PATH)


def forget_token() -> bool:
    try:
        os.unlink(TOKEN_PATH)
        return True
    except FileNotFoundError:
        return False


def _envelope(payload: object, what: str) -> dict[str, Any]:
    """Every answer is `{"data": ..., "notification": [...]}`. Unwrap it."""
    if not isinstance(payload, dict) or "data" not in payload:
        raise NeptunError(f"{what} returned something this client cannot read")
    return payload


OTP_FIELD_HINTS = ("code", "token", "otp", "twofactor", "authenticator")
#: `__RequestVerificationToken` contains "token" and is on every ASP.NET form,
#: including the language and theme pickers in the page furniture. Never it.
#: `NeptunCode` and `CodePrefix` both contain "code" and sit before `TOTPCode`
#: in the form. Filling either of them posts an empty TOTPCode, which the portal
#: answers by quietly sending you back to the login page.
OTP_FIELD_NEVER = ("verification", "culture", "theme", "returnurl",
                   "neptuncode", "codeprefix")
#: Checked before the hints, because guessing is the fallback, not the plan.
OTP_FIELD_EXACT = ("TOTPCode", "TotpCode", "Code", "TwoFactorCode")


def _guid_from(text: str) -> str | None:
    """The hand-off GUID, from a URL or from the page that carries it.

    Also notes which student-web server the GUID belongs to, when the text says.
    """
    import re

    global HANDOFF_BASE
    match = re.search(r"[?&]GUID=([0-9a-fA-F-]{36})", text)
    if not match:
        return None
    suffix = re.escape(profile()["neptun"].get("student_host_suffix", ""))
    server = re.search(r"https://(hallgato\d*\." + suffix + r")/outerlogin[^\s\"']*"
                       + re.escape(match.group(1)), text, re.I)
    if "/outerlogin" in text.casefold() and not server:
        return None
    if server:
        HANDOFF_BASE = "https://" + server.group(1).lower()
    return match.group(1)


def guid_from_input(value: str) -> str:
    """A GUID pasted bare, or the whole outerlogin URL copied out of a browser."""
    guid = _guid_from(value)
    if guid:
        return guid
    if "://" in value:
        raise NeptunError("outerlogin URL is outside the configured student hosts")
    if len(value.strip()) == 36:
        return value.strip()
    raise NeptunError("that is neither a GUID nor an outerlogin?GUID=... URL")


HANDOFF_PATH = "/ToNeptunWeb/ToNeptunHWeb"  # ELTE flow path; see profile


def _hop(trail: list[str] | None, label: str, status: object, url: str, page: str) -> None:
    """Note where a login step landed, so a failed attempt says why without a second one.

    Only the status, the URL with its query dropped and the page title go in -
    never a form value, a cookie or a GUID.
    """
    if trail is None:
        return
    bare = urllib.parse.urlsplit(url)._replace(query="").geturl()
    trail.append(f"{label}: {status} {bare}")


FULL_MARKER = "webes fel\u00fclete betelt"  # "A Neptun hallgatói webes felülete betelt."
def _looks_full(page: str) -> bool:
    """The turned-away hand-off page, in whichever language the portal chose.

    The text changes with Accept-Language, the structure does not: a page that
    lets you through submits itself on a timer, a full one only offers a
    button that calls DoSubmit().
    """
    if FULL_MARKER in page:
        return True
    return 'onclick="DoSubmit();"' in page and "window.setTimeout(DoSubmit" not in page


HANDOFF_WAIT = 150.0
HANDOFF_MIN_GAP = 5.0


def _handoff(session: "http.Session", trail: list[str] | None = None,
             page: str | None = None, url: str | None = None) -> str | None:
    """Ask the portal to send us over to the student web, and catch the GUID.

    Logging in leaves you on the portal's own news page. The GUID only exists
    once you ask to be handed over, which in the browser is a page that posts
    NeptunWebIndex and NeptunWebType back to the same path. The answer
    redirects to hallgato1/outerlogin?GUID=..., and that URL is the prize.

    The hand-off page carries `var delay = <ms>` and its script waits that long
    before submitting. While the student web is still being prepared the server
    answers the post with the same page again, and the browser just submits
    again (an authenticated post can come back as the hand-off
    page, not a redirect). So keep submitting, honouring the delay, for as long
    as the page's own progress bar runs.
    """
    import re

    deadline = time.time() + HANDOFF_WAIT
    full_seen = 0
    tries = 0
    try:
        if page is None or "NeptunWebIndex" not in page:
            status, page, url = session.open(portal_url() + HANDOFF_PATH)
            _hop(trail, "GET handoff", status, url, page)
        while True:
            guid = _guid_from(url or "") or _guid_from(page)
            if guid:
                return guid
            found = next(((action, fields) for action, fields in http.forms(page)
                          if "NeptunWebIndex" in fields), None)
            if _looks_full(page):
                full_seen += 1
            if found is None:
                _dump("handoff-no-form", page)
                return None
            delay = re.search(r"var\s+delay\s*=\s*(\d+)", page)
            wait = int(delay.group(1)) / 1000 if delay else 0
            # The page's own delay is ~1-3 s and the browser leaves the retry
            # to a human click; a script should not hammer a full server faster.
            wait = max(wait, HANDOFF_MIN_GAP) if tries else wait
            tries += 1
            if time.time() + wait > deadline:
                _dump("handoff-timeout", page)
                if full_seen:
                    raise NeptunFull(
                        "signed in, but the Neptun student web is full ('A Neptun "
                        "hallgatói webes felülete betelt') and stayed full for "
                        f"{HANDOFF_WAIT:.0f}s over {full_seen} hand-off tries. This is "
                        "ELTE's capacity, not a credential problem; the password and "
                        "2FA were accepted. Try later, or pass --wait to hold on longer."
                    )
                raise NeptunError(
                    f"signed in, but the student web kept its hand-off queue page "
                    f"('please wait while Neptun student web loads') for "
                    f"{HANDOFF_WAIT:.0f}s over {tries} tries and never redirected. "
                    "The password and 2FA were accepted; this is the portal not "
                    "releasing a slot. Try later, or pass --wait to hold on longer."
                )
            if wait:
                time.sleep(wait)
            action, form = found
            target = urllib.parse.urljoin(url or portal_url(), action or HANDOFF_PATH)
            if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(portal_url()).netloc:
                raise NeptunError("handoff form points outside the configured portal")
            status, page, url = session.open(target, data=form)
            _hop(trail, f"POST handoff (delay {wait:g}s)", status, url, page)
            if urllib.parse.urlsplit(url).path.lower().startswith("/account/login"):
                return None  # not signed in; resubmitting would not change that
            if not wait:
                time.sleep(2)
    except http.HttpError as exc:
        _hop(trail, "handoff HttpError", exc.status, exc.path, "")
        return None


def _dump(label: str, page: str) -> None:
    """Never persist a login page: hidden fields can contain credentials."""
    del label, page


def _login_again(session: "http.Session", trail: list[str]) -> str | None:
    """After 2FA the portal drops you on its home page, not on the hand-off.

    In a browser, the session is already signed in at
    that point, and clicking Login once more moves on to the student web. So
    open /Account/Login again with the same cookies and take whatever it offers:
    a GUID, the auto-submitting hand-off form, or the hand-off path itself.
    """
    try:
        status, page, url = session.open(portal_url() + "/Account/Login")
    except http.HttpError as exc:
        _hop(trail, "login-again HttpError", exc.status, exc.path, "")
        return None
    _hop(trail, "GET login again", status, url, page)
    guid = _guid_from(url) or _guid_from(page)
    if guid:
        return guid
    if "NeptunWebIndex" in page:
        return _handoff(session, trail, page, url)
    _dump("login-again", page)
    return None


def _portal_logout(session: "http.Session") -> None:
    """Sign the portal session out after a failed hand-off, so attempts do not pile up."""
    try:
        _, page, _ = session.open(portal_url() + "/")
        form = http.form_fields(page, action_contains="/Account/Logout")
        if form:
            session.open(portal_url() + "/Account/Logout", data=form)
    except (http.HttpError, NeptunError):
        pass


def portal_login(*, code: str | None = None, wait: float | None = None) -> str:
    """Log in at the portal and hand off; sign out again if no GUID came back."""
    _require_flow()
    session = http.Session()
    try:
        return _portal_login(session, code=code, wait=wait)
    except NeptunLockRisk:
        raise
    except NeptunError:
        _portal_logout(session)
        raise


def _portal_login(session: "http.Session", *, code: str | None = None,
                  wait: float | None = None) -> str:
    """Log in at the portal and come back with the single-use hand-off GUID.

    The ELTE API login has redirected to the portal, so this drives the portal
    form. The API's direct authenticate path can answer with an empty-body
    redirect. The portal checks the password, then hands the browser to the
    student host with a single-use GUID.

    One attempt. The captcha risk lives here, not in the API.
    """
    global HANDOFF_WAIT
    if wait is not None:
        HANDOFF_WAIT = float(wait)
    user, password = credentials()
    login_url = portal_url() + "/Account/Login"
    _, page, _ = session.open(login_url)
    fields = http.form_fields(page, action_contains="/Account/Login")
    if "LoginName" not in fields or "Password" not in fields:
        raise NeptunError("the portal login page no longer carries LoginName and Password")
    fields["LoginName"] = user
    fields["Password"] = password
    status, page, url = session.open(login_url, data=fields)
    trail: list[str] = []
    _hop(trail, "POST login", status, url, page)

    # While the second factor is pending, leave the session alone: posting the
    # hand-off in between is not what a browser does.
    two_factor_pending = any("TOTPCode" in f for _, f in http.forms(page))
    guid = _guid_from(url) or (None if two_factor_pending else _handoff(session, trail))
    if guid:
        return guid

    # No GUID yet: the portal is asking for the second factor. A code minted in
    # the last seconds of its window can expire in flight, so wait out the tail.
    if code is None and seconds_left() < 8:
        time.sleep(seconds_left() + 1)
    otp = code if code is not None else current_code()
    if not otp:
        raise NeptunError(
            "the portal asked for something more than the password and no TOTP "
            f"seed resolved (status {status} at {urllib.parse.urlsplit(url).path}). Not guessing."
        )
    seen: list[str] = []
    for action, form in http.forms(page):
        if any(x in action.lower() for x in ("setlanguage", "settheme", "logout")):
            continue
        seen += list(form)
        target = next((k for k in OTP_FIELD_EXACT if k in form), None)
        target = target or next((k for k in form
                       if any(hint in k.lower() for hint in OTP_FIELD_HINTS)
                       and not any(bad in k.lower() for bad in OTP_FIELD_NEVER)), None)
        if not target:
            continue
        form[target] = otp
        next_url = urllib.parse.urljoin(url, action) if action else url
        status, page, url = session.open(next_url, data=form)
        _hop(trail, "POST 2FA", status, url, page)
        if any("TOTPCode" in fields for _, fields in http.forms(page)):
            raise NeptunError(
                "the second factor was not accepted. A TOTP code is single-use, "
                "so a code already spent this 30-second window will bounce - wait "
                "for the next one. Not retrying on my own."
            )
        guid = _guid_from(url) or _handoff(session, trail) or _login_again(session, trail)
        if guid:
            return guid
        break
    raise NeptunError(
        f"logged in but no hand-off GUID appeared (last stop {urllib.parse.urlsplit(url).path}). "
        f"Form fields on the way: {sorted(set(seen))}. Hops: {trail}. "
        "Not retrying - repeated attempts put a captcha on the account."
    )


def _merge_cookies(old: str | None, set_cookie: str | None) -> str:
    """Fold Set-Cookie lines into a Cookie header value, newest value wins."""
    jar: dict[str, str] = {}
    for part in (old or "").split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            jar[name] = value
    for line in (set_cookie or "").splitlines():
        first = line.split(";", 1)[0]
        if "=" in first:
            name, value = first.strip().split("=", 1)
            if value:
                jar[name] = value
            else:
                jar.pop(name, None)
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def _token_post(name: str, payload: dict[str, Any], cookies: str | None = None,
                token: str | None = None) -> tuple[dict[str, Any], str]:
    """POST to the account API keeping the cookies, which the refresh depends on."""
    headers = {"Content-Type": "application/json", **http.JSON_HEADERS}
    if cookies:
        headers["Cookie"] = cookies
    if token:
        headers["Authorization"] = f"Bearer {token}"
    _, raw, head = http.request(url_for(name), method="POST", headers=headers,
                                data=json.dumps(payload).encode("utf-8"), follow=False)
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise NeptunError("token exchange returned invalid JSON") from None
    return body, _merge_cookies(cookies, head.get("Set-Cookie"))


def _store_token(data: Any, note: str, cookies: str) -> dict[str, Any]:
    if isinstance(data, str):  # a spent or unknown GUID answers with the logout URL
        raise NeptunError("that GUID is spent or unknown - Neptun sent us to log out")
    if not isinstance(data, dict) or not data.get("accessToken"):
        raise NeptunError(f"{note} returned no accessToken")
    minutes = data.get("sessionTimeoutInMinutes")
    minutes = float(minutes) if isinstance(minutes, (int, float)) else 15.0
    expires_at = time.time() + minutes * 60
    _write_cached(str(data["accessToken"]), expires_at, note=note, base=base_url(),
                  cookies=cookies)
    return {"user": data.get("userName"), "server": base_url(), "expires_at": expires_at,
            "seconds": int(expires_at - time.time()),
            "refreshable": bool(cookies),
            "two_factor_required": bool(data.get("isTwoFactorRequired"))}


def outer_login(guid: str) -> dict[str, Any]:
    """Trade a hand-off GUID for an access token. The GUID is single-use."""
    _require_flow()
    body, cookies = _token_post("outer_login", {"guid": guid, "lcid": profile()["neptun"]["lcid"]})
    return _store_token(_envelope(body, "outer login")["data"], "Account/OuterLogin", cookies)


def refresh() -> dict[str, Any]:
    """Account/GetNewTokens, as the web client does it: an empty POST with cookies.

    The access token lives about 5 minutes (EnvironmentData says so), the
    session 15. This is not a login and carries no captcha risk.
    """
    cached = _read_cached(ignore_expiry=True)
    if not cached or not cached.get("cookies"):
        raise NeptunError("no refreshable session cached - run `elte neptun login`")
    try:
        body, cookies = _token_post("refresh", {}, cached["cookies"], str(cached["token"]))
    except (http.HttpError, json.JSONDecodeError):
        forget_token()
        raise NeptunError("the Neptun session has ended - run `elte neptun login`") from None
    # Unlike every other call, GetNewTokens is NOT wrapped in {"data": ...}: the
    # web client reads n.accessToken straight off the body. The refresh also
    # rotates the session cookie, so a body this client fails to read must not
    # cost the new cookie - that is how the first live refresh killed its session.
    data = body.get("data") if isinstance(body, dict) and "data" in body else body
    if not isinstance(data, dict) or not data.get("accessToken"):
        _write_cached(str(cached["token"]), float(cached.get("expires_at", 0)),
                      note="refresh unreadable", base=cached.get("base"), cookies=cookies)
        keys = sorted(body) if isinstance(body, dict) else type(body).__name__
        raise NeptunError(f"token refresh answered without an accessToken (keys: {keys})")
    return _store_token(data, "Account/GetNewTokens", cookies)


def authenticate(*, code: str | None = None) -> dict[str, Any]:
    """One attempt. No retry, ever, from inside this function.

    The payload keys are the web client's own, `subtituteGUID` misspelling
    included. `token` is the 2FA code and is sent empty when there is no seed,
    which is what the browser does for an account without 2FA.
    """
    _require_flow()
    user, password = credentials()
    otp = code if code is not None else (current_code() or "")
    body: dict[str, Any] = {
        "userName": user,
        "password": password,
        "captcha": "",
        "captchaIdentifier": "",
        "token": otp,
        "subtituteGUID": "",
        "LCID": profile()["neptun"]["lcid"],
    }
    try:
        payload = http.post_json(url_for("authenticate"), body)
    except http.HttpError as exc:
        raise NeptunError(
            f"authentication failed ({exc.status} at {exc.path}). "
            "Not retrying - repeated failures put a captcha on the account."
        ) from None
    data = _envelope(payload, "authentication")["data"]
    if not isinstance(data, dict):
        raise NeptunError("authentication returned no data object")
    if data.get("isCaptchaRequired"):
        raise NeptunLockRisk(
            "Neptun now wants a captcha for this account. Log in once through the "
            "browser to clear it; this client will not guess at captchas."
        )
    token = data.get("accessToken")
    if not isinstance(token, str) or not token:
        if data.get("isTwoFactorRequired"):
            raise NeptunError(
                "Neptun wants a 2FA code and none was accepted. Pass one with "
                "--code. Not retrying on my own."
            )
        raise NeptunError("authentication returned no accessToken")
    minutes = data.get("sessionTimeoutInMinutes")
    minutes = float(minutes) if isinstance(minutes, (int, float)) else 15.0
    expires_at = time.time() + minutes * 60
    _write_cached(token, expires_at, note="Account/Authenticate")
    return {"user": data.get("userName"), "expires_at": expires_at,
            "seconds": int(expires_at - time.time()), "used_totp": bool(otp),
            "two_factor_required": bool(data.get("isTwoFactorRequired"))}


def bearer(*, allow_login: bool = True) -> str:
    cached = _read_cached()
    if cached:
        return str(cached["token"])
    del allow_login
    # Never log in from here. The API's own login is closed at ELTE, so the old
    # authenticate() fallback spent a login attempt for nothing, and the portal
    # route needs a 2FA code and a human decision.
    raise NeptunError("no live Neptun token - run `elte neptun login`")


def call(name: str, **params: Any) -> Any:
    """A GET against a named endpoint with the cached bearer.

    A 401 refreshes the token exactly once, because that is the one retry that
    is not a guess: the token is known-expired, the credentials are known-good.
    Paging defaults come from DEFAULT_PARAMS and any of them can be overridden.
    """
    import urllib.parse

    merged: dict[str, Any] = dict(DEFAULT_PARAMS.get(name, {}))
    merged.update({k: v for k, v in params.items() if v is not None})
    url = url_for(name)
    if merged:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(merged, doseq=True)

    def fetch(token: str) -> Any:
        payload, _ = http.get_json(url, headers={"Authorization": f"Bearer {token}"})
        return _envelope(payload, name)["data"]

    try:
        return fetch(bearer())
    except http.HttpError as exc:
        if exc.status != 401:
            raise NeptunError(f"{exc.status} for {exc.path}") from exc
    refresh()
    try:
        return fetch(bearer())
    except http.HttpError as exc:
        raise NeptunError(f"{exc.status} for {exc.path} after one refresh") from exc


def probe() -> list[dict[str, Any]]:
    """Ask whether each endpoint exists. Sends no credentials, ever.

    A 401 or 405 is a better sign than a 200: it means something is listening
    and it wants auth or another verb. A 404 means the path is wrong.
    """
    rows = []
    for name in sorted(endpoints()):
        url = url_for(name)
        ok, detail = http.reachable(url, follow=False)
        verdict = {
            "401": "exists, wants auth",
            "403": "exists, refused",
            "405": "exists, wrong verb (expected for POST endpoints)",
            "302": "exists, redirects to the login page - that is its way of "
                   "saying unauthenticated",
            "404": "not here",
            "200": "answers without auth - check what it returned",
            "timeout": "no answer to a plain GET; the host may only serve its own app",
            "unreachable": "DNS or TLS failed",
        }.get(detail, "unclear")
        rows.append({"endpoint": name, "url": url, "reachable": ok,
                     "status": detail, "verdict": verdict})
    return rows
