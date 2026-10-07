# EltePortal

EltePortal is a toolbox for ELTE students who want to use the university systems they already depend on without living in a pile of browser tabs. It reads Canvas, the bundled ELTE Neptun profile, the public timetable, and selected public course-file sites, then gives you the result in one consistent shape.

It is for the student who wants to find a file, check what is due, search a timetable, or keep a local record of a semester. You can use it as a normal terminal command. If you write scripts, the same toolbox also gives you clean JSON and a small Python library.

## Why use it?

ELTE has more than one place where your academic life happens. Canvas has course pages and files. Neptun holds another part of your student information. Tanrend has the public schedule. A department site may have the one PDF you actually need. None of this is complicated by itself, but moving between all of it gets old fast.

EltePortal puts the read side of those systems behind commands that behave the same way. You can ask for your Canvas courses, download the files from one course, search the timetable, inspect named Neptun records after logging in, or write local reports about missing work and grades. It does not pretend to replace the websites. It makes the repetitive parts less annoying.

It also refuses to be clever with your account. A bad automatic retry can put a captcha or lockout in your way. Regenerating a calendar-export link can break every calendar that uses it. So the tool stops instead of guessing. That is not a missing feature. It is the point.

## What you can do with it

| If you need to... | Use EltePortal to... |
| --- | --- |
| See the courses Canvas knows about | List active, completed, and pending Canvas enrolments. |
| Get course material onto your machine | List or download files from a Canvas course into a folder you choose. |
| Check what is coming up | Read Canvas upcoming events and assignment dates. |
| Keep a local academic record | Archive Canvas course data and write local missing-work or grade reports. |
| Search the timetable | Look up a subject code, teacher, or subject name in the public timetable. |
| Read Neptun data | Log in once, keep a short-lived session locally, then call named read endpoints from the selected profile. |
| Find public teaching material | Browse configured public file servers and check whether configured student sites answer. |

You do not need to use every part. A Canvas-only setup is useful on its own. A profile decides which systems belong to your institution.

## Install and run a first command

From a source checkout, in PowerShell on Linux or macOS:

```powershell
python3 -m venv .venv
& ./.venv/bin/python -m pip install .
& ./.venv/bin/elte --json config check
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install .
& ./.venv/Scripts/elte.exe --json config check
```

The check validates the bundled ELTE profile without contacting a university service or reading a credential. Python 3.11 or newer is required. Package index publication is pending, so install from source for now.

After you configure a Canvas token, these are the sort of commands you will use:

```powershell
& ./.venv/bin/elte --json canvas courses
& ./.venv/bin/elte --json canvas files <COURSE-ID>
& ./.venv/bin/elte --json canvas pull <COURSE-ID> --into <FOLDER>
& ./.venv/bin/elte --json tanrend <SUBJECT-OR-TEACHER> --term <TERM>
```

`--json` is useful for scripts and gives every result the same envelope. Without it, the command prints something intended for a human. Start with `config check`, then configure only the systems you actually use.

## Supported systems and profiles

| Provider | What you can read | What a profile supplies |
| --- | --- | --- |
| Canvas | Courses, assignments, files, upcoming items, and archive data | The instance's HTTPS `/api/v1` base URL |
| Neptun | Named read endpoints through a cached student session | Portal and student API URLs, locale, endpoint paths, and an explicit flow |
| Timetable | Searchable public HTML timetable | Search page URL; the built in parser expects the ELTE table layout |
| Open files | An Apache style directory and a static course site | Server URLs and optional site entries |

The bundled [ELTE profile](elteportal/profiles/elte.toml) is a reference. To add another institution, copy [the minimal example](examples/university.toml), edit its URLs and enabled providers, then run `elte --profile <PATH-TO-PROFILE> --json config check`. This requires no Python for Canvas or sites with the supported formats. A different timetable layout or Neptun login flow needs a provider plugin; a profile must say `flow = "unsupported"` until that implementation is tested. The ELTE Neptun flow is preserved from local observations and has not been publicly verified at other universities. See the [profile reference](docs/profiles.md) and [Neptun notes](docs/neptun.md).

## Configuration

EltePortal reads `$env:ELTE_CONFIG` if set, otherwise `$env:XDG_CONFIG_HOME/elteportal/config.toml`, otherwise `~/.config/elteportal/config.toml` when present. An absent config uses the bundled ELTE profile and environment secrets. A minimal config looks like this:

```toml
profile = "/absolute/path/to/university.toml"

[secrets]
backend = "env"
```

Pass `--config <PATH>` or `--profile <PATH>` before the subcommand for one invocation. `$env:ELTE_PROFILE` overrides the profile path; `$env:CANVAS_BASE_URL`, `$env:NEPTUN_BASE_URL`, and `$env:NEPTUN_PORTAL_URL` override their profile URLs and are checked for HTTPS. `elte --json config check` reports the selected profile and backend, never their secret values. The full field list is in [configuration and profile reference](docs/profiles.md).

## Secrets

