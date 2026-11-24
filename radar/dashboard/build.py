"""Builds the static dashboard (published to GitHub Pages).

Output (in site/):
  index.html     one self-contained page: CSS, JavaScript and data all inline,
                 so it also works when opened straight from disk
  data.json      the same data on its own (handy for other tools)
  deadlines.ics  every tracked deadline as a calendar feed - subscribe to
                 <dashboard url>/deadlines.ics in Google Calendar

All the interactivity (tabs, filters, search, calendar) is plain JavaScript in
static/app.js. Text from sources is always inserted with textContent (never as
HTML), so a malicious job title can't inject scripts into the page.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar import store
from radar.config import Config
from radar.eligibility import class_for_grad_year
from radar.models import CLASS_YEARS, FIELD_LABELS
from radar.timeutil import now_iso, now_utc, parse_datetime, to_iso, today_local

HERE = Path(__file__).resolve().parent
STALE_UPSTREAM_DAYS = 14

# Extra keys worth sending to the browser (others stay in the DB only).
EXTRA_KEYS = (
    "notes", "format", "off_season", "stale", "stanford", "aka", "typical_open_month",
    "expected_open_start", "expected_open_end", "salary", "last_verified", "status_hint",
    "watch", "watch_note", "hidden_reason", "priority", "conference",
)


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def collect_data(conn: sqlite3.Connection, cfg: Config) -> dict:
    keep_days = int(cfg.get("dashboard.keep_closed_days", 14))
    cutoff = to_iso(now_utc() - timedelta(days=keep_days))

    rows = conn.execute(
        "SELECT * FROM opportunities WHERE status != 'closed' OR closed_at >= ? OR registry_id IS NOT NULL",
        (cutoff,),
    ).fetchall()
    ids = [r["id"] for r in rows]

    deadlines: dict[int, list] = {}
    sources_by_opp: dict[int, list] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for d in conn.execute(
            f"SELECT * FROM deadlines WHERE active = 1 AND opportunity_id IN ({marks}) ORDER BY date", ids
        ):
            deadlines.setdefault(d["opportunity_id"], []).append(
                {"id": d["id"], "kind": d["kind"], "date": d["date"], "label": d["label"], "approx": bool(d["approx"]),
                 "method": d["method"], "evidence": d["evidence"] or ""}
            )
        for s in conn.execute(
            f"SELECT opportunity_id, source_id FROM sightings WHERE active = 1 AND opportunity_id IN ({marks})", ids
        ):
            sources_by_opp.setdefault(s["opportunity_id"], []).append(s["source_id"])

    first_runs = store.first_ok_runs(conn)
    items = []
    for r in rows:
        extra = _loads(r["extra"], {})
        items.append(
            {
                "id": r["id"],
                "title": r["title"],
                "org": r["organization"],
                "fields": [f for f in (r["fields"] or "").split(",") if f],
                "field_method": r["field_method"],
                "type": r["type"],
                "subcategory": r["subcategory"] or "",
                "location": r["location"] or "",
                "remote": bool(r["remote"]),
                "bay_area": bool(r["bay_area"]),
                "chicagoland": bool(r["chicagoland"]),
                "travel": r["travel"] or "",
                "travel_reason": r["travel_reason"] or "",
                "url": r["url"] or "",
                "posted": r["posted_at"],
                "first_seen": r["first_seen_at"],
                # Items from a source's very first import aren't "new" - they're the baseline.
                "baseline": store.is_baseline(r, first_runs),
                "closed_at": r["closed_at"],
                "status": r["status"],
                "status_reason": r["status_reason"] or "",
                "terms": _loads(r["terms"], []),
                "class_years": _loads(r["class_years"], []),
                "score": r["eligibility_score"],
                "reasons": _loads(r["eligibility_reasons"], []),
                "early": bool(r["early_career"]),
                "registry_id": r["registry_id"],
                "sources": sorted(set(sources_by_opp.get(r["id"], []))),
                "deadlines": deadlines.get(r["id"], []),
                "extra": {k: extra[k] for k in EXTRA_KEYS if k in extra},
            }
        )

    me = class_for_grad_year(cfg.grad_year, today_local())
    return {
        "generated_at": now_iso(),
        "today": today_local().isoformat(),
        "title": cfg.get("dashboard.title", "Opportunity Radar"),
        "url": cfg.get("dashboard.url", ""),
        "profile": {
            "grad_year": cfg.grad_year,
            "class_year": me,
            "next_class_year": CLASS_YEARS[CLASS_YEARS.index(me) + 1] if me and me != "senior" else None,
            "allow_partial": bool(cfg.get("travel.allow_partial", False)),
        },
        "field_labels": FIELD_LABELS,
        "items": items,
        "sources": collect_sources(conn, cfg),
        "manual_links": cfg.get("dashboard.manual_check_links", []) or [],
        "page_changes": collect_page_changes(conn),
        "watched_pages": collect_watched_pages(conn),
        "suggestions": [dict(s) for s in conn.execute(
            "SELECT id, kind, name, url, why, target_config, found_at FROM suggestions WHERE status = 'pending' ORDER BY found_at DESC"
        )],
        "repo": "TheAaravPatel/opportunity-radar",
    }


def collect_sources(conn: sqlite3.Connection, cfg: Config) -> list[dict]:
    """Source-health rows, with a simple ok / failing / stale / disabled state."""
    seen = {}
    for s in conn.execute("SELECT * FROM sources ORDER BY id"):
        seen[s["id"]] = dict(s)
    out = []
    for source_id, options in cfg.sources.items():
        row = seen.pop(source_id, {})
        state = _health_state(row, options)
        out.append(
            {
                "id": source_id,
                "name": options.get("name", source_id),
                "homepage": options.get("homepage", ""),
                "group": options.get("group", "") if isinstance(options.get("group"), str) else ",".join(options.get("group") or []),
                "enabled": options.get("enabled", True),
                "state": state,
                "last_success": row.get("last_success_at"),
                "last_attempt": row.get("last_attempt_at"),
                "upstream_updated": row.get("upstream_updated_at"),
                "items": row.get("items_last_run"),
                "error": row.get("last_error"),
                "failures": row.get("consecutive_failures", 0),
                "warnings": _loads(row.get("warnings"), []),
                "credit": options.get("credit", ""),
            }
        )
    return out


def _health_state(row: dict, options: dict) -> str:
    if not options.get("enabled", True):
        return "disabled"
    if any("waiting for GitHub Secret" in w for w in _loads(row.get("warnings"), [])):
        return "needs secret"
    if not row:
        return "never run"
    if row.get("consecutive_failures"):
        return "failing"
    upstream = row.get("upstream_updated_at")
    if upstream and now_utc() - parse_datetime(upstream) > timedelta(days=STALE_UPSTREAM_DAYS):
        return "stale"
    return "ok"


def collect_page_changes(conn: sqlite3.Connection) -> list[dict]:
    """Watched pages that changed in the last 14 days (filled in by the page-watcher)."""
    cutoff = to_iso(now_utc() - timedelta(days=14))
    rows = conn.execute(
        """SELECT p.id, COALESCE(p.link, p.url) AS url, p.kind, p.last_changed_at, p.fields,
                  COALESCE(p.name, p.id) AS title, p.org AS organization,
                  (SELECT diff FROM page_snapshots s WHERE s.page_id = p.id AND s.changed = 1
                   ORDER BY s.id DESC LIMIT 1) AS diff
           FROM pages p
           WHERE p.last_changed_at >= ? ORDER BY p.last_changed_at DESC""",
        (cutoff,),
    ).fetchall()
    return [{**dict(r), "fields": [f for f in (r["fields"] or "").split(",") if f]} for r in rows]


def collect_watched_pages(conn: sqlite3.Connection) -> list[dict]:
    """Every page the page-watcher checks, for the Sources tab."""
    rows = conn.execute(
        """SELECT id, kind, COALESCE(link, url) AS url, COALESCE(name, id) AS name, org, last_checked_at,
                  last_changed_at, consecutive_failures, last_error, fetch_method, text_chars
           FROM pages ORDER BY kind DESC, name"""
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- output
def build_dashboard(conn: sqlite3.Connection, cfg: Config, out_dir: Path) -> Path:
    data = collect_data(conn, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "data.json").write_text(data_json, encoding="utf-8")
    (out_dir / "deadlines.ics").write_text(build_ics(data), encoding="utf-8")

    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    html = env.get_template("index.html.j2").render(
        title=data["title"],
        generated_at=data["generated_at"],
        css=(HERE / "static" / "app.css").read_text(encoding="utf-8"),
        js=(HERE / "static" / "app.js").read_text(encoding="utf-8"),
        # "</" would end the <script> tag early, so escape it inside the JSON.
        data_json=data_json.replace("</", "<\\/"),
    )
    index = out_dir / "index.html"
    index.write_text(html, encoding="utf-8")
    return index


def _ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(data: dict) -> str:
    """An all-day calendar event for each upcoming (or recent) deadline."""
    stamp = data["generated_at"].replace("-", "").replace(":", "")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Opportunity Radar//deadlines//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Opportunity Radar deadlines",
    ]
    today = data["today"]
    for item in data["items"]:
        for d in item["deadlines"]:
            if d["date"] < today and item["status"] == "closed":
                continue
            day = d["date"].replace("-", "")
            kind = "Opens" if d["kind"] == "opens" else d["kind"].capitalize()
            summary = f"{kind}: {item['title']} ({item['org']})"
            description = " - ".join(filter(None, [d["label"], "approximate date" if d["approx"] else "", item["url"]]))
            lines += [
                "BEGIN:VEVENT",
                f"UID:radar-deadline-{d['id']}@opportunity-radar",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{day}",
                f"SUMMARY:{_ics_escape(summary)}",
                f"DESCRIPTION:{_ics_escape(description)}",
                f"URL:{item['url']}" if item["url"].startswith("http") else "X-NO-URL:1",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
