"""Writing items into the database: dedupe, sightings, deadlines, closing, health.

How an incoming item finds its database row (first match wins):
  1. this source has seen this exact item before (same source_item_id)
  2. exact dedupe key: normalized org | title | first city
  3. fuzzy: same normalized org | title AND the cities overlap (or one side has
     no city) AND that row isn't already another item *from the same source*
     (one source listing two items means they really are different)
Otherwise a new row is created.

Display fields (title, url, location...) belong to the row's `primary_source`
- the first source that created it - so rows don't flip-flop between feeds.
Other sources only add fields/terms and keep the row alive.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from datetime import timedelta

from radar.eligibility import Eligibility
from radar.models import Item
from radar.timeutil import parse_datetime, to_iso

# If a successful fetch returns less than this share of last run's items,
# something is probably wrong upstream: don't close anything this time.
SUSPICIOUS_DROP = 0.5
SUSPICIOUS_MIN_PREVIOUS = 20


@dataclass
class Prepared:
    """An Item plus everything the pipeline computed about it."""

    item: Item
    fields: list[str]
    field_method: str
    eligibility: Eligibility
    early_career: bool
    dedupe_key: str
    match_key: str
    cities: list[str]
    location: str
    bay_area: bool
    chicagoland: bool = False
    travel: str = ""
    travel_reason: str = ""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ----------------------------------------------------------------- sources
def upsert_source(conn: sqlite3.Connection, source_id: str, type_name: str, name: str, homepage: str, group: str) -> None:
    conn.execute(
        """INSERT INTO sources (id, type, name, homepage, grp) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET type=excluded.type, name=excluded.name,
               homepage=excluded.homepage, grp=excluded.grp""",
        (source_id, type_name, name, homepage, group),
    )


def start_run(conn: sqlite3.Connection, source_id: str, now: str) -> int:
    cur = conn.execute("INSERT INTO runs (source_id, started_at) VALUES (?, ?)", (source_id, now))
    conn.execute("UPDATE sources SET last_attempt_at = ? WHERE id = ?", (now, source_id))
    return cur.lastrowid


def finish_run(conn, run_id: int, now: str, ok: bool, seen=None, new=None, closed=None, error=None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=?, items_seen=?, items_new=?, items_closed=?, error=? WHERE id=?",
        (now, int(ok), seen, new, closed, error, run_id),
    )


def record_success(conn, source_id: str, now: str, seen: int, upstream_updated_at: str | None, warnings: list[str]) -> None:
    conn.execute(
        """UPDATE sources SET last_success_at=?, last_error=NULL, consecutive_failures=0,
               items_last_run=?, upstream_updated_at=COALESCE(?, upstream_updated_at), warnings=?
           WHERE id=?""",
        (now, seen, upstream_updated_at, _json(warnings), source_id),
    )


def record_failure(conn, source_id: str, error: str) -> None:
    conn.execute(
        "UPDATE sources SET last_error=?, consecutive_failures=consecutive_failures+1 WHERE id=?",
        (error[:500], source_id),
    )


# ------------------------------------------------------------------- items
def _find_existing(conn, source_id: str, p: Prepared) -> sqlite3.Row | None:
    row = conn.execute(
        """SELECT o.* FROM sightings s JOIN opportunities o ON o.id = s.opportunity_id
           WHERE s.source_id = ? AND s.source_item_id = ?""",
        (source_id, p.item.source_item_id),
    ).fetchone()
    if row:
        return row
    row = conn.execute("SELECT * FROM opportunities WHERE dedupe_key = ?", (p.dedupe_key,)).fetchone()
    if row:
        return row
    if p.dedupe_key.startswith("conf|"):
        row = _find_conference_tba(conn, source_id, p.dedupe_key) or _find_conference_alias(conn, p.dedupe_key)
        if row:
            return row
    for candidate in conn.execute("SELECT * FROM opportunities WHERE match_key = ?", (p.match_key,)):
        same_source = conn.execute(
            "SELECT 1 FROM sightings WHERE opportunity_id = ? AND source_id = ?", (candidate["id"], source_id)
        ).fetchone()
        if same_source:
            continue
        their_cities = set(json.loads(candidate["cities"] or "[]"))
        if not their_cities or not p.cities or their_cities & set(p.cities):
            return candidate
    return None


def _find_conference_tba(conn, source_id: str, key: str) -> sqlite3.Row | None:
    """Conference keys are "conf|<name>|<year>", and year is "tba" when a feed
    doesn't know the dates yet. Let "TreeHacks (tba)" and "TreeHacks 2027" match,
    in either direction - but never two rows from the same source."""
    name, year = key.rsplit("|", 1)
    if year == "tba":
        query = "SELECT * FROM opportunities WHERE dedupe_key LIKE ? ESCAPE '\\' ORDER BY dedupe_key DESC"
        args = (name.replace("%", "\\%").replace("_", "\\_") + "|%",)
    else:
        query, args = "SELECT * FROM opportunities WHERE dedupe_key = ?", (f"{name}|tba",)
    for candidate in conn.execute(query, args):
        same_source = conn.execute(
            "SELECT 1 FROM sightings WHERE opportunity_id = ? AND source_id = ?", (candidate["id"], source_id)
        ).fetchone()
        if not same_source:
            return candidate
    return None


def _find_conference_alias(conn, key: str) -> sqlite3.Row | None:
    name = key.rsplit("|", 1)[0]
    return conn.execute(
        "SELECT o.* FROM conference_aliases a JOIN opportunities o ON o.id = a.opportunity_id WHERE a.alias_key = ?",
        (name,),
    ).fetchone()


def _row_values(p: Prepared, now: str) -> dict:
    item = p.item
    return {
        "title": item.title,
        "organization": item.organization,
        "fields": ",".join(p.fields),
        "field_method": p.field_method,
        "type": item.type,
        "subcategory": item.subcategory,
        "location": p.location,
        "cities": _json(p.cities),
        "remote": int(item.remote),
        "bay_area": int(p.bay_area),
        "chicagoland": int(p.chicagoland),
        "travel": p.travel or None,
        "travel_reason": p.travel_reason or None,
        "url": item.url,
        "posted_at": item.posted_at,
        "status": item.status or "open",
        "status_reason": item.status_reason or None,
        "terms": _json(item.terms),
        "degrees": _json(item.degrees),
        "class_years": _json(p.eligibility.class_years),
        "eligibility_score": p.eligibility.score,
        "eligibility_reasons": _json(p.eligibility.reasons),
        "early_career": int(p.early_career),
        "registry_id": item.registry_id or None,
        "extra": _json(item.extra),
        "last_seen_at": now,
    }


def save_items(conn, source_id: str, prepared: list[Prepared], run_id: int, now: str, deadline_method: str) -> tuple[int, int]:
    """Upsert every prepared item. Returns (items seen, new opportunities)."""
    new = 0
    written_this_run: set[int] = set()   # rows this source already wrote during this run
    for p in prepared:
        existing = _find_existing(conn, source_id, p)
        values = _row_values(p, now)
        if existing is None:
            values.update(
                dedupe_key=p.dedupe_key, match_key=p.match_key, first_seen_at=now,
                primary_source=source_id, first_run_id=run_id,
            )
            cols = ", ".join(values)
            marks = ", ".join(f":{k}" for k in values)
            opp_id = conn.execute(f"INSERT INTO opportunities ({cols}) VALUES ({marks})", values).lastrowid
            new += 1
        else:
            opp_id = existing["id"]
            # A source can list the same job twice (e.g. a Summer and a Fall
            # posting with identical title/location). The second copy merges
            # into the first instead of overwriting it.
            if existing["primary_source"] in (None, source_id) and opp_id not in written_this_run:
                _overwrite(conn, existing, p, values)
            else:
                _merge(conn, existing, p, now)
            if existing["status"] == "closed" and (p.item.status or "open") != "closed":
                conn.execute("UPDATE opportunities SET closed_at = NULL WHERE id = ?", (opp_id,))
        written_this_run.add(opp_id)
        owns_row = existing is None or existing["primary_source"] in (None, source_id)
        if owns_row and p.item.extra.get("conference"):
            _upsert_conference(conn, opp_id, p.item.extra["conference"])
        for alias in p.item.extra.get("alias_keys") or []:
            conn.execute("INSERT OR REPLACE INTO conference_aliases (alias_key, opportunity_id) VALUES (?, ?)", (alias, opp_id))
        _upsert_sighting(conn, opp_id, source_id, p.item, run_id, now)
        for d in p.item.deadlines:
            _upsert_deadline(conn, opp_id, source_id, d, deadline_method, run_id, now)
    return len(prepared), new


def _overwrite(conn, existing: sqlite3.Row, p: Prepared, values: dict) -> None:
    # Only move the dedupe keys if no other row already owns them.
    if p.dedupe_key != existing["dedupe_key"]:
        clash = conn.execute("SELECT 1 FROM opportunities WHERE dedupe_key = ? AND id != ?", (p.dedupe_key, existing["id"])).fetchone()
        if not clash:
            values = {**values, "dedupe_key": p.dedupe_key, "match_key": p.match_key}
    if values["status"] != "closed":
        values = {**values, "closed_at": None}
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    conn.execute(f"UPDATE opportunities SET {assignments} WHERE id = :id", {**values, "id": existing["id"]})


def _merge(conn, existing: sqlite3.Row, p: Prepared, now: str) -> None:
    """A secondary source saw this row: add what it knows, keep it alive."""
    fields = sorted(set(filter(None, (existing["fields"] or "").split(","))) | set(p.fields))
    terms = sorted(set(json.loads(existing["terms"] or "[]")) | set(p.item.terms))
    posted = min(filter(None, [existing["posted_at"], p.item.posted_at]), default=None)
    # Re-open a closed row only if this source says the item is NOT closed
    # (a past conference listed twice must stay closed).
    incoming = p.item.status or "open"
    status = existing["status"] if existing["status"] != "closed" or incoming == "closed" else incoming
    conn.execute(
        """UPDATE opportunities SET fields=?, terms=?, posted_at=?, last_seen_at=?, status=?,
               early_career=MAX(early_career, ?) WHERE id=?""",
        (",".join(fields), json.dumps(terms), posted, now, status, int(p.early_career), existing["id"]),
    )


def _upsert_conference(conn, opp_id: int, c: dict) -> None:
    """Conference-specific fields live in their own table (1 row per opportunity)."""
    def flag(value):
        return None if value is None else int(bool(value))

    conn.execute(
        """INSERT OR REPLACE INTO conference_details
               (opportunity_id, year, start_date, end_date, location, virtual, bay_area, cost_tier,
                cost_note, scholarship, scholarship_covers, undergrad_eligible, poster_track)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (opp_id, c.get("year"), c.get("start_date"), c.get("end_date"), c.get("location"),
         flag(c.get("virtual")), flag(c.get("bay_area")), c.get("cost_tier"), c.get("cost_note"),
         flag(c.get("scholarship")), c.get("scholarship_covers"), flag(c.get("undergrad_eligible")),
         flag(c.get("poster_track"))),
    )


