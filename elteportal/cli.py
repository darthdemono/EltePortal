"""`elte` - one command over the ELTE systems.

Every subcommand prints a table by default and a JSON envelope under --json:

    {"ok": true, "command": "canvas courses", "data": ..., "warnings": [...]}

so the output can be piped without parsing prose. Errors use the same envelope
with "ok": false and exit non-zero.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from typing import Any

from . import __version__, archive, canvas, config, files, http, neptun, nicknames, tanrend
from .api import Client, envelope
from .vault import VaultError, seconds_left


class Fail(RuntimeError):
    """Anything the user should see as a clean failure, not a traceback."""


VERIFIER_WARNING = ("Canvas file URLs carry a `verifier` token that grants the "
                    "download to whoever holds the link - do not paste them anywhere.")


def emit(command: str, data: Any, *, as_json: bool, warnings: list[str] | None = None,
         table: str | None = None) -> None:
    if as_json:
        print(json.dumps(envelope(command, data, warnings=warnings), ensure_ascii=False, indent=1))
        return
    if table is not None:
        print(table)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    for warning in warnings or []:
        print(f"warning: {warning}", file=sys.stderr)


def die(command: str, message: str, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(envelope(command, error=message)))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 1


# ------------------------------------------------------------------------ canvas


def cmd_canvas_courses(args: argparse.Namespace) -> Any:
    rows = args.client.data("canvas", "courses")
    if args.term:
        # a nicknamed course reports the nickname as `name`, so the term only
        # survives in original_name - match on both, and on the code
        rows = [c for c in rows if args.term in " ".join(
            str(c.get(k) or "") for k in ("name", "original_name", "course_code"))]
    data = [{"id": c["id"], "code": c.get("course_code"), "name": c.get("name"),
             "original_name": c.get("original_name"), "start": c.get("start_at")}
            for c in rows]
    table = "\n".join(f"{r['id']:6}  {(r['code'] or '')[:34]:36}  {(r['name'] or '')[:60]}"
                      for r in data)
    return data, f"{len(data)} courses\n{table}"


def cmd_canvas_files(args: argparse.Namespace) -> Any:
    rows = args.client.data("canvas", "files", course=args.course)
    data = [{"id": f.get("id"), "name": f.get("display_name"), "size": f.get("size"),
             "url": f.get("url")} for f in rows]
    total = sum(r["size"] or 0 for r in data)
    table = "\n".join(f"{(r['size'] or 0)/1e6:8.2f} MB  {r['name']}" for r in data)
    return data, f"{len(data)} files, {total/1e6:.1f} MB\n{table}"


CANVAS_FILE_COMMANDS = {"canvas files"}


def cmd_canvas_pull(args: argparse.Namespace) -> Any:
    tok = canvas.token()
    rows = canvas.course_files(tok, args.course)
    if args.skip_images:
        rows = [f for f in rows
                if not (f.get("display_name") or "").lower().endswith((".png", ".jpg", ".jpeg"))]
    pulled = []
    for entry in rows:
        target = os.path.join(args.into, (entry.get("display_name") or "unnamed").replace("/", "_"))
        if os.path.exists(target) and not args.force:
            continue
        pulled.append(canvas.download_file(entry, args.into))
    return pulled, f"pulled {len(pulled)} files into {args.into}"


def cmd_canvas_links(args: argparse.Namespace) -> Any:
    tok = canvas.token()
    course_ids = [args.course] if args.course else [c["id"] for c in canvas.courses(tok)]
    found: list[dict[str, str]] = []
    for cid in course_ids:
        try:
            found += [{**row, "course": cid} for row in canvas.outbound_links(tok, cid)]
        except canvas.CanvasError:
            continue
    hosts: dict[str, int] = {}
    for row in found:
        hosts[row["host"]] = hosts.get(row["host"], 0) + 1
    table = "\n".join(f"{count:4}  {host}" for host, count in
                      sorted(hosts.items(), key=lambda kv: -kv[1]))
    return {"hosts": hosts, "links": archive._scrub(found)}, f"{len(hosts)} hosts across {len(course_ids)} courses\n{table}"


def cmd_canvas_upcoming(args: argparse.Namespace) -> Any:
    del args
    tok = canvas.token()
    rows = canvas.upcoming(tok)
    data = [{"title": r.get("title"), "at": r.get("start_at") or
             (r.get("assignment") or {}).get("due_at"), "url": r.get("html_url")} for r in rows]
    table = "\n".join(f"{str(r['at'])[:16]:18}  {r['title']}" for r in data)
    return data, table or "nothing upcoming"


def cmd_canvas_archive(args: argparse.Namespace) -> Any:
    data = archive.archive_account(canvas.token(username=args.account), args.into, workers=args.workers)
    return data, (f"archived {data['course_total']} courses into {args.into}; "
                  f"account endpoint errors: {data['account_errors']}")


def cmd_canvas_nickname(args: argparse.Namespace) -> Any:
    names = nicknames.archive_nicknames(args.archive)
    output = io.StringIO()
    nicknames.run(nicknames=names, apply=args.apply, output=output)
    return {"applied": args.apply, "courses": len(names), "summary": output.getvalue()}, output.getvalue()


# ------------------------------------------------------------------------ neptun


def cmd_neptun_probe(args: argparse.Namespace) -> Any:
    del args
    rows = neptun.probe()
    table = "\n".join(f"{r['status']:>11}  {r['endpoint']:14} {r['verdict']}" for r in rows)
    return rows, f"base {neptun.base_url()}\n{table}"


def cmd_neptun_code(args: argparse.Namespace) -> Any:
    del args
    code = neptun.current_code()
    if not code:
        raise Fail("no TOTP seed - set NEPTUN_TOTP_SECRET, or put one in the "
                   "vault entry 'neptun' under totp_secret")
    left = seconds_left()
    return {"available": True, "valid_for": left}, f"TOTP available ({left}s left); code is never printed"


def cmd_neptun_creds(args: argparse.Namespace) -> Any:
    """Prove the vault answers, without posting anything to Neptun."""
    del args
    report = neptun.credential_status()
    if not report.get("ready"):
        raise Fail(report.get("error", "credentials did not resolve"))
    totp = report["totp"]
    lines = [
        f"base        {report['authenticate_url']}",
        "username    resolved",
        "password    resolved",
        f"totp        {totp['digits']} digits" + (f", {totp['valid_for']}s left"
                                                  if totp.get("valid_for") else ""),
        "token       " + ("cached, %ss left" % report["token_cache"]["seconds_left"]
                          if report["token_cache"]["present"] else "none cached"),
        "",
        "nothing was sent to Neptun.",
    ]
    return report, "\n".join(lines)


def cmd_neptun_login(args: argparse.Namespace) -> Any:
    """The portal route by default: ELTE has closed the API's own login."""
    if args.api:
        result = neptun.authenticate(code=args.code)
        return result, (f"token cached, {result['seconds']}s of life, "
                        f"totp={result['used_totp']}")
    guid = (neptun.guid_from_input(args.guid) if args.guid
            else neptun.portal_login(code=args.code, wait=args.wait))
    result = neptun.outer_login(guid)
    return result, (f"{result['user']}: token cached on {neptun.base_url()}, "
                    f"{result['seconds']}s of life")


