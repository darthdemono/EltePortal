# Secret backends

`vault.secret(name)` checks the environment first, then one backend selected in `[secrets]` or by `$env:ELTE_SECRET_BACKEND`. Empty values fail closed. The supported names are `CANVAS_API_KEY`, `NEPTUN_USER`, `NEPTUN_PASS`, and `NEPTUN_TOTP_SECRET`. The package computes an RFC 6238 code locally from the seed and never prints the code.

The default `env` backend reads only environment variables. Its values are inherited by child processes, so use a short lived shell and do not paste values into issues or logs. The optional `keyring` backend calls Python keyring's `get_password(service, name)` with service `elteportal` by default. Install `.[keyring]` and use your OS keyring to set the four names. The keyring project's [official API documentation](https://keyring.readthedocs.io/en/stable/) covers `set_password` and `get_password`; availability depends on the host's configured keyring.

The `file` backend reads a JSON object mapping the names to strings. It refuses anything other than a regular file owned by the current user with no group or world permissions. The file is cleartext at rest, so place it on a trusted filesystem and give it mode 0600 before running the tool.

```toml
[secrets]
backend = "file"
file = "/absolute/path/to/private-secrets.json"
```

The `command` backend invokes argument arrays without a shell. Each command must print exactly one value to stdout and exit 0. Stderr is captured but never echoed. Do not put a secret in an argument or in the TOML file. The following Python example is only a format illustration; replace the command with your manager's documented read operation.

```toml
[secrets]
backend = "command"

[secrets.commands]
CANVAS_API_KEY = ["python", "get_secret.py", "CANVAS_API_KEY"]
```

Bitwarden's [official CLI guide](https://bitwarden.com/help/cli/) documents `bw get password <id>` and `bw get totp <id>`. The package does not hardcode Bitwarden or 1Password because vault item selectors and output formats are user specific; the command backend is the bridge. No 1Password command syntax was verified for this release.

The `legacy` backend is retained for an existing installation and selected explicitly. It reads legacy provider and field names inside the compatibility module. It is not the default, and no private binary path is packaged. An external backend can register an `elteportal.secrets` entry point; [plugins.md](plugins.md) gives the callable contract.

`elte --json neptun creds` resolves credentials locally, reports only whether each required value resolved, and sends no network request. It is a diagnostic, not permission to retry login. The token cache is a separate derived session file under the user cache directory with mode 0600.
