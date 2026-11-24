"""Runs sources: fetch -> prepare -> save -> close missing -> record health.

A source that fails (network error, bad data, a bug) is logged and recorded in
the source-health table, and the next source still runs. Nothing a single
source does can stop the whole pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
import traceback
from dataclasses import dataclass, field

from radar import store
from radar.classify import FieldClassifier
from radar.config import Config
from radar.early_detect import EarlyCareerDetector
from radar.eligibility import EligibilityScorer
from radar.http import Http
from radar.models import Item
from radar.normalize import cities_of, dedupe_key, display_location, is_bay_area, match_key
from radar.sources import build_source
from radar.timeutil import now_iso, today_local

log = logging.getLogger(__name__)

REGISTRY_TYPES = {"early_programs_registry", "watched_pages_registry"}
EARLY_TYPES = {"early-career program", "pipeline program"}


@dataclass
class RunReport:
    source_id: str
    ok: bool
    seen: int = 0
    new: int = 0
    closed: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return f"{self.source_id}: FAILED - {self.error}"
        extra = f" (warnings: {'; '.join(self.warnings)})" if self.warnings else ""
        return f"{self.source_id}: {self.seen} items, {self.new} new, {self.closed} closed{extra}"


class Preparer:
    """Computes classification, eligibility and keys for each Item."""

    def __init__(self, cfg: Config):
        self.classifier = FieldClassifier(cfg.keywords)
        self.scorer = EligibilityScorer(cfg.keywords, cfg.grad_year)
        self.early = EarlyCareerDetector(cfg.keywords)
        self.today = today_local()

    def prepare(self, item: Item) -> store.Prepared:
        fields, method = self.classifier.classify(item)
        eligibility = self.scorer.score(item, self.today)
        # Early-career = a registry program, or a posting whose title uses
        # early-career words ("Sophomore Insight Day") - unless it's explicitly
        # only for juniors/seniors.
        title_hit = self.early.is_early(item.title)
        upper_years_only = bool(eligibility.class_years) and set(eligibility.class_years) <= {"junior", "senior"}
        early = item.type in EARLY_TYPES or (title_hit and not upper_years_only)
        return store.Prepared(
            item=item,
            fields=fields,
            field_method=method,
            eligibility=eligibility,
            early_career=early,
            dedupe_key=dedupe_key(item.organization, item.title, item.locations),
            match_key=match_key(item.organization, item.title),
            cities=cities_of(item.locations),
            location=display_location(item.locations),
            bay_area=is_bay_area(item.locations),
        )


def source_groups(options: dict) -> list[str]:
    """A source's `group` can be one name ("jobs") or a list ([jobs, daily])."""
    group = options.get("group", [])
    return [group] if isinstance(group, str) else list(group or [])


def enabled_sources(cfg: Config, group: str | None = None, only: str | None = None) -> dict[str, dict]:
    chosen = {}
    for source_id, options in cfg.sources.items():
        if only and source_id != only:
            continue
        if not only and not options.get("enabled", True):
            continue
        if group and not only and group not in source_groups(options):
            continue
        chosen[source_id] = options
    return chosen


def run_group(conn: sqlite3.Connection, cfg: Config, http: Http, group: str | None = None, only: str | None = None) -> list[RunReport]:
    preparer = Preparer(cfg)
    reports = []
    for source_id, options in enabled_sources(cfg, group, only).items():
        report = run_source(conn, cfg, http, preparer, source_id, options)
        log.info(report.summary())
        reports.append(report)
    return reports


def run_source(conn, cfg: Config, http: Http, preparer: Preparer, source_id: str, options: dict) -> RunReport:
    started = now_iso()
    try:
        source = build_source(source_id, options, cfg, http)
    except ValueError as exc:
        return RunReport(source_id, ok=False, error=str(exc))
    store.upsert_source(conn, source_id, source.type_name, source.name, source.homepage, ",".join(source_groups(options)))
    run_id = store.start_run(conn, source_id, started)
    conn.commit()

    try:
        result = source.fetch()
        prepared = [preparer.prepare(i) for i in result.items if i.title and i.organization]
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the others
        error = f"{type(exc).__name__}: {exc}"
        log.warning("source %s failed: %s", source_id, error)
        log.debug("traceback for %s:\n%s", source_id, traceback.format_exc())
        store.record_failure(conn, source_id, error)
        store.finish_run(conn, run_id, now_iso(), ok=False, error=error[:500])
        conn.commit()
        return RunReport(source_id, ok=False, error=error)

    now = now_iso()
    method = "manual" if source.type_name in REGISTRY_TYPES else "structured"
    seen, new = store.save_items(conn, source_id, prepared, run_id, now, method)
    closed, warning = store.close_missing(conn, source_id, run_id, seen, result.failed_scopes, now)
    warnings = list(result.warnings) + ([warning] if warning else [])
    if result.failed_scopes:
        warnings.append(f"{len(result.failed_scopes)} sub-feeds failed: {', '.join(sorted(result.failed_scopes))[:300]}")
    store.record_success(conn, source_id, now, seen, result.upstream_updated_at, warnings)
    store.finish_run(conn, run_id, now, ok=True, seen=seen, new=new, closed=closed)
    conn.commit()
    return RunReport(source_id, ok=True, seen=seen, new=new, closed=closed, warnings=warnings)
