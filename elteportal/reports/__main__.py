"""Run one or both Canvas archive reports."""
from __future__ import annotations

import sys

from . import REPORTS


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print(f"usage: python -m elteportal.reports [{' | '.join(REPORTS)}]")
        return 0
    unknown = [arg for arg in args if arg not in REPORTS]
    if unknown:
        print(f"unknown report(s): {', '.join(unknown)}. known: {', '.join(REPORTS)}",
              file=sys.stderr)
        return 2
    failures = 0
    for name in args or list(REPORTS):
        try:
            failures += 1 if REPORTS[name]() else 0
        except Exception as exc:
            print(f"{name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
