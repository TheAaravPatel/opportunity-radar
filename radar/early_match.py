"""Linking early-career job postings to the programs in config/early_programs.yaml.

Job feeds (SimplifyJobs, bank and trading-firm job boards) sometimes carry the
actual application for a registry program, e.g. "2027 Morgan Stanley Sophomore
Summer Analyst Program". When that happens, the program's card says "Posting
is live" with the link, and its status becomes open - no more guessing from
the usual opening month.

A posting matches a program when
  1. the company is the program's `firm` (or one of its `match_orgs`),
     ignoring "Inc.", "&", spaces...; and
  2. the title fits that program: one of its `match_titles` phrases is in the
     title, or the title shares a distinctive word with the program's name or
     `aka` names ("Possibilities", "Ignite", "Sophomore"...). Being the firm's
     only program isn't enough: Bain's "CREW Program" is not Bain's "BEL".

Early-career postings that look like programs (an insight day, a summit...)
but match no program become suggestions on the dashboard's Suggested tab.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date

from radar.classify import compile_phrases
from radar.normalize import normalize_org
from radar.timeutil import now_iso

# Words too common in program names to tell two programs apart.
STOP_WORDS = {
    "program", "programs", "programme", "internship", "internships", "intern", "interns", "summer", "analyst",
    "analysts", "associate", "early", "career", "careers", "series", "student", "students", "undergraduate",
    "undergraduates", "the", "and", "for", "with", "from", "into", "your", "year", "years", "2026", "2027", "2028",
    "virtual", "event", "events", "week", "day", "days", "class", "talent", "experience",
}
STOP_WORDS |= {w[:-1] for w in STOP_WORDS if len(w) > 4 and w.endswith("s")}


def org_key(name: str) -> str:
    """'JPMorgan Chase & Co.' -> 'jpmorganchase'."""
    return re.sub(r"[^a-z0-9]", "", normalize_org(name or ""))


def words(text: str) -> set[str]:
    """Lowercase words of 3+ letters, with simple plurals folded ("Insights" = "Insight")."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) >= 3:
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


class ProgramMatcher:
    def __init__(self, programs: list[dict]):
        self.programs = [p for p in programs if p.get("id") and p.get("firm")]
        self.by_firm: dict[str, list[dict]] = defaultdict(list)
        for p in self.programs:
            for name in [p["firm"], *(p.get("match_orgs") or [])]:
                self.by_firm[org_key(name)].append(p)
        self.distinctive = {}
        for p in self.programs:
            firm_words = set().union(*(words(n) for n in [p["firm"], *(p.get("match_orgs") or [])]))
            names = " ".join([p.get("program", ""), *(p.get("aka") or [])])
            self.distinctive[p["id"]] = words(names) - STOP_WORDS - firm_words

    def candidates(self, org: str) -> list[dict]:
        key = org_key(org)
        if not key:
            return []
        if key in self.by_firm:                    # exact firm first ("Citadel" vs "Citadel Securities")
            return self.by_firm[key]
        for firm, programs in self.by_firm.items():  # then "Citi" ~ "Citigroup", "IMC" ~ "IMC Trading"
            if min(len(firm), len(key)) >= 3 and (key.startswith(firm) or firm.startswith(key)):
                return programs
        return []

    def match(self, org: str, title: str) -> str | None:
        """The registry id of the program this posting is for, or None."""
        programs = self.candidates(org)
        if not programs:
            return None
        low = (title or "").lower()
        for p in programs:
            if any(phrase.lower() in low for phrase in p.get("match_titles") or []):
                return p["id"]
        title_words = words(title)
        scored = sorted(((len(self.distinctive[p["id"]] & title_words), p) for p in programs),
                        key=lambda pair: -pair[0])
        best_score, best = scored[0]
        if best_score and (len(scored) == 1 or scored[1][0] < best_score):
            return best["id"]
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]


def link_early_postings(conn, cfg, today: date) -> dict:
    """Match every open early-career posting to a program (stored in the
    early_programs table) and refresh the keyword-detection suggestions."""
    programs = (cfg.load_file("early_programs.yaml") or {}).get("programs") or []
    matcher = ProgramMatcher(programs)
    early = cfg.keywords.get("early_career", {}) or {}
    programish = compile_phrases(list(early.get("weak") or []) + list(early.get("program_words") or []))

    registry_rows = {r["registry_id"]: r["id"] for r in conn.execute(
        """SELECT o.id, o.registry_id FROM opportunities o JOIN sources s ON s.id = o.primary_source
           WHERE s.type = 'early_programs_registry'""")}
    links: dict[str, list[int]] = defaultdict(list)
    suggestion_urls: set[str] = set()
    now = now_iso()
    for row in conn.execute(
        """SELECT id, organization, title, url, fields, class_years, primary_source FROM opportunities
           WHERE early_career = 1 AND registry_id IS NULL AND status != 'closed'"""
    ).fetchall():
        class_years = json.loads(row["class_years"] or "[]")
        program_id = matcher.match(row["organization"], row["title"])
        if program_id:
            links[program_id].append(row["id"])
        elif programish and programish.search(row["title"] or "") and row["url"]:
            suggestion_urls.add(row["url"])
            draft = {"id": _slug(f"{row['organization']} {row['title']}"), "firm": row["organization"],
                     "program": row["title"], "url": row["url"],
                     "fields": [f for f in (row["fields"] or "").split(",") if f], "class_years": class_years,
                     "notes": f"Found by keyword detection in {row['primary_source']} on {today.isoformat()}"}
            conn.execute(
                """INSERT OR IGNORE INTO suggestions (kind, name, url, why, target_config, payload, source, found_at, status)
                   VALUES ('early_program', ?, ?, ?, 'config/early_programs.yaml', ?, 'keyword-detection', ?, 'pending')""",
                (f"{row['organization']}: {row['title']}", row["url"],
                 f"Looks like an early-career program (seen in {row['primary_source']}) but isn't in the registry yet.",
                 json.dumps(draft), now),
            )
    # A suggestion whose posting has closed (or is now in the registry) is no longer useful.
    for s in conn.execute("SELECT id, url FROM suggestions WHERE kind='early_program' AND source='keyword-detection' AND status='pending'").fetchall():
        if s["url"] not in suggestion_urls:
            conn.execute("UPDATE suggestions SET status = 'expired' WHERE id = ?", (s["id"],))

    for program in matcher.programs:
        conn.execute(
            """INSERT INTO early_programs (id, opportunity_id, matched_opportunity_ids) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET opportunity_id = excluded.opportunity_id,
                                             matched_opportunity_ids = excluded.matched_opportunity_ids""",
            (program["id"], registry_rows.get(program["id"]), json.dumps(sorted(links.get(program["id"], [])))),
        )
    return {"matched": sum(len(v) for v in links.values()), "suggested": len(suggestion_urls)}
