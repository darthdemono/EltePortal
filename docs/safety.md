# Safety contract

The account protections are executable rules, not advice to remember later. Offline tests in `tests/test_core.py` and `tests/test_release.py` cover the failure paths.

1. Canvas read methods use GET. The nickname PUT lives in `nicknames.py`, defaults to comparison, and requires `--apply` in the CLI or `apply=True` in the direct function. [Canvas documents](https://developerdocs.instructure.com/services/canvas/resources/users) that nicknames change the name displayed by later API calls and parts of the web UI.
2. A Neptun username or password that resolves to blank is refused before a POST. Repeated failed logins can lead to a captcha or account lock. [BGE explicitly warns of a lock after repeated failures](https://neptun1.uni-bge.hu/hallgato/Login.aspx); an exact ELTE threshold was not verified.
3. Failed authentication is attempted once per invocation. An authenticated GET that receives a 401 can refresh one cached, known good session and retry once. No other automatic login retry exists.
4. `Calendar/GenerateNewLinksForCalendarExport` is absent from the shipped endpoint map and rejected from profile or environment overrides because it invalidates old export links. The impact was observed in the existing ELTE implementation; a public vendor statement was not found.
5. Authenticated HTTP requests do not follow redirects. Canvas pagination also refuses next links on another origin, so a hostile Link header cannot receive the token.
6. Signed Canvas file URLs are redacted in the CLI JSON and local archive metadata. A URL with a verifier can authorize a download even without the API token.
7. Unexpected plugin exception text is masked in `Client.call()` and the CLI. Do not put credentials in plugin data, warnings, or logs; plugin code runs in the caller's process and must be trusted before installation.

Canvas publishes [dynamic API throttling behavior](https://developerdocs.instructure.com/services/canvas/basics/file.throttling) and an [API policy](https://www.instructure.com/policies/canvas-api-policy). No ELTE specific automation allowance or Neptun numeric rate limit was verified. Do not schedule Neptun login attempts or infer permission from an accessible endpoint. The tool never submits assignments, enrolls in classes, bypasses 2FA, solves captchas, or regenerates calendar links.
