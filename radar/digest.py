"""The daily email digest (sent through Gmail SMTP with an app password).

Sections, in this order (from the spec):
  1. Deadlines in the next 14 days (all types). Deadlines exactly 14, 7 or 2
     days away are highlighted as reminders.
  2. Early programs opening or closing this week, plus "usually opens in
     2 weeks" warnings for programs that haven't posted yet.
  3. New since the last digest, by field.
  4. New conferences, hackathons and travel scholarships.
  5. Stanford (and other watched) pages that changed.
  6. Sources that failed.

When is it sent? Only if there's something worth an email: anything new, a
reminder firing today (for something you're eligible for), an early-program
opening/closing/warning, a changed page, or a source that newly started failing.
A source that has been failing for days doesn't trigger a new email every day.

Secrets (GitHub Actions -> Settings -> Secrets): GMAIL_ADDRESS,
GMAIL_APP_PASSWORD, and optionally DIGEST_TO (defaults to GMAIL_ADDRESS).
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import sqlite3
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar import store
from radar.config import Config
from radar.db import get_meta, set_meta
from radar.eligibility import class_for_grad_year
from radar.models import FIELD_LABELS, FIELDS
from radar.timeutil import now_iso, today_local

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent / "templates"
CONFERENCE_TYPES = ("conference", "hackathon", "travel scholarship")
EARLY_TYPES = ("early-career program", "pipeline program")


@dataclass
class Digest:
    today: date
    first_digest: bool
    deadlines: list[dict] = field(default_factory=list)
    reminders: list[dict] = field(default_factory=list)
    early_opening: list[dict] = field(default_factory=list)
    early_closing: list[dict] = field(default_factory=list)
    early_warnings: list[dict] = field(default_factory=list)
    early_unreported: list[dict] = field(default_factory=list)   # opening/closing items not emailed yet
    new_early_postings: list[dict] = field(default_factory=list)
    new_by_field: dict[str, list[dict]] = field(default_factory=dict)
    new_counts: dict[str, int] = field(default_factory=dict)
    new_conferences: list[dict] = field(default_factory=list)
    page_changes: list[dict] = field(default_factory=list)
    failed_sources: list[dict] = field(default_factory=list)
    newly_failed: list[str] = field(default_factory=list)
    totals: dict = field(default_factory=dict)

    new_unique: int = 0     # distinct new items (one item can be in two fields)

    @property
    def new_total(self) -> int:
        return self.new_unique + len(self.new_conferences)

    @property
    def should_send(self) -> bool:
        return bool(
            self.new_total or self.reminders or self.early_unreported or self.early_warnings or self.page_changes or self.newly_failed or self.first_digest
        )

    def subject(self) -> str:
        parts = []
        if self.reminders:
            parts.append(f"{len(self.reminders)} reminder{'s' if len(self.reminders) != 1 else ''}")
        soon = [d for d in self.deadlines if d["days"] <= 7]
        if soon:
            parts.append(f"{len(soon)} due this week")
        if self.new_total:
            parts.append(f"{self.new_total} new")
        if self.early_warnings or self.early_opening:
            parts.append("programs opening")
        summary = ", ".join(parts) or "daily digest"
        return f"Opportunity Radar - {summary} ({self.today:%b} {self.today.day})"


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _open_to_me(class_years: list[str], me: str | None) -> bool:
    return not class_years or (me in class_years)


def _item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "org": row["organization"],
        "url": row["url"] or "",
        "type": row["type"],
        "location": row["location"] or "",
        "fields": [f for f in (row["fields"] or "").split(",") if f],
        "score": row["eligibility_score"],
        "status": row["status"],
        "status_reason": row["status_reason"] or "",
        "class_years": _loads(row["class_years"], []),
        "early": bool(row["early_career"]),
        "registry_id": row["registry_id"],
        "extra": _loads(row["extra"], {}),
    }


def build_digest(conn: sqlite3.Connection, cfg: Config) -> Digest:
    today = today_local()
    me = class_for_grad_year(cfg.grad_year, today)
    window = int(cfg.get("digest.deadline_window_days", 14))
    reminder_days = set(cfg.get("digest.reminder_days", [14, 7, 2]) or [])
    warn_days = int(cfg.get("digest.expected_open_warning_days", 14))
    cap = int(cfg.get("digest.max_new_per_field", 15))
    last = get_meta(conn, "last_digest_at")
    digest = Digest(today=today, first_digest=last is None)

    # 1. Deadlines in the next N days ---------------------------------------
    rows = conn.execute(
        """SELECT d.id AS deadline_id, d.kind, d.date, d.label, d.approx, o.*
           FROM deadlines d JOIN opportunities o ON o.id = d.opportunity_id
           WHERE d.active = 1 AND o.status != 'closed' AND d.date >= ? AND d.date <= ?
             AND d.kind != 'event'          -- a conference starting isn't a deadline
           ORDER BY d.date""",
        (today.isoformat(), (today + timedelta(days=window)).isoformat()),
    ).fetchall()
    sent = {(r["deadline_id"], r["days_before"]) for r in conn.execute("SELECT * FROM reminders_sent")}
    for r in rows:
        days = (date.fromisoformat(r["date"]) - today).days
        entry = {
            **_item(r), "deadline_id": r["deadline_id"], "kind": r["kind"], "date": r["date"],
            "label": r["label"], "approx": bool(r["approx"]), "days": days,
            "eligible": _open_to_me(_loads(r["class_years"], []), me),
        }
        entry["reminder"] = days in reminder_days and entry["eligible"] and (r["deadline_id"], days) not in sent
        digest.deadlines.append(entry)
        if entry["reminder"]:
            digest.reminders.append(entry)

    # 2. Early programs this week + "usually opens in 2 weeks" ----------------
    week_end = (today + timedelta(days=7)).isoformat()
    for r in conn.execute(
        f"""SELECT d.id AS deadline_id, d.kind, d.date, d.label, o.* FROM deadlines d JOIN opportunities o ON o.id = d.opportunity_id
            WHERE d.active = 1 AND o.type IN ({",".join("?" * len(EARLY_TYPES))})
              AND d.date >= ? AND d.date <= ? ORDER BY d.date""",
        (*EARLY_TYPES, today.isoformat(), week_end),
    ):
        entry = {**_item(r), "kind": r["kind"], "date": r["date"], "label": r["label"],
                 "days": (date.fromisoformat(r["date"]) - today).days,
                 "week_key": f"early_week:{r['deadline_id']}"}
        (digest.early_opening if r["kind"] == "opens" else digest.early_closing).append(entry)
        # Shown in every digest that week, but only triggers an email the first time.
        if not get_meta(conn, entry["week_key"]):
            digest.early_unreported.append(entry)

    for r in conn.execute(
        f"SELECT * FROM opportunities WHERE type IN ({','.join('?' * len(EARLY_TYPES))}) AND status IN ('closed', 'expected soon')",
        EARLY_TYPES,
    ):
        item = _item(r)
        start = item["extra"].get("expected_open_start")
        if not start:
            continue
        month_start = date.fromisoformat(start) + timedelta(days=warn_days)
        already = get_meta(conn, f"warned:{item['registry_id']}:{start}")
        if date.fromisoformat(start) <= today < month_start and not already:
            item["opens_month"] = f"{month_start:%B}"
            item["warn_key"] = f"warned:{item['registry_id']}:{start}"
            digest.early_warnings.append(item)

    # 3 + 4. New since last digest --------------------------------------------
    first_runs = store.first_ok_runs(conn)
    since = last or (today - timedelta(days=1)).isoformat()
    last_run = int(get_meta(conn, "last_digest_run_id", "0") or 0)
    new_rows = [
        r for r in conn.execute(
            """SELECT * FROM opportunities WHERE (first_seen_at > ? OR first_run_id > ?) AND status != 'closed'
               ORDER BY eligibility_score DESC, first_seen_at DESC""",
            (since, last_run),
        )
        # items from a source's very first import are the baseline, not "new"
        if not store.is_baseline(r, first_runs) and (r["first_run_id"] or 0) > last_run
    ]
    for r in new_rows:
        item = _item(r)
        if item["type"] in CONFERENCE_TYPES:
            digest.new_conferences.append(item)
            continue
        digest.new_unique += 1
        if item["early"] and item["type"] not in EARLY_TYPES:
            digest.new_early_postings.append(item)
        for f in item["fields"] or ["other"]:
            digest.new_counts[f] = digest.new_counts.get(f, 0) + 1
            bucket = digest.new_by_field.setdefault(f, [])
            if len(bucket) < cap:
                bucket.append(item)

    # 5. Changed pages ----------------------------------------------------------
    digest.page_changes = [
        dict(r) for r in conn.execute(
            """SELECT p.id, p.url, p.last_changed_at, o.title, o.organization,
                      (SELECT diff FROM page_snapshots s WHERE s.page_id = p.id AND s.changed = 1
                       ORDER BY s.fetched_at DESC LIMIT 1) AS diff
               FROM pages p LEFT JOIN opportunities o ON o.id = p.opportunity_id
               WHERE p.last_changed_at > ?""",
            (since,),
        )
    ]

    # 6. Failed sources ---------------------------------------------------------
    enabled = {sid for sid, opts in cfg.sources.items() if opts.get("enabled", True)}
    reported = set(_loads(get_meta(conn, "failing_reported"), []))
    for r in conn.execute("SELECT * FROM sources WHERE consecutive_failures > 0 ORDER BY id"):
        if r["id"] not in enabled:
            continue
        digest.failed_sources.append(dict(r))
        if r["id"] not in reported:
            digest.newly_failed.append(r["id"])

    digest.totals = {
        "open": conn.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'open'").fetchone()[0],
        "tracked": conn.execute("SELECT COUNT(*) FROM opportunities WHERE status != 'closed'").fetchone()[0],
    }
    return digest


def render(digest: Digest, cfg: Config) -> tuple[str, str]:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    context = {
        "d": digest,
        "fields": [*FIELDS, "other"],
        "field_labels": {**FIELD_LABELS, "other": "Other / unclassified"},
        "dashboard_url": cfg.get("dashboard.url", ""),
    }
    html = env.get_template("digest.html.j2").render(**context)
    text = env.get_template("digest.txt.j2").render(**context)
    return html, text


def send_email(subject: str, html: str, text: str, cfg: Config) -> None:
    sender = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = os.environ.get("DIGEST_TO") or sender
    if not sender or not password:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set (add them as GitHub Secrets)")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Opportunity Radar <{sender}>"
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    host = cfg.get("digest.smtp_host", "smtp.gmail.com")
    port = int(cfg.get("digest.smtp_port", 587))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(sender, password)
        smtp.send_message(msg)


def mark_sent(conn: sqlite3.Connection, digest: Digest) -> None:
    """Remember what this digest covered so the next one only shows news."""
    now = now_iso()
    set_meta(conn, "last_digest_at", now)
    conn.executemany(
        "INSERT OR IGNORE INTO reminders_sent (deadline_id, days_before, sent_at) VALUES (?, ?, ?)",
        [(r["deadline_id"], r["days"], now) for r in digest.reminders],
    )
    for item in digest.early_warnings:
        set_meta(conn, item["warn_key"], now)
    for item in digest.early_unreported:
        set_meta(conn, item["week_key"], now)
    last_run = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0] or 0
    set_meta(conn, "last_digest_run_id", str(last_run))
    set_meta(conn, "failing_reported", json.dumps(sorted(s["id"] for s in digest.failed_sources)))
    conn.commit()


def run_digest(conn: sqlite3.Connection, cfg: Config, dry_run: bool, out_dir: Path, force: bool = False) -> int:
    if not cfg.get("digest.enabled", True) and not force:
        print("digest disabled in settings.yaml")
        return 0
    digest = build_digest(conn, cfg)
    if not digest.should_send and not force:
        print("nothing new or due - no digest today")
        return 0
    html, text = render(digest, cfg)
    subject = digest.subject()
    if dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "digest.html").write_text(html, encoding="utf-8")
        (out_dir / "digest.txt").write_text(f"Subject: {subject}\n\n{text}", encoding="utf-8")
        print(f"dry run: wrote {out_dir / 'digest.html'} ({subject})")
        return 0
    try:
        send_email(subject, html, text, cfg)
    except Exception as exc:  # noqa: BLE001 - report clearly, don't crash the workflow
        print(f"digest NOT sent: {exc}")
        return 1
    mark_sent(conn, digest)
    print(f"digest sent: {subject}")
    return 0
