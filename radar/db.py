"""The SQLite database: schema and connection helpers.

The database file is data/radar.db. On GitHub Actions it is downloaded from the
`data` branch at the start of each run and pushed back at the end (see
scripts/save-db.sh), so history (first-seen dates, closed items, page
snapshots) survives between runs.

Table overview
  sources            one row per source: health info for the dashboard panel
  runs               one row per source per run (a log)
  opportunities      the normalized items everything else shows
  sightings          which source(s) listed each opportunity, and whether they still do
  deadlines          deadlines attached to opportunities (many per opportunity)
  reminders_sent     which 14/7/2-day reminders the digest already sent
  pages, page_snapshots   the page-watcher's state (Phase 4)
  early_programs     registry status history (Phase 5)
  conference_details extra conference fields (Phase 6)
  suggestions        new sources found by discovery, waiting for approval (Phase 7)
  llm_cache          Claude answers, so we never pay twice for the same question
  meta               small key/value store (schema version, last digest time...)

Lists (fields, terms, reasons...) are stored as JSON text; `fields` is a plain
comma-separated string ("cs,econ") so SQL can filter it with LIKE.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id                   TEXT PRIMARY KEY,   -- key from sources.yaml, e.g. "simplify"
    type                 TEXT NOT NULL,      -- module that handles it, e.g. "listings_json"
    name                 TEXT,
    homepage             TEXT,
    grp                  TEXT,               -- schedule group: jobs / daily
    last_attempt_at      TEXT,
    last_success_at      TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    items_last_run       INTEGER,
    upstream_updated_at  TEXT,               -- when the upstream data last changed
    warnings             TEXT                -- JSON list
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    source_id    TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    ok           INTEGER NOT NULL DEFAULT 0,
    items_seen   INTEGER,
    items_new    INTEGER,
    items_closed INTEGER,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    id                  INTEGER PRIMARY KEY,
    dedupe_key          TEXT NOT NULL UNIQUE, -- normalized org | title | city
    match_key           TEXT NOT NULL,        -- normalized org | title (fuzzy dedupe)
    title               TEXT NOT NULL,
    organization        TEXT NOT NULL,
    fields              TEXT NOT NULL DEFAULT '',  -- "cs,econ"
    field_method        TEXT,                 -- source / keyword / llm / unplaced
    type                TEXT NOT NULL,
    subcategory         TEXT,
    location            TEXT,                 -- display string
    cities              TEXT,                 -- JSON list of normalized cities
    remote              INTEGER NOT NULL DEFAULT 0,
    bay_area            INTEGER NOT NULL DEFAULT 0,
    url                 TEXT,
    posted_at           TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    closed_at           TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    status_reason       TEXT,
    terms               TEXT,                 -- JSON list
    degrees             TEXT,                 -- JSON list
    class_years         TEXT,                 -- JSON list ([] = no restriction found)
    eligibility_score   INTEGER,
    eligibility_reasons TEXT,                 -- JSON list of {points, reason}
    early_career        INTEGER NOT NULL DEFAULT 0,
    registry_id         TEXT,                 -- early-program / page id this belongs to
    primary_source      TEXT,                 -- source allowed to overwrite display fields
    first_run_id        INTEGER,              -- run that first created this row
    extra               TEXT                  -- JSON object
);
CREATE INDEX IF NOT EXISTS idx_opp_match ON opportunities(match_key);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_opp_first_seen ON opportunities(first_seen_at);

CREATE TABLE IF NOT EXISTS sightings (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_id      TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    scope          TEXT NOT NULL DEFAULT '',
    url            TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_run_id    INTEGER,                   -- run that last saw it (used to close missing items)
    active         INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source_id, source_item_id)
);
CREATE INDEX IF NOT EXISTS idx_sightings_opp ON sightings(opportunity_id);

CREATE TABLE IF NOT EXISTS deadlines (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_id      TEXT,
    kind           TEXT NOT NULL,
    date           TEXT NOT NULL,             -- YYYY-MM-DD
    label          TEXT NOT NULL DEFAULT '',
    approx         INTEGER NOT NULL DEFAULT 0,
    method         TEXT NOT NULL,             -- structured / manual / regex / llm
    evidence       TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_run_id    INTEGER,
    active         INTEGER NOT NULL DEFAULT 1,
    UNIQUE (opportunity_id, kind, date, label)
);
CREATE INDEX IF NOT EXISTS idx_deadlines_date ON deadlines(date);

CREATE TABLE IF NOT EXISTS reminders_sent (
    deadline_id INTEGER NOT NULL,
    days_before INTEGER NOT NULL,
    sent_at     TEXT NOT NULL,
    PRIMARY KEY (deadline_id, days_before)
);

CREATE TABLE IF NOT EXISTS pages (
    id                   TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL,       -- stanford / early_program / conference / job_page
    url                  TEXT NOT NULL,
    opportunity_id       INTEGER,
    render               TEXT,
    last_checked_at      TEXT,
    last_changed_at      TEXT,
    last_status          INTEGER,
    content_hash         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT
);

CREATE TABLE IF NOT EXISTS page_snapshots (
    id           INTEGER PRIMARY KEY,
    page_id      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text_z       BLOB,                        -- zlib-compressed page text
    diff         TEXT,                        -- readable diff vs. previous snapshot
    changed      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_snapshots_page ON page_snapshots(page_id, fetched_at);

CREATE TABLE IF NOT EXISTS early_programs (
    id                    TEXT PRIMARY KEY,
    opportunity_id        INTEGER,
    status                TEXT,
    status_reason         TEXT,
    expected_open_start   TEXT,
    expected_open_end     TEXT,
    last_status_change_at TEXT,
    matched_opportunity_ids TEXT
);

CREATE TABLE IF NOT EXISTS conference_details (
    opportunity_id     INTEGER PRIMARY KEY REFERENCES opportunities(id) ON DELETE CASCADE,
    year               INTEGER,
    start_date         TEXT,
    end_date           TEXT,
    location           TEXT,
    virtual            INTEGER,
    bay_area           INTEGER,
    cost_tier          TEXT,                  -- free / scholarship / student / full
    cost_note          TEXT,
    scholarship        INTEGER,
    scholarship_covers TEXT,
    undergrad_eligible INTEGER,
    poster_track       INTEGER
);

-- Other names a registry conference is known by ("Women in Data Science (WiDS)
-- Worldwide" = "WiDS Worldwide Conference"), so feeds using them merge into it.
CREATE TABLE IF NOT EXISTS conference_aliases (
    alias_key      TEXT PRIMARY KEY,          -- "conf|<name>" (no year)
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS suggestions (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,              -- stanford_page / early_program / conference / company
    name          TEXT NOT NULL,
    url           TEXT,
    why           TEXT,
    target_config TEXT,
    payload       TEXT,                       -- JSON: the config entry to add if approved
    source        TEXT,                       -- discovery / keyword-detection
    found_at      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (kind, url)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    purpose    TEXT,
    model      TEXT,
    response   TEXT,
    created_at TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and if needed create) the database, making sure the schema exists."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row          # rows behave like dicts: row["title"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def compact(conn: sqlite3.Connection) -> None:
    """Shrink the file before committing it to git."""
    conn.commit()
    conn.execute("VACUUM")
