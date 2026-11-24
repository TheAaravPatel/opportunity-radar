"""Sources that read our own hand-curated YAML registries.

  early_programs_registry  -> config/early_programs.yaml (Section 1B)
  watched_pages_registry   -> config/watched_pages.yaml  (Section 2, Stanford)

They turn each entry into an Item with an explicit status, so the dashboard and
digest show these programs and their known deadlines immediately - even before
the page-watcher (which checks the pages themselves) exists.

Status rules (shared by both registries):
  possibly discontinued  the entry's status_hint says so
  opening soon           a known "opens" date is still ahead
  open                   an application deadline is still ahead, or there are
                         no dates and no typical month (rolling / ongoing)
  expected soon          nothing posted, but today is inside the usual opening
                         window: from 2 weeks before `typical_open_month` to its end
  closed                 every known deadline has passed (or between cycles)
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from radar.models import Deadline, FetchResult, Item
from radar.sources.base import Source, register
from radar.timeutil import to_date_str, today_local

EARLY_WINDOW_DAYS = 14   # "expected soon" starts this many days before the typical month
STALE_REASON = "Page looks out of date - check it before relying on it"
REGISTRY_SOURCE_TYPES = ("early_programs_registry", "watched_pages_registry")


def expected_window(month: int, today: date) -> tuple[date, date]:
    """The next opening window for a program that usually opens in `month`.

    The window runs from 14 days before the 1st of that month to the month's end.
    If this year's window is already over, use next year's.
    """
    for year in (today.year, today.year + 1):
        start = date(year, month, 1) - timedelta(days=EARLY_WINDOW_DAYS)
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end >= today:
            return start, end
    raise AssertionError("unreachable")


def _fmt(day: str) -> str:
    """'2027-01-31' -> 'Jan 31, 2027'."""
    parsed = date.fromisoformat(day)
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def parse_deadlines(entries: list | None) -> list[Deadline]:
    deadlines = []
    for entry in entries or []:
        day = to_date_str(entry.get("date"))
        if not day:
            continue
        deadlines.append(
            Deadline(
                kind=entry.get("kind", "application"),
                date=day,
                label=entry.get("label", "") or "",
                approx=bool(entry.get("approx", False)),
            )
        )
    return deadlines


def registry_status(entry: dict, deadlines: list[Deadline], today: date) -> tuple[str, str]:
    """Return (status, human-readable reason)."""
    hint = (entry.get("status_hint") or "").lower()
    if hint in ("possibly discontinued", "discontinued"):
        word = "Discontinued" if hint == "discontinued" else "Possibly discontinued"
        return "possibly discontinued", f"{word}: {entry.get('notes', '').strip()}".strip().rstrip(":")

    iso_today = today.isoformat()
    opens_ahead = sorted(d.date for d in deadlines if d.kind == "opens" and d.date > iso_today)
    apps = sorted(d.date for d in deadlines if d.kind in ("application", "priority"))
    apps_ahead = [d for d in apps if d >= iso_today]

    if opens_ahead and (not apps_ahead or opens_ahead[0] <= apps_ahead[0]):
        return "opening soon", f"Applications open {_fmt(opens_ahead[0])}"
    if apps_ahead:
        return "open", f"Next deadline {_fmt(apps_ahead[0])}"

    month = entry.get("typical_open_month")
    if month:
        month_name = calendar.month_name[int(month)]
        start, end = expected_window(int(month), today)
        if start <= today <= end:
            return "expected soon", f"Usually opens in {month_name} - not posted yet"
        if apps:
            return "closed", f"Last deadline {_fmt(apps[-1])} passed; usually opens in {month_name}"
        return "closed", f"Between cycles - usually opens in {month_name}"
    if apps:
        return "closed", f"Deadline {_fmt(apps[-1])} passed"
    return "open", "No fixed deadline listed (rolling or ongoing)"


def _window_extra(entry: dict, today: date) -> dict:
    month = entry.get("typical_open_month")
    if not month:
        return {}
    start, end = expected_window(int(month), today)
    return {"typical_open_month": int(month), "expected_open_start": start.isoformat(), "expected_open_end": end.isoformat()}


def _travel_extra(entry: dict) -> dict:
    """Hand-checked `travel_covered: full | partial | none` (+ optional travel_note)."""
    out = {}
    if entry.get("travel_covered"):
        out["travel_covered"] = str(entry["travel_covered"]).lower()
    if entry.get("travel_note"):
        out["travel_note"] = entry["travel_note"]
    return out


def _watch_extra(entry: dict) -> dict:
    """`watch: false` entries are skipped by the page-watcher (bot-blocked or login-only)."""
    if entry.get("watch", True):
        return {}
    return {"watch": False, "watch_note": entry.get("watch_note", "")}


def refresh_registry_statuses(conn, today: date) -> int:
    """Recompute registry rows' status from ALL their active deadlines.

    The registry sources only know the dates written in the YAML; the
    page-watcher may have found more on the program's page. Same rules as
    registry_status(). Returns how many rows changed.
    """
    from radar.store import UNLISTED_REASON   # (imported here to avoid an import loop)

    rows = conn.execute(
        f"""SELECT o.id, o.status, o.status_reason, o.extra FROM opportunities o
            JOIN sources s ON s.id = o.primary_source
            WHERE s.type IN ({", ".join("?" * len(REGISTRY_SOURCE_TYPES))})
              AND (o.status_reason IS NULL OR o.status_reason != ?)""",
        (*REGISTRY_SOURCE_TYPES, UNLISTED_REASON),
    ).fetchall()
    changed = 0
    for row in rows:
        extra = json.loads(row["extra"] or "{}")
        deadlines = [Deadline(kind=d["kind"], date=d["date"]) for d in conn.execute(
            "SELECT kind, date FROM deadlines WHERE opportunity_id = ? AND active = 1", (row["id"],))]
        entry = {"status_hint": extra.get("status_hint", ""), "typical_open_month": extra.get("typical_open_month"),
                 "notes": extra.get("notes", "")}
        status, reason = registry_status(entry, deadlines, today)
        if extra.get("stale") and status == "open":
            reason = STALE_REASON
        if (status, reason) != (row["status"], row["status_reason"]):
            closed_at = "closed_at" if status == "closed" else "NULL"
            conn.execute(f"UPDATE opportunities SET status = ?, status_reason = ?, closed_at = {closed_at} WHERE id = ?",
                         (status, reason, row["id"]))
            changed += 1
    return changed


@register("early_programs_registry")
class EarlyProgramsRegistry(Source):
    def fetch(self) -> FetchResult:
        data = self.cfg.load_file(self.options.get("file", "early_programs.yaml"))
        return FetchResult(items=early_program_items(data.get("programs", []) or [], today_local()))


def early_program_items(programs: list[dict], today: date) -> list[Item]:
    items = []
    for entry in programs:
        deadlines = parse_deadlines(entry.get("deadlines"))
        status, reason = registry_status(entry, deadlines, today)
        location = entry.get("location") or ""
        items.append(
            Item(
                source_item_id=entry["id"],
                registry_id=entry["id"],
                title=entry["program"],
                organization=entry["firm"],
                url=entry.get("url", ""),
                type=entry.get("type", "early-career program"),
                fields=list(entry.get("fields") or []),
                subcategory=entry.get("format", ""),
                locations=[location] if location else [],
                remote="virtual" in location.lower(),
                class_years=list(entry.get("class_years") or []),
                text=" ".join([entry.get("notes", "") or "", " ".join(entry.get("aka") or [])]),
                deadlines=deadlines,
                status=status,
                status_reason=reason,
                extra={
                    "format": entry.get("format", ""),
                    "notes": (entry.get("notes") or "").strip(),
                    "aka": entry.get("aka") or [],
                    "status_hint": entry.get("status_hint", ""),
                    "last_verified": str(entry.get("last_verified", "")),
                    **_watch_extra(entry),
                    **_travel_extra(entry),
                    **_window_extra(entry, today),
                },
            )
        )
    return items


@register("watched_pages_registry")
class WatchedPagesRegistry(Source):
    def fetch(self) -> FetchResult:
        data = self.cfg.load_file(self.options.get("file", "watched_pages.yaml"))
        return FetchResult(items=watched_page_items(data.get("pages", []) or [], today_local()))


def watched_page_items(pages: list[dict], today: date) -> list[Item]:
    items = []
    for entry in pages:
        deadlines = parse_deadlines(entry.get("deadlines"))
        status, reason = registry_status(entry, deadlines, today)
        stale = (entry.get("status_hint") or "").lower() == "stale"
        if stale and status == "open":
            reason = STALE_REASON
        items.append(
            Item(
                source_item_id=entry["id"],
                registry_id=entry["id"],
                title=entry["name"],
                organization=entry.get("org") or "Stanford",
                url=entry["url"],
                type=entry.get("type", "research program"),
                fields=list(entry.get("fields") or []),
                locations=["Stanford, CA"],
                class_years=list(entry.get("class_years") or []),
                text=entry.get("notes", "") or "",
                deadlines=deadlines,
                status=status,
                status_reason=reason,
                extra={
                    "notes": (entry.get("notes") or "").strip(),
                    "status_hint": entry.get("status_hint", ""),
                    "stale": stale,
                    "render": entry.get("render", "auto"),
                    "stanford": True,
                    **_watch_extra(entry),
                    **_travel_extra(entry),
                    **_window_extra(entry, today),
                },
            )
        )
    return items


# ------------------------------------------------------------- conferences
@register("conferences_registry")
class ConferencesRegistry(Source):
    """config/conferences.yaml - hand-curated conferences, hackathons, travel scholarships."""

    def fetch(self) -> FetchResult:
        data = self.cfg.load_file(self.options.get("file", "conferences.yaml"))
        return FetchResult(items=conference_registry_items(data.get("conferences", []) or [], today_local()))


def conference_registry_items(entries: list[dict], today: date) -> list[Item]:
    from radar.sources.conferences_common import conference_item, key_name   # avoid an import cycle

    items = []
    for entry in entries:
        deadlines = parse_deadlines(entry.get("deadlines"))
        hint = (entry.get("status_hint") or "").lower()
        if hint == "cancelled":
            status, reason = "possibly discontinued", f"Cancelled: {entry.get('notes', '')}".strip()
        else:
            status, reason = registry_status(entry, deadlines, today)
        start, end = to_date_str(entry.get("start")), to_date_str(entry.get("end"))
        if not start and entry.get("typical_month") and status == "open":
            reason = f"Dates not announced yet - usually held in {calendar.month_name[int(entry['typical_month'])]}"
        item = conference_item(
            source_item_id=entry["id"],
            name=entry["name"],
            url=entry["url"],
            type_=entry.get("type", "conference"),
            fields=list(entry.get("fields") or []),
            start=start,
            end=end,
            location=entry.get("location", "") or "",
            virtual=bool(entry.get("virtual", False)),
            cost=entry.get("cost", "unknown"),
            cost_note=entry.get("cost_note", "") or "",
            scholarship=entry.get("travel_scholarship"),
            scholarship_covers=entry.get("scholarship_covers", "") or "",
            undergrad_eligible=entry.get("undergrad_eligible"),
            poster_track=entry.get("poster_track"),
            deadlines=deadlines,
            notes=(entry.get("notes") or "").strip(),
            organizer=entry.get("organizer", "") or "",
            status=status,
            status_reason=reason,
            registry_id=entry["id"],
            class_years=list(entry.get("class_years") or []),
            today=today,
            dedupe_name=entry.get("key_name", "") or "",
        )
        item.extra.update(_watch_extra(entry))
        item.extra.update(_travel_extra(entry))
        item.extra["last_verified"] = str(entry.get("last_verified", ""))
        # Other names feeds use for this event (see conference_aliases in db.py).
        item.extra["alias_keys"] = [f"conf|{key_name(a)}" for a in entry.get("aka") or []]
        items.append(item)
    return items
