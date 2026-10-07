# Profiles and configuration

A profile is a TOML file with `schema_version = 1`, a nonempty `name`, and `[canvas]`, `[neptun]`, `[timetable]`, `[files]`, and `[sites]` sections. Copy [`examples/university.toml`](../examples/university.toml) to start a new institution. Run `elte --profile <PATH-TO-PROFILE> --json config check` before any network call. The machine readable schema is [`profile-v1.json`](../elteportal/schemas/profile-v1.json); the runtime validator is authoritative for URL and safety restrictions.

| Field | Required behavior |
| --- | --- |
| `canvas.enabled`, `canvas.base_url` | If enabled, the URL must use HTTPS and end in `/api/v1`. |
| `neptun.enabled`, `neptun.flow` | Choose `elte-portal` only for a verified matching flow; otherwise use `unsupported` and a separate provider plugin. |
| `neptun.portal_url`, `neptun.api_url` | HTTPS URLs; the API URL ends in `/api`. Required when Neptun is enabled. |
| `neptun.lcid`, `neptun.student_host_suffix` | Integer locale code and allowed student host suffix for the ELTE portal flow. |
| `[neptun.endpoints]` | Named relative API paths. Regenerating calendar export links is always rejected. Different deployments need independently verified paths. |
| `timetable.enabled`, `timetable.url` | HTTPS search page URL. The built in parser expects the ELTE HTML table and query parameters. |
| `[files]` | Named HTTP or HTTPS roots. `wornox` and `webprog` are legacy command aliases; plugins can support other layouts. |
| `[[sites.entries]]` | Optional `key`, `url`, `auth`, `what` for the `sites` probe. |

Profile URLs cannot contain embedded usernames, passwords, or URL fragments. Disabled providers need their section but can omit URLs. This lets a Canvas only university work without pretending its Neptun deployment shares ELTE's portal.

The config search path is `$env:ELTE_CONFIG` when set, then `$env:XDG_CONFIG_HOME/elteportal/config.toml`, then `~/.config/elteportal/config.toml` when present. The CLI's `--config` overrides the search. A relative `profile` path in a config file is relative to that config file. `$env:ELTE_PROFILE` or CLI `--profile` overrides the selected profile, and the specific URL environment overrides are `$env:CANVAS_BASE_URL`, `$env:NEPTUN_BASE_URL`, `$env:NEPTUN_PORTAL_URL`. Each URL override is checked for HTTPS. No settings file is written automatically.

```toml
profile = "/absolute/path/to/university.toml"

[secrets]
backend = "env"
```

The profile is not a credential store. Put secrets in one of the backends described in [secrets.md](secrets.md).
