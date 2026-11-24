"""The page-watcher source: checks the pages behind our registries every day.

  watched_pages.yaml -> pages       Stanford program pages
  watched_pages.yaml -> job_pages   employer job-search pages with no public
                                    job API (only "page changed" alerts)
  early_programs.yaml -> programs   early-career program pages

For Stanford and early-program pages it adds what the page itself says to
the program's existing card: deadlines found on the page, "applications are
now open" wording, and whether the page has disappeared (see
refresh_registry_statuses in registries.py).

This source is special: it remembers what each page said last time (the
pages / page_snapshots tables), so the pipeline gives it the database as
`self.conn`. The Verify workflow runs it without a database: it then only
fetches the pages and reports what it found.
"""

from __future__ import annotations

import logging

import httpx

from radar.llm import Claude
from radar.models import FetchResult, Item
from radar.sources.base import Source, register
from radar.sources.registries import early_program_items, parse_deadlines, registry_status, watched_page_items
from radar.timeutil import now_iso, today_local
from radar.watcher import snapshot
from radar.watcher.deadlines import deadline_excerpt, find_deadlines, worth_asking_claude
from radar.watcher.fetch import PageFetcher, RobotsDisallowed
from radar.watcher.signals import page_signal, years_seen
from radar.watcher.text import page_lines

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 50   # a program page with less text than this didn't load properly
# kind -> builds the same Item the registry source makes, so the two merge into one card
CARD_BUILDERS = {"stanford": watched_page_items, "early_program": early_program_items}


@register("page_watcher")
class PageWatcherSource(Source):
    def entries(self) -> list[tuple[dict, str]]:
        pages = self.cfg.load_file(self.options.get("file", "watched_pages.yaml")) or {}
        entries = [(e, "stanford") for e in pages.get("pages") or []] + \
                  [(e, "job_page") for e in pages.get("job_pages") or []]
        if self.options.get("programs_file"):
            programs = self.cfg.load_file(self.options["programs_file"]) or {}
            entries += [(e, "early_program") for e in programs.get("programs") or []]
        return [(e, kind) for e, kind in entries if e.get("watch", True) and e.get("url")]

    def fetch(self) -> FetchResult:
        conn = self.conn
        self.claude = Claude(conn, self.cfg) if conn is not None else None
        self.today, self.now = today_local(), now_iso()
        entries = self.entries()

        result = FetchResult(items=[])
        with PageFetcher(self.http) as fetcher:
            for entry, kind in entries:
                if conn is not None:
                    snapshot.save_page_info(conn, entry["id"], {**entry, "name": _name(entry)}, kind)
                try:
                    item, note = self._check(fetcher, conn, entry, kind)
                except RobotsDisallowed as exc:
                    self._failed(conn, result, entry["id"], f"{exc} - set watch: false")
                    continue
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    self._failed(conn, result, entry["id"], f"HTTP {code} from {entry['url']}", gone=code in (404, 410))
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad page never stops the others
                    self._failed(conn, result, entry["id"], f"{type(exc).__name__}: {exc}")
                    continue
                if item:
                    result.items.append(item)
                log.info("%s: %s", entry["id"], note)
                if conn is None:
                    result.warnings.append(f"{entry['id']}: {note}")   # the Verify report shows these
            if fetcher.browser_error:
                result.warnings.append(f"browser unavailable for JavaScript pages: {fetcher.browser_error[:200]}")
        if entries and len(result.failed_scopes) == len(entries):
            raise RuntimeError(f"all {len(entries)} watched pages failed: " + "; ".join(result.warnings[:3]))
        return result

    def _failed(self, conn, result: FetchResult, page_id: str, error: str, gone: bool = False) -> None:
        # A failed page keeps its earlier deadlines (failed_scopes protects them).
        result.failed_scopes.add(page_id)
        result.warnings.append(f"{page_id}: {error[:200]}")
        if conn is not None:
            snapshot.record_failure(conn, page_id, self.now, error, gone=gone)

    def _check(self, fetcher: PageFetcher, conn, entry: dict, kind: str) -> tuple[Item | None, str]:
        html, status, method = fetcher.fetch(entry["url"], entry.get("render", "auto"))
        lines = page_lines(html, entry.get("selector"))
        chars = sum(len(line) for line in lines)
        if kind in CARD_BUILDERS and chars < MIN_TEXT_CHARS:
            raise RuntimeError(f"only {chars} characters of text (needs a browser, or the page moved?)")

        signal = page_signal(lines, self.today) if kind in CARD_BUILDERS else (None, "")
        note = f"{chars} chars via {method}"
        if signal[0]:
            note += f", says applications are {signal[0]}" + (f' ("{signal[1][:120]}")' if conn is None else "")
        if conn is not None:
            diff = snapshot.record_check(conn, entry["id"], lines, self.now, status, method,
                                         entry.get("only_lines_matching"), entry.get("ignore_lines"),
                                         signal=signal, years=years_seen(lines, self.today))
            note += ", first snapshot" if diff is None else (f", CHANGED ({len(diff)} lines)" if diff else ", unchanged")
        if kind not in CARD_BUILDERS:
            return None, note

        # Deadlines on the page that the YAML doesn't already list.
        known_dates = {d.date for d in parse_deadlines(entry.get("deadlines"))}
        found = find_deadlines(lines, self.today)
        if self.claude is not None and worth_asking_claude(lines, found):
            from_claude = self.claude.extract_deadlines(_name(entry), entry["url"], deadline_excerpt(lines), self.today)
            if from_claude is not None:
                found += from_claude
                note += f", asked Claude ({len(from_claude)} dates)"
        new = [d for d in found if d.date not in known_dates]
        if new and conn is None:      # Verify report: show the sentence each date came from
            note += ", found " + "; ".join(f"{d.kind} {d.date}{' (year assumed)' if d.approx else ''} from \"{d.evidence[:140]}\"" for d in new)
        elif new:
            note += ", found " + ", ".join(f"{d.kind} {d.date}" for d in new)

        # The registry's own Item for this entry (same name / org / place, so the
        # two merge into one card; the registry keeps the display details),
        # carrying the newly found deadlines.
        item = CARD_BUILDERS[kind]([entry], self.today)[0]
        item.status, item.status_reason = registry_status(entry, parse_deadlines(entry.get("deadlines")) + new, self.today)
        item.deadlines = new
        item.scope = entry["id"]       # a page that fails to load keeps its deadlines
        return item, note


def _name(entry: dict) -> str:
    """Early programs have firm + program instead of a name."""
    if entry.get("name"):
        return entry["name"]
    return f"{entry.get('firm', '')}: {entry.get('program', entry['id'])}".strip(": ")
