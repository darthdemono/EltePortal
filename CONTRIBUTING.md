# Contributing

Please start with a small, reproducible change. For a new institution, submit a profile containing only public URLs and source citations. For a different login protocol or timetable layout, add a provider with offline, invented fixtures. Do not include HAR files, cookies, access tokens, real student records, or screenshots with personal data.

The project supports Python 3.11 and newer. Run `python -m pytest -q`, `ruff check .`, and `mypy elteportal` before proposing code. The CI workflow also builds sdist and wheel. Keep Canvas reads GET only; a write must be explicit and dry run by default. Any Neptun authentication change needs a test showing one failed attempt produces no retry.

Document a changed public command or schema in the README and changelog. Security reports should follow [SECURITY.md](SECURITY.md), not a public issue.