def _upsert_sighting(conn, opp_id: int, source_id: str, item: Item, run_id: int, now: str) -> None:
    conn.execute(
        """INSERT INTO sightings (opportunity_id, source_id, source_item_id, scope, url,
                                  first_seen_at, last_seen_at, last_run_id, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(source_id, source_item_id) DO UPDATE SET
               opportunity_id=excluded.opportunity_id, scope=excluded.scope, url=excluded.url,
               last_seen_at=excluded.last_seen_at, last_run_id=excluded.last_run_id, active=1""",
        (opp_id, source_id, item.source_item_id, item.scope, item.url, now, now, run_id),
    )


def _upsert_deadline(conn, opp_id: int, source_id: str, d, method: str, run_id: int, now: str) -> None:
    conn.execute(
        """INSERT INTO deadlines (opportunity_id, source_id, kind, date, label, approx, method,
                                  first_seen_at, last_seen_at, last_run_id, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(opportunity_id, kind, date, label) DO UPDATE SET
               approx=excluded.approx, last_seen_at=excluded.last_seen_at,
               last_run_id=excluded.last_run_id, active=1""",
        (opp_id, source_id, d.kind, d.date, d.label or "", int(d.approx), method, now, now, run_id),
    )


UNLISTED_REASON = "No longer listed by its source"