def cmd_neptun_logout(args: argparse.Namespace) -> Any:
    del args
    return {"forgotten": neptun.forget_token()}, "token cache cleared"


def cmd_neptun_get(args: argparse.Namespace) -> Any:
    params = {}
    for pair in args.param or []:
        key, _, value = pair.partition("=")
        if not _:
            raise Fail(f"--param wants key=value, got {pair!r}")
        params[key] = value
    payload = neptun.call(args.endpoint, **params)
    if args.endpoint == "calendar_links":
        payload = {"redacted": True, "reason": "calendar export links grant account access"}
    return payload, json.dumps(payload, ensure_ascii=False, indent=1)[:4000]


# ------------------------------------------------------------------------ tanrend


def cmd_tanrend(args: argparse.Namespace) -> Any:
    term = tanrend.term_from_label(args.term)
    rows = args.client.data("timetable", "search", term=term, query=args.query, mode=args.mode)
    table = "\n".join("  ".join(str(v) for k, v in row.items() if not k.startswith("_"))[:180]
                      for row in rows)
    return rows, f"{len(rows)} rows for {args.query} in {term}\n{table}"


# ------------------------------------------------------------------------ files


def cmd_wornox_ls(args: argparse.Namespace) -> Any:
    rows = [row for row in args.client.data("files", "walk", root=args.root)
            if not args.match or args.match.lower() in row["path"].lower()]
    table = "\n".join(row["path"] for row in rows)
    return rows, f"{len(rows)} files\n{table}"


