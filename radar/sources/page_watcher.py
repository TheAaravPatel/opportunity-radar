"""The page-watcher source: checks every page in config/watched_pages.yaml.

  pages      Stanford program pages. Each one is already on the dashboard
             (the watched_pages_registry source); this adds the deadlines
             found on the page itself and a "page changed" alert.
  job_pages  employer job-search pages with no public job API (Analysis
             Group, Brookings...). Only "page changed" alerts, e.g. a new
             "Summer Analyst Intern" line.

This source is special: it remembers what each page said last time (the
pages / page_snapshots tables), so the pipeline gives it the database as
`self.conn`. The Verify workflow runs it without a database: it then only
fetches the pages and reports what it found.
"""

from __future__ import annotations

import logging

from radar.llm import Claude
from radar.models import FetchResult, Item
from radar.sources.base import Source, register
from radar.sources.registries import parse_deadlines, registry_status
from radar.timeutil import now_iso, today_local
from radar.watcher import snapshot
from radar.watcher.deadlines import deadline_excerpt, find_deadlines, worth_asking_claude
from radar.watcher.fetch import PageFetcher, RobotsDisallowed
from radar.watcher.text import page_lines

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 50   # a Stanford page with less text than this didn't load properly


@register("page_watcher")
class PageWatcherSource(Source):
    def fetch(self) -> FetchResult:
        registry = self.cfg.load_file(self.options.get("file", "watched_pages.yaml")) or {}
        entries = [(e, "stanford") for e in registry.get("pages") or []] + \
                  [(e, "job_page") for e in registry.get("job_pages") or []]
        conn = self.conn
        self.claude = Claude(conn, self.cfg) if conn is not None else None
        self.today, self.now = today_local(), now_iso()

        result = FetchResult(items=[])
        checked = 0
        with PageFetcher(self.http) as fetcher:
            for entry, kind in entries:
                if not entry.get("watch", True):
                    continue
                checked += 1
                if conn is not None:
                    snapshot.save_page_info(conn, entry["id"], entry, kind)
                try:
                    item, note = self._check(fetcher, conn, entry, kind)
                except RobotsDisallowed as exc:
                    self._failed(conn, result, entry["id"], f"{exc} - set watch: false")
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
        if checked and len(result.failed_scopes) == checked:
            raise RuntimeError(f"all {checked} watched pages failed: " + "; ".join(result.warnings[:3]))
        return result

    def _failed(self, conn, result: FetchResult, page_id: str, error: str) -> None:
        # A failed page keeps its earlier deadlines (failed_scopes protects them).
        result.failed_scopes.add(page_id)
        result.warnings.append(f"{page_id}: {error[:200]}")
        if conn is not None:
            snapshot.record_failure(conn, page_id, self.now, error)

    def _check(self, fetcher: PageFetcher, conn, entry: dict, kind: str) -> tuple[Item | None, str]:
        html, status, method = fetcher.fetch(entry["url"], entry.get("render", "auto"), entry.get("selector"))
        lines = page_lines(html, entry.get("selector"))
        chars = sum(len(line) for line in lines)
        if kind == "stanford" and chars < MIN_TEXT_CHARS:
            raise RuntimeError(f"only {chars} characters of text (needs a browser, or the page moved?)")

        note = f"{chars} chars via {method}"
        if conn is not None:
            diff = snapshot.record_check(conn, entry["id"], lines, self.now, status, method,
                                         entry.get("only_lines_matching"), entry.get("ignore_lines"))
            note += ", first snapshot" if diff is None else (f", CHANGED ({len(diff)} lines)" if diff else ", unchanged")
        if kind != "stanford":
            return None, note

        # Deadlines on the page that the YAML doesn't already list.
        known_dates = {d.date for d in parse_deadlines(entry.get("deadlines"))}
        found = find_deadlines(lines, self.today)
        if self.claude is not None and worth_asking_claude(lines, found):
            from_claude = self.claude.extract_deadlines(entry.get("name", entry["id"]), entry["url"],
                                                        deadline_excerpt(lines), self.today)
            if from_claude is not None:
                found += from_claude
                note += f", asked Claude ({len(from_claude)} dates)"
        new = [d for d in found if d.date not in known_dates]
        if new:
            note += ", found " + ", ".join(f"{d.kind} {d.date}" for d in new)

        # Same name / org / place as the registry's entry, so the two merge
        # into one dashboard card; the registry keeps the display details.
        all_deadlines = parse_deadlines(entry.get("deadlines")) + new
        status_now, reason = registry_status(entry, all_deadlines, self.today)
        item = Item(
            source_item_id=entry["id"],
            registry_id=entry["id"],
            title=entry["name"],
            organization=entry.get("org") or "Stanford",
            url=entry["url"],
            type=entry.get("type", "research program"),
            fields=list(entry.get("fields") or []),
            locations=["Stanford, CA"],
            class_years=list(entry.get("class_years") or []),
            deadlines=new,
            status=status_now,
            status_reason=reason,
            scope=entry["id"],      # a page that fails to load keeps its deadlines
            extra={"stanford": True},
        )
        return item, note