def _close_unlisted(conn, opp_ids: set[int], now: str) -> int:
    """Close each opportunity that no source lists any more."""
    closed = 0
    for opp_id in opp_ids:
        still_listed = conn.execute("SELECT 1 FROM sightings WHERE opportunity_id = ? AND active = 1", (opp_id,)).fetchone()
        if not still_listed:
            cur = conn.execute(
                "UPDATE opportunities SET status='closed', closed_at=?, status_reason=? WHERE id=? AND status != 'closed'",
                (now, UNLISTED_REASON, opp_id),
            )
            closed += cur.rowcount
    return closed


def _close_window_feed(conn, source_id: str, run_id: int, now: str, keep_days: float) -> int:
    """Closing rule for feeds that only show their newest N items."""
    cutoff = to_iso(parse_datetime(now) - timedelta(days=keep_days))
    # Self-heal: re-activate recently seen sightings that an earlier run closed
    # too eagerly, and re-open their opportunities.
    revived = conn.execute(
        "SELECT id, opportunity_id FROM sightings WHERE source_id = ? AND active = 0 AND last_seen_at >= ?",
        (source_id, cutoff),
    ).fetchall()
    conn.executemany("UPDATE sightings SET active = 1 WHERE id = ?", [(r["id"],) for r in revived])
    for r in revived:
        conn.execute(
            "UPDATE opportunities SET status='open', closed_at=NULL, status_reason=NULL WHERE id=? AND status='closed' AND status_reason=?",
            (r["opportunity_id"], UNLISTED_REASON),
        )
    # Only items missing for longer than keep_days count as gone.
    stale = conn.execute(
        """SELECT id, opportunity_id FROM sightings WHERE source_id = ? AND active = 1
           AND (last_run_id IS NULL OR last_run_id != ?) AND last_seen_at < ?""",
        (source_id, run_id, cutoff),
    ).fetchall()
    conn.executemany("UPDATE sightings SET active = 0 WHERE id = ?", [(r["id"],) for r in stale])
    return _close_unlisted(conn, {r["opportunity_id"] for r in stale}, now)