Set `$env:CANVAS_API_KEY` for Canvas. Neptun uses `$env:NEPTUN_USER`, `$env:NEPTUN_PASS`, and, for TOTP accounts, `$env:NEPTUN_TOTP_SECRET`. Environment values win over the configured backend. Blank or whitespace only values are refused.

| Backend | Where the secret lives | Setup |
| --- | --- | --- |
| `env` | In the process environment | Default; supply the variables yourself. |
| `keyring` | In your OS keyring | Install `.[keyring]`; store each variable name under service `elteportal`. |
| `file` | In a JSON file you choose | Point `secrets.file` at a regular file owned by you with mode 0600; it is cleartext at rest. |
| `command` | In your password manager | Set each secret name to an argument array under `[secrets.commands]`; stdout is the value, stderr is never shown. |
| `legacy` | In an existing optional vault | Explicitly select this backend; it is for existing setups, not required for a new install. |

The command backend lets you use a manager CLI without putting a command, account name, or secret in the project code. [Secrets guide](docs/secrets.md) gives the exact TOML shapes. `elte --json neptun creds` checks resolution without a login and reports only whether each required value resolved.

## Safety rules

Canvas reads use GET. The sole Canvas write is a course nickname change, and `elte canvas nickname --archive <PATH>` only shows a comparison until you add `--apply`. That matters because a course nickname changes what Canvas displays elsewhere. Credentials are checked before a Neptun login request; an empty password is a local error, not an experiment on your account. Failed authentication is never retried automatically because repeated mistakes can trigger a captcha or lockout. A cached, known expired token may be refreshed once after an authenticated 401. The client never calls Neptun's calendar export link regeneration endpoint because it invalidates existing subscriptions. Signed Canvas file links are redacted in JSON output because the link itself can grant a download. See [safety details](docs/safety.md).

## Output and exit codes

Every successful `--json` result has this versioned envelope:

```json
{"schema_version":"1.0","ok":true,"command":"config check","data":{"profile":"ELTE","schema_version":1,"canvas":true,"neptun_flow":"elte-portal","secrets_backend":"env"},"warnings":[]}
```

Failures add `"error"` and set `"ok":false`. Exit code 0 means success, 1 means a runtime or configuration failure, and 2 means invalid command line arguments. The [JSON Schema](elteportal/schemas/envelope-v1.json) defines this shape. Data fields vary by command; compare parsed JSON, not terminal spacing.

## Use it from Python

The CLI uses the same profile bound client. `Client.call()` returns an envelope and masks unexpected plugin exceptions; `Client.data()` returns the provider's value and lets normal exceptions propagate.

```python
from elteportal import Client

client = Client(profile_path="examples/university.toml")
result = client.call("config", "check")
print(result["ok"], result["data"]["profile"])
```

This example is offline and prints `True Example University`. The older `elteportal.canvas`, `elteportal.nicknames`, and `python -m elteportal.reports` imports remain available through at least one minor release.

## Add a provider or secret backend

A provider package registers `elteportal.providers` entry point to a class with `run(action, **params)`. A secret backend registers `elteportal.secrets` to a callable accepting `(env_name, settings)`. Both are loaded only when selected. A plugin must not return raw credentials or add an implicit write; [the plugin guide](docs/plugins.md) has a minimal working package and an offline test.

## What this is not

It is not a universal Neptun client, a scheduler, a grade authority, or permission to scrape an institution. It will not submit assignments, enroll in courses, bypass MFA, solve captchas, or regenerate calendar links. A parser cannot make an undocumented deployment portable by changing a hostname. Nice try, hostname.

## FAQ and troubleshooting

**Can I use an OAuth login instead of a Canvas token?** Canvas documents OAuth2, but this client currently accepts an access token that you supply; it does not register an OAuth app. Use only a token you are authorized to create.

**Why is a Neptun login refused?** Run `elte --json config check`, then `elte --json neptun creds`. A missing value, unsupported flow, or spent TOTP code is a reason to stop and inspect the account in a browser. The client will not retry for you.

**Why are some Canvas courses or files absent?** Canvas permissions and publication state determine what its API returns. A concluded enrollment may still be visible, while an unpublished course can deny its files. Check the course in Canvas before treating the client result as a complete archive.

**Where do local reports go?** By default, under your user data directory in `elteportal/reports`. Set `$env:ELTE_REPORT_DIR` to choose another location.

## Responsible use

[Instructure's API policy](https://www.instructure.com/policies/canvas-api-policy) and [throttling documentation](https://developerdocs.instructure.com/services/canvas/basics/file.throttling) apply to Canvas access. No ELTE specific automated access allowance or fixed Neptun rate limit was verified for this release. Ask your institution before automating repeated collection, keep requests modest, and stop on authentication failure. This tool never claims that an available endpoint grants permission to use it.

## Contributing and licence

Start with [CONTRIBUTING.md](CONTRIBUTING.md), use invented offline fixtures, and report security issues privately using [SECURITY.md](SECURITY.md). EltePortal is licensed under [Apache-2.0](LICENSE), with the accompanying [NOTICE](NOTICE).
