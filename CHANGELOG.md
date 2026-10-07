# Changelog

## 1.0.0

- Added institution profiles, a validated TOML config, an offline config check, provider entry points, and a small library client.
- Added environment, permission checked file, optional keyring, command, and explicit legacy vault secret backends.
- Versioned the JSON envelope and declared exit codes. Kept legacy imports and `elte` commands for at least one minor release.
- Enforced no empty credentials, no authentication retry, an explicit nickname write, and token safe redirect and pagination handling with offline tests.
- Added public documentation, CI, and Apache-2.0 licence files.