# ---------------------------------------------------------------- baseline
def first_ok_runs(conn) -> dict[str, int]:
    """Each source's first successful run id."""
    return {
        r["source_id"]: r["first_id"]
        for r in conn.execute("SELECT source_id, MIN(id) AS first_id FROM runs WHERE ok = 1 GROUP BY source_id")
    }


def is_baseline(row, first_runs: dict[str, int]) -> bool:
    """True for rows created by their source's very first import.

    Those are the starting point, not news: the first Simplify run imports
    ~4,000 internships, and none of them should show up as "new today".
    """
    first = first_runs.get(row["primary_source"] or "")
    return bool(first and row["first_run_id"] and row["first_run_id"] <= first)


# ----------------------------------------------------------------- closing
def close_missing(conn, source_id: str, run_id: int, seen: int, failed_scopes: set[str], now: str,
                  keep_missing_days: float | None = None) -> tuple[int, str | None]:
    """Close items this source no longer lists. Returns (closed count, warning).

    keep_missing_days: for "newest N jobs" feeds (Jobs Every Hour), an item
    dropping out of the window doesn't mean the job closed. Such items stay
    open until they've been missing for this many days.
    """
    if keep_missing_days:
        return _close_window_feed(conn, source_id, run_id, now, keep_missing_days), None
    previous = conn.execute("SELECT items_last_run FROM sources WHERE id = ?", (source_id,)).fetchone()
    previous = previous["items_last_run"] if previous else None
    if previous and previous >= SUSPICIOUS_MIN_PREVIOUS and seen < previous * SUSPICIOUS_DROP:
        return 0, (
            f"returned {seen} items vs {previous} last run - looks broken upstream, so nothing was closed"
        )

    scopes = sorted(failed_scopes)
    scope_filter = f"AND scope NOT IN ({', '.join('?' * len(scopes))})" if scopes else ""
    stale = conn.execute(
        f"""SELECT id, opportunity_id FROM sightings
            WHERE source_id = ? AND active = 1 AND (last_run_id IS NULL OR last_run_id != ?) {scope_filter}""",
        (source_id, run_id, *scopes),
    ).fetchall()
    conn.executemany("UPDATE sightings SET active = 0 WHERE id = ?", [(r["id"],) for r in stale])
    closed = _close_unlisted(conn, {r["opportunity_id"] for r in stale}, now)

    # Deadlines this source no longer reports (e.g. removed from a registry YAML).
    conn.execute(
        f"""UPDATE deadlines SET active = 0
            WHERE source_id = ? AND active = 1 AND (last_run_id IS NULL OR last_run_id != ?)
              AND opportunity_id IN (SELECT opportunity_id FROM sightings WHERE source_id = ? {scope_filter})""",
        (source_id, run_id, source_id, *scopes),
    )
    return closed, None