def cmd_wornox_pull(args: argparse.Namespace) -> Any:
    rows = [row for row in args.client.data("files", "walk", root=args.root)
            if args.match.lower() in row["path"].lower()]
    pulled = [files.fetch(row["url"], args.into, name=row["name"]) for row in rows]
    return pulled, f"pulled {len(pulled)} files into {args.into}"


def cmd_webprog(args: argparse.Namespace) -> Any:
    if args.what == "subjects":
        rows = files.webprog_subjects()
        return rows, "\n".join(f"{r.get('name','')[:44]:46} {r.get('url','')}" for r in rows)
    if args.what == "materials":
        rows = files.webprog_materials()
        return rows, "\n".join(f"{r.get('name','')[:44]:46} {r.get('url','')}" for r in rows)
    rows = files.webprog_lectures()
    return rows, "\n".join(f"{r['n']:2}  {r['title'][:40]:42} {r['url']}" for r in rows)


# ------------------------------------------------------------------------ sites


def cmd_sites(args: argparse.Namespace) -> Any:
    rows = args.client.data("sites", "probe", keys=args.only or None)
    table = "\n".join(
        f"{'up ' if r['reachable'] else 'DOWN':5} {r['status']:>13}  {r['key']:12} "
        f"{(r['auth'] or '-'):13} {r['what'][:60]}" for r in rows)
    return rows, table


def cmd_config_check(args: argparse.Namespace) -> Any:
    data = args.client.data("config", "check")
    return data, json.dumps(data, indent=2)


def cmd_provider(args: argparse.Namespace) -> Any:
    params = {}
    for item in args.param or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise Fail("--param requires key=value")
        params[key] = value
    result = args.client.call(args.name, args.action, **params)
    if not result["ok"]:
        raise Fail(result["error"])
    return result["data"], json.dumps(result["data"], ensure_ascii=False, indent=1)


def cmd_reports(args: argparse.Namespace) -> Any:
    from .reports import REPORTS
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = REPORTS[args.cmd]()
    if result:
        raise Fail(f"{args.cmd} report failed; check the configured output directory")
    return {"report": args.cmd, "exit_code": result}, output.getvalue()


