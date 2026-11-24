"""`python -m radar verify`: check every source and registry URL once.

Writes nothing to the database. Prints a Markdown report (and adds it to the
GitHub Actions run summary when it runs there), so you can see at a glance:
  * which sources work, how many items they return, how fresh they are
  * which registry pages load, redirect, 404, or look JavaScript-rendered
  * which pages never mention the current/next year (a hint the program may be
    stale or discontinued)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from bs4 import BeautifulSoup

from radar.ats_discovery import discovery_report
from radar.config import ROOT, Config
from radar.http import BlockedError, Http
from radar.sources import build_source
from radar.timeutil import today_local


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")[:120]


def verify_sources(cfg: Config, http: Http, only: str | None = None) -> list[str]:
    lines = ["## Sources", "", "| Source | Result | Items | Upstream updated | Sample titles |", "|---|---|---|---|---|"]
    for source_id, options in cfg.sources.items():
        if only and source_id != only:
            continue
        if not only and not options.get("enabled", True):
            lines.append(f"| {source_id} | disabled | | | |")
            continue
        started = time.monotonic()
        try:
            result = build_source(source_id, options, cfg, http).fetch()
        except Exception as exc:  # noqa: BLE001
            lines.append(f"| {source_id} | ❌ {_cell(type(exc).__name__ + ': ' + str(exc))} | | | |")
            continue
        took = time.monotonic() - started
        sample = "; ".join(f"{i.organization}: {i.title}" for i in result.items[:3])
        extra = f" ({len(result.failed_scopes)} sub-feeds failed)" if result.failed_scopes else ""
        lines.append(
            f"| {source_id} | ✅ {took:.1f}s{extra} | {len(result.items)} | {result.upstream_updated_at or '-'} | {_cell(sample)} |"
        )
        for warning in result.warnings:
            lines.append(f"|  | ⚠️ {_cell(warning)} | | | |")
    return lines


LOGIN_HINTS = ("/login", "webauth", "weblogin", "/sso/", "idp.stanford.edu", "signin")


def registry_urls(cfg: Config) -> list[tuple[str, str, str, bool]]:
    """(registry, id, url, watched?) for every URL in the YAML registries."""
    urls = []
    for entry in (cfg.load_file("early_programs.yaml") or {}).get("programs", []) or []:
        urls.append(("early_programs", entry["id"], entry["url"], entry.get("watch", True)))
    for entry in (cfg.load_file("watched_pages.yaml") or {}).get("pages", []) or []:
        urls.append(("watched_pages", entry["id"], entry["url"], entry.get("watch", True)))
    return urls


def verify_urls(cfg: Config, http: Http) -> list[str]:
    years = {str(today_local().year), str(today_local().year + 1)}
    lines = [
        "", "## Registry URLs", "",
        f"Mentions = which of {sorted(years)} appear on the page (none = possibly stale).", "",
        "| Registry | Id | Result | Text chars | Mentions | Final URL |", "|---|---|---|---|---|---|",
    ]
    for registry, entry_id, url, watched in registry_urls(cfg):
        label = entry_id if watched else f"{entry_id} (watch: false)"
        try:
            response = http.get(url)
        except BlockedError as exc:
            lines.append(f"| {registry} | {label} | 🚫 blocks bots - set watch: false ({_cell(exc)}) | | | {_cell(url)} |")
            continue
        except Exception as exc:  # noqa: BLE001
            lines.append(f"| {registry} | {label} | ❌ {_cell(type(exc).__name__ + ': ' + str(exc))} | | | {_cell(url)} |")
            continue
        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        found = sorted(y for y in years if y in text)
        final = str(response.url)
        verdict = "✅" if found else "⚠️ no current-cycle year"
        if len(text) < 300:
            verdict = "⚠️ almost no text (JavaScript-rendered or blocked?)"
        if any(hint in final.lower() for hint in LOGIN_HINTS):
            verdict = "🔒 redirects to a login page - must not be watched"
        lines.append(
            f"| {registry} | {entry_id} | {verdict} (HTTP {response.status_code}) | {len(text)} | {', '.join(found) or '-'} | "
            f"{_cell(final if final != url else 'same')} |"
        )
    return lines


def run_verify(cfg: Config, http: Http, only: str | None = None, check_urls: bool = True) -> int:
    lines = ["# Opportunity Radar verification report", "", f"Run date: {today_local().isoformat()}", ""]
    lines += verify_sources(cfg, http, only)
    if check_urls and not only:
        # Separate client with fewer retries so a dead page doesn't stall the report.
        url_http = Http.from_config(cfg)
        url_http.max_retries = 1
        lines += verify_urls(cfg, url_http)
        # Probing guesses mostly returns 404s, so never retry those requests.
        probe_http = Http.from_config(cfg)
        probe_http.max_retries = 0
        lines += discovery_report(cfg, probe_http)
    report = "\n".join(lines) + "\n"
    print(report)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "verify.md").write_text(report, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(report)
    return 0
