"""HTTP plumbing shared by every adapter.

Two rules hold everywhere in this package. A URL may appear in an error message,
a credential may not. And nothing here retries an authenticated request on its
own: a retry loop against a login endpoint is how an account gets locked, so the
decision to try again belongs to the caller, once, deliberately.
"""
from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = "EltePortal/1.0"
# The portal Session only. Matches the Brave build in reference/*.har.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TIMEOUT = 30


class HttpError(RuntimeError):
    """A request failed. Carries the status and the path, never a secret."""

    def __init__(self, status: int | str, url: str, body: str = "") -> None:
        parts = urllib.parse.urlsplit(url)
        self.status = status
        self.path = f"{parts.hostname or ''}{parts.path}"
        self.body = ""  # response bodies may contain credentials or personal data
        super().__init__(f"HTTP {status} for {self.path}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 302 to a login page is an answer, not a detour worth following."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _headers(message: Any) -> dict[str, str]:
    """Response headers as a dict, without losing all but the last Set-Cookie."""
    head = dict(message)
    cookies = message.get_all("Set-Cookie") or []
    if cookies:
        head["Set-Cookie"] = "\n".join(cookies)
    return head


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = TIMEOUT,
    follow: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    """Return (status, body, headers). Raises HttpError on a non-2xx answer."""
    if any(key.casefold() in {"authorization", "cookie"} for key in (headers or {})):
        follow = False  # never forward an authenticated request across a redirect
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", UA)
    req.add_header("Accept-Encoding", "gzip")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.urlopen if follow else urllib.request.build_opener(_NoRedirect).open
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, raw, _headers(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        if exc.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        if not follow and 300 <= exc.code < 400:
            # Neptun answers a POST with 302 and puts the payload in the body
            # anyway. Following it loses the body and lands on an HTML page,
            # which is what a JSON parse error here really means.
            return exc.code, raw, _headers(exc.headers)
        raise HttpError(exc.code, url, raw.decode("utf-8", "replace")) from None
    except urllib.error.URLError as exc:
        raise HttpError("unreachable", url, str(exc.reason)) from None
    except TimeoutError:
        raise HttpError("timeout", url) from None


#: Neptun's API answers a request without this header with the SPA's index.html,
#: status 200, chunked - which looks exactly like a parser bug and is not one.
JSON_HEADERS = {"Accept": "application/json"}


def get_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
    status, raw, head = request(url, headers={**JSON_HEADERS, **(headers or {})}, follow=False)
    if 300 <= status < 400:
        # An expired bearer is answered 302 -> the portal, with WWW-Authenticate
        # and "Authorization has been denied" in the body. Following it lands on
        # HTML and surfaces as a JSON parse error; it is a 401 in all but name.
        if "Bearer" in head.get("WWW-Authenticate", "") or b"has been denied" in raw:
            raise HttpError(401, url, raw.decode("utf-8", "replace"))
        raise HttpError(status, url, raw.decode("utf-8", "replace"))
    try:
        return json.loads(raw.decode("utf-8", "replace")), head
    except json.JSONDecodeError:
        raise HttpError("invalid-json", url) from None


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    body = json.dumps(payload).encode("utf-8")
    head = {"Content-Type": "application/json", **JSON_HEADERS, **(headers or {})}
    _, raw, _ = request(url, method="POST", headers=head, data=body, follow=False)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise HttpError("invalid-json", url) from None


def get_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    _, raw, _ = request(url, headers=headers)
    return raw.decode("utf-8", "replace")


def reachable(url: str, timeout: int = 12, *, follow: bool = True) -> tuple[bool, str]:
    """Probe a URL without caring what it returns. Used by `elte sites`."""
    try:
        status, raw, _ = request(url, timeout=timeout, follow=follow)
        text = raw.decode("utf-8", "replace")
        if "ELTE-n kívülről nem elérhető" in text or "restricted to ELTEnet" in text:
            return False, "eltenet-only"
        return True, str(status)
    except HttpError as exc:
        if isinstance(exc.status, int):
            return True, str(exc.status)
        return False, str(exc.status)


class Session:
    """A cookie-keeping browser stand-in, for the one place that needs one.

    Neptun's login is an ASP.NET Core form: an antiforgery cookie has to travel
    with the token hidden in the page, and the answer is a chain of redirects
    that ends on another host. urllib does that fine as long as something holds
    the jar, which is all this is.
    """

    def __init__(self, *, timeout: int = TIMEOUT) -> None:
        import http.cookiejar

        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.timeout = timeout
        self.last_url: str | None = None

    def open(self, url: str, *, data: dict[str, str] | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        """GET, or POST a form when `data` is given. Returns (status, text, final url)."""
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body,
                                     method="POST" if body is not None else "GET")
        # This request follows the browser's portal hand-off behavior.
        req.add_header("User-Agent", BROWSER_UA)
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;"
                                 "q=0.9,image/avif,image/webp,*/*;q=0.8")
        req.add_header("Accept-Language", "en-US,en;q=0.8,hu;q=0.6")
        req.add_header("Upgrade-Insecure-Requests", "1")
        req.add_header("Sec-Fetch-Dest", "document")
        req.add_header("Sec-Fetch-Mode", "navigate")
        req.add_header("Sec-Fetch-Site", "same-origin" if self.last_url else "none")
        req.add_header("Sec-Fetch-User", "?1")
        if self.last_url and body is None:
            req.add_header("Referer", self.last_url)
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.add_header("Origin", "{0}://{1}".format(*urllib.parse.urlsplit(url)[:2]))
            req.add_header("Referer", self.last_url or url)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                self.last_url = resp.url
                return resp.status, raw.decode("utf-8", "replace"), resp.url
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            raise HttpError(exc.code, url, raw.decode("utf-8", "replace")) from None
        except urllib.error.URLError as exc:
            raise HttpError("unreachable", url, str(exc.reason)) from None


def forms(html: str) -> list[tuple[str, dict[str, str]]]:
    """Every form on the page as (action, fields), in document order."""
    import html as html_module
    import re

    out: list[tuple[str, dict[str, str]]] = []
    for match in re.finditer(r"<form([^>]*)>(.*?)</form>", html, re.S | re.I):
        action = re.search(r'action="([^"]*)"', match.group(1))
        fields: dict[str, str] = {}
        for tag in re.finditer(r"<input[^>]*>", match.group(2), re.I):
            name = re.search(r'name="([^"]+)"', tag.group(0))
            if not name:
                continue
            value = re.search(r'value="([^"]*)"', tag.group(0))
            # A hidden timestamp comes through as ...46.2237948&#x2B;02:00 and the
            # server will not parse its own value back with the entity in it.
            fields[name.group(1)] = html_module.unescape(value.group(1)) if value else ""
        out.append((action.group(1) if action else "", fields))
    return out


def form_fields(html: str, *, action_contains: str = "") -> dict[str, str]:
    """Every named input of the first matching form, hidden ones included."""
    import re

    import html as html_module

    for form in re.finditer(r"<form[^>]*>(.*?)</form>", html, re.S | re.I):
        opening = html[form.start():form.start() + (form.group(0).index(">") + 1)]
        if action_contains and action_contains.lower() not in opening.lower():
            continue
        fields: dict[str, str] = {}
        for tag in re.finditer(r"<input[^>]*>", form.group(1), re.I):
            name = re.search(r'name="([^"]+)"', tag.group(0))
            if not name:
                continue
            value = re.search(r'value="([^"]*)"', tag.group(0))
            fields[name.group(1)] = html_module.unescape(value.group(1)) if value else ""
        if fields:
            return fields
    return {}