# ------------------------------------------------------------------------ wiring


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elte", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable envelope")
    parser.add_argument("--profile", help="institution TOML profile path")
    parser.add_argument("--config", help="config TOML path")
    parser.add_argument("--version", action="version", version=f"elte {__version__}")
    sub = parser.add_subparsers(dest="group", required=True)

    can = sub.add_parser("canvas", help="Canvas LMS, read-only").add_subparsers(
        dest="cmd", required=True)
    p = can.add_parser("courses", help="every course, concluded ones included")
    p.add_argument("--term", help="substring filter on the course name")
    p.set_defaults(fn=cmd_canvas_courses)
    p = can.add_parser("files", help="file list for one course")
    p.add_argument("course", type=int)
    p.set_defaults(fn=cmd_canvas_files)
    p = can.add_parser("pull", help="download a course's files")
    p.add_argument("course", type=int)
    p.add_argument("--into", required=True)
    p.add_argument("--skip-images", action="store_true")
    p.add_argument("--force", action="store_true", help="re-download files already present")
    p.set_defaults(fn=cmd_canvas_pull)
    p = can.add_parser("links", help="every non-Canvas URL a course links out to")
    p.add_argument("--course", type=int, help="one course; omit to sweep all of them")
    p.set_defaults(fn=cmd_canvas_links)
    p = can.add_parser("upcoming", help="upcoming events for the account")
    p.set_defaults(fn=cmd_canvas_upcoming)
    p = can.add_parser("archive", help="download every course and account record locally")
    p.add_argument("--account", required=True, help="optional legacy vault account selector")
    p.add_argument("--into", required=True, help="local account archive directory")
    p.add_argument("--workers", type=int, default=4, help="parallel course downloads (default 4)")
    p.set_defaults(fn=cmd_canvas_archive)
    p = can.add_parser("nickname", help="compare nicknames; --apply enables the only Canvas write")
    p.add_argument("--archive", required=True, help="local course archive root")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_canvas_nickname)

    nep = sub.add_parser("neptun", help="Neptun - endpoints unconfirmed, read the module docstring").add_subparsers(
        dest="cmd", required=True)
    p = nep.add_parser("probe", help="check which endpoints exist, sends no credentials")
    p.set_defaults(fn=cmd_neptun_probe)
    p = nep.add_parser("code", help="check TOTP availability without printing the code")
    p.set_defaults(fn=cmd_neptun_code)
    p = nep.add_parser("creds", help="prove the vault answers; sends nothing to Neptun")
    p.set_defaults(fn=cmd_neptun_creds)
    p = nep.add_parser("login", help="authenticate once and cache the token")
    p.add_argument("--code", help="override the generated TOTP code")
    p.add_argument("--guid", help="a hand-off GUID, or the whole outerlogin?GUID=... URL, "
                        "copied out of a browser login")
    p.add_argument("--wait", type=float, metavar="SECONDS",
                   help="how long to keep asking for the hand-off while the student "
                        "web reports itself full (default 150); no extra login attempts")
    p.add_argument("--api", action="store_true",
                   help="use api/Account/Authenticate, which ELTE answers with a "
                        "redirect to the portal")
    p.set_defaults(fn=cmd_neptun_login)
    p = nep.add_parser("logout", help="drop the cached token")
    p.set_defaults(fn=cmd_neptun_logout)
    p = nep.add_parser("get", help="GET a named endpoint with the cached token")
    p.add_argument("endpoint")
    p.add_argument("--param", action="append", metavar="KEY=VALUE",
                   help="query parameter; repeatable, overrides the paging defaults")
    p.set_defaults(fn=cmd_neptun_get)

    p = sub.add_parser("tanrend", help="public timetable, no login")
    p.add_argument("query", help="subject code, teacher or subject name")
    p.add_argument("--term", required=True)
    p.add_argument("--mode", default="code", choices=sorted(tanrend.MODES))
    p.set_defaults(fn=cmd_tanrend)

    wor = sub.add_parser("wornox", help="the open course file server").add_subparsers(
        dest="cmd", required=True)
    p = wor.add_parser("ls")
    p.add_argument("--match", default="", help="substring filter on the path")
    p.add_argument("--root")
    p.set_defaults(fn=cmd_wornox_ls)
    p = wor.add_parser("pull")
    p.add_argument("match", help="substring the path must contain")
    p.add_argument("--into", required=True)
    p.add_argument("--root")
    p.set_defaults(fn=cmd_wornox_pull)

    p = sub.add_parser("webprog", help="the web-programming department site")
    p.add_argument("what", nargs="?", default="lectures",
                   choices=["lectures", "subjects", "materials"])
    p.set_defaults(fn=cmd_webprog)

    p = sub.add_parser("sites", help="which ELTE systems are up and what each one wants")
    p.add_argument("--only", nargs="*", help="restrict to these keys")
    p.set_defaults(fn=cmd_sites)

    p = sub.add_parser("config", help="validate local config and profile without network")
    p.add_argument("cmd", choices=["check"])
    p.set_defaults(fn=cmd_config_check)

    p = sub.add_parser("provider", help="call an installed provider plugin")
    p.add_argument("name")
    p.add_argument("action")
    p.add_argument("--param", action="append")
    p.set_defaults(fn=cmd_provider)

    p = sub.add_parser("reports", help="write a local Canvas report")
    p.add_argument("cmd", choices=["missing", "grades"])
    p.set_defaults(fn=cmd_reports)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = f"{args.group} {getattr(args, 'cmd', '')}".strip()
    if args.group == "provider":
        command = f"provider {args.name} {args.action}"
    try:
        args.client = Client(args.profile, args.config)
        with config.use(args.client.profile, args.client.settings):
            data, table = args.fn(args)
    except (Fail, VaultError, config.ConfigError, canvas.CanvasError, neptun.NeptunError,
            ValueError, http.HttpError) as exc:
        return die(command, str(exc), as_json=args.json)
    except KeyboardInterrupt:
        return die(command, "interrupted", as_json=args.json)
    warnings = [VERIFIER_WARNING] if command in CANVAS_FILE_COMMANDS else []
    emit(command, data, as_json=args.json, warnings=warnings, table=table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
