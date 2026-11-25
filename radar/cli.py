"""Command-line entry point:  python -m radar <command> [options]

Commands
  fetch      run sources and update the database
             --group jobs|daily   which schedule group (see sources.yaml)
             --source ID          run just one source (even if disabled)
  dashboard  build the static site into site/ (index.html, data.json, deadlines.ics)
  digest     build the daily email and send it (or --dry-run to write out/digest.html)
  verify     fetch every source + load every registry URL once, print a report;
             writes nothing to the database (used by the "Verify sources" workflow)
  discover   weekly (needs ANTHROPIC_API_KEY): Claude searches the web for new
             opportunities -> Suggested tab (--dry-run: print, store nothing)
  discovery-brief            this week's instructions for the Claude Code discovery task
  import-findings FILE       store the finds that task sends (import-findings workflow)
  approve ID / reject ID     decide on a suggestion (approve adds it to the config)
  decide "Approve suggestion #42"   the same, from a GitHub issue title (approve workflow)

Global options: --db PATH (default data/radar.db), --config DIR, -v for debug logs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from radar import db
from radar.config import DEFAULT_DB_PATH, ROOT, load_config
from radar.http import Http


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar", description="Opportunity Radar")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--config", default=None, help="config directory (default: ./config)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="run sources")
    p_fetch.add_argument("--group", default=None)
    p_fetch.add_argument("--source", default=None)

    p_dash = sub.add_parser("dashboard", help="build the static dashboard")
    p_dash.add_argument("--out", default=str(ROOT / "site"))

    p_digest = sub.add_parser("digest", help="build + send the email digest")
    p_digest.add_argument("--dry-run", action="store_true", help="don't send; write out/digest.html")
    p_digest.add_argument("--out", default=str(ROOT / "out"))
    p_digest.add_argument("--force", action="store_true", help="send even if nothing is new or due")

    p_verify = sub.add_parser("verify", help="check every source and URL once")
    p_verify.add_argument("--source", default=None)
    p_verify.add_argument("--skip-urls", action="store_true", help="only check sources, not registry URLs")
    p_verify.add_argument("--discovery-only", action="store_true", help="only guess job boards for ats: unknown companies")

    p_probe = sub.add_parser("probe", help="print the structure of public pages/feeds (debugging aid)")
    p_probe.add_argument("urls", nargs="+")

    p_discover = sub.add_parser("discover", help="weekly web search for new opportunities (needs ANTHROPIC_API_KEY)")
    p_discover.add_argument("--dry-run", action="store_true")
    sub.add_parser("discovery-brief", help="print this week's discovery instructions (for the Claude Code task)")
    p_import = sub.add_parser("import-findings", help="store finds sent by the Claude Code discovery task")
    p_import.add_argument("file", help="JSON file: {\"suggestions\": [...]}")
    p_import.add_argument("--focus", default="")
    p_import.add_argument("--searches", type=int, default=0)
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"{name} a suggestion by its number")
        p.add_argument("id", type=int)
    p_decide = sub.add_parser("decide", help="approve/reject from an issue title like 'Approve suggestion #42'")
    p_decide.add_argument("title")
    p_decide.add_argument("--message-file", default=None, help="also write the reply here (for the workflow)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(Path(args.config) if args.config else None)

    if args.command == "fetch":
        from radar.pipeline import run_group

        conn = db.connect(args.db)
        http = Http.from_config(cfg)
        reports = run_group(conn, cfg, http, group=args.group, only=args.source)
        db.compact(conn)
        print("\n".join(r.summary() for r in reports) or "no sources matched")
        # Exit code 0 even if some sources failed: failures are reported in the
        # dashboard/digest, and the workflow must still save the database.
        return 0

    if args.command == "dashboard":
        from radar.dashboard.build import build_dashboard

        conn = db.connect(args.db)
        out = build_dashboard(conn, cfg, Path(args.out))
        print(f"dashboard written to {out}")
        return 0

    if args.command == "digest":
        from radar.digest import run_digest

        conn = db.connect(args.db)
        return run_digest(conn, cfg, dry_run=args.dry_run, out_dir=Path(args.out), force=args.force)

    if args.command == "verify":
        from radar.verify import run_verify

        http = Http.from_config(cfg)
        return run_verify(cfg, http, only=args.source, check_urls=not args.skip_urls, discovery_only=args.discovery_only)

    if args.command == "probe":
        from radar.probe import probe

        return probe(args.urls, Http.from_config(cfg))

    if args.command == "discover":
        import json

        from radar.discovery import Discovery

        summary = Discovery(db.connect(args.db), cfg).run(dry_run=args.dry_run)
        print(json.dumps(summary, indent=2))
        # A failed run still exits 0 so the workflow saves the database and the
        # dashboard shows the error; the summary above says what went wrong.
        return 0

    if args.command == "discovery-brief":
        from radar.discovery import brief

        print(brief(cfg))
        return 0

    if args.command == "import-findings":
        import json

        from radar.discovery import Discovery, clean_findings

        try:
            data = json.loads(Path(args.file).read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            print(f"findings are not valid JSON: {exc}")
            return 1
        found = clean_findings(data, int(cfg.get("discovery.max_suggestions", 8)))
        summary = Discovery(db.connect(args.db), cfg).store_findings(
            found, args.focus or "unknown", args.searches, via="Claude Code task")
        print(json.dumps(summary, indent=2))
        return 0

    if args.command in ("approve", "reject", "decide"):
        from radar.approve import DecisionError, decide, parse_title

        conn = db.connect(args.db)
        try:
            action, number = parse_title(args.title) if args.command == "decide" else (args.command, args.id)
            message, code = decide(conn, action, number), 0
        except DecisionError as exc:
            message, code = f"Nothing changed: {exc}", 1
        print(message)
        if getattr(args, "message_file", None):
            Path(args.message_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.message_file).write_text(message + "\n", encoding="utf-8")
        return code

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
