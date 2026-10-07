# Architecture

EltePortal has a narrow library surface: `Client`, `envelope`, and a provider contract. The CLI parses arguments, creates a `Client`, runs the same provider objects, and formats a versioned envelope. Existing modules remain importable as compatibility shims through at least one minor release.

| Layer | Responsibility |
| --- | --- |
| `config.py` | Load TOML settings and a validated institution profile, with a context local to one client call. |
| `providers.py` | Built in provider objects and `elteportal.providers` entry point discovery. |
| `api.py` | Profile bound `Client.data()` and safe `Client.call()` envelope. |
| `canvas.py`, `neptun.py`, `tanrend.py`, `files.py`, `sites.py` | Protocol specific reads. Canvas uses GET only; the explicit nickname action is separate. |
| `vault.py` | Environment override and one selected secret backend, plus local TOTP generation. |
| `http.py` | Standard library requests. Authenticated requests do not follow redirects. |

`Client.call()` masks unexpected plugin exception text because plugins can accidentally include credentials in exceptions. `Client.data()` is for callers that want native exceptions and accept responsibility for handling them. The CLI prints the shared envelope with `schema_version = "1.0"` under `--json`; success is exit 0, handled failure exit 1, parser usage error exit 2. The schema lives in [`elteportal/schemas/envelope-v1.json`](../elteportal/schemas/envelope-v1.json).

The current implementation keeps a small number of default profile values as compatibility constants at import time. Active requests resolve the selected profile at call time. The default profile is packaged as [`elte.toml`](../elteportal/profiles/elte.toml); it is data, not a Python fork.

Reports write local markdown to `$env:ELTE_REPORT_DIR` or the user's data directory. They are not scheduled by this package. `nicknames.py` is the only Canvas write path and requires explicit `apply=True` even when called directly.
