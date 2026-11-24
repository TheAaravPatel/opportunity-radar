"""Command-line entry point:  python -m radar <command> [options]

Commands
  fetch      run sources and update the database
             --group jobs|daily   which schedule group (see sources.yaml)
             --source ID          run just one source (even if disabled)
  dashboard  build the static site into site/ (index.html, data.json, deadlines.ics)
  digest     build the daily email and send it (or --dry-run to write out/digest.html)
  verify     fetch every source + load every registry URL once, print a report;
             writes nothing to the database (used by the "Verify sources" workflow)

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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
