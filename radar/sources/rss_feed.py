"""Generic RSS / Atom source - for blogs that post opportunities.

Feeds are made for automated reading, so they're a polite source. Configure
each feed in sources.yaml:

  econ_ug_blog:
    type: rss_feed
    url: https://econugblog.wordpress.com/feed/
    fields: [econ]
    title_must_match: "intern|research assistant|fellowship|program|challenge"
    max_age_days: 60          # ignore older posts
    keep_missing_days: 60     # a feed only shows its latest posts (see store.py)

From each post we take: title, link, date, and from its text a "Location: ..."
line and an "Apply by <date>" / "Deadline: <date>" deadline (if present).
Posts whose deadline has passed come out closed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from radar.models import Deadline, FetchResult, Item
from radar.sources.base import Source, register
from radar.sources.conferences_common import parse_date_range
from radar.timeutil import to_date_str, today_local

ATOM = "{http://www.w3.org/2005/Atom}"
_DEADLINE = re.compile(
    r"(?:apply by|deadline|applications? (?:close|closes|are due|due)|due|submit by)\s*(?:is|:|-|–)?\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:,?\s*20\d\d)?)",
    re.I,
)
_LOCATION = re.compile(r"\bLocation:\s*([^.\n]{2,80}?)(?:\s{2,}|\.|\n|Apply|$)", re.I)


def _text(node, name: str) -> str:
    child = node.find(name)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_feed(xml_text: str) -> list[dict]:
    """[{title, link, date, html}] for RSS 2.0 or Atom."""
    root = ET.fromstring(xml_text)
    posts = []
    for item in root.iter("item"):                       # RSS 2.0
        published = _text(item, "pubDate")
        try:
            day = parsedate_to_datetime(published).date().isoformat() if published else None
        except (TypeError, ValueError):
            day = None
        posts.append({"title": _text(item, "title"), "link": _text(item, "link"), "date": day,
                      "html": _text(item, "description") or _text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")})
    for entry in root.iter(f"{ATOM}entry"):              # Atom
        link = entry.find(f"{ATOM}link")
        posts.append({"title": _text(entry, f"{ATOM}title"), "link": link.get("href") if link is not None else "",
                      "date": to_date_str(_text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")),
                      "html": _text(entry, f"{ATOM}summary") or _text(entry, f"{ATOM}content")})
    return posts


def _type_for(title: str) -> str:
    low = title.lower()
    if re.search(r"research assistant|predoc|pre-doc|\bra\b", low):
        return "RA"
    if re.search(r"fellowship|scholarship|scholars", low):
        return "fellowship"
    if re.search(r"conference|summit|challenge|competition|case comp", low):
        return "conference"
    if re.search(r"insight|discovery|explore|crew|leadership program|early id", low):
        return "early-career program"
    return "industry internship"


def _organization(title: str, fallback: str) -> str:
    """'Cost Accountant Intern – Summer 2027 – NVIDIA' -> 'NVIDIA'; '... at Johns Hopkins' -> 'Johns Hopkins'."""
    parts = [p.strip() for p in re.split(r"\s+[–—-]\s+", title) if p.strip()]
    if len(parts) >= 2 and not re.search(r"\b(20\d\d|summer|fall|spring|winter|apply)\b", parts[-1], re.I):
        return parts[-1]
    match = re.search(r"\bat\s+([A-Z][\w&.,' ]{2,60})$", title)
    return match.group(1).strip() if match else fallback


@register("rss_feed")
class RssFeedSource(Source):
    def fetch(self) -> FetchResult:
        text = self.http.get_text(self.options["url"])
        return parse_rss_items(parse_feed(text), self.options, today_local())


def parse_rss_items(posts: list[dict], options: dict, today: date) -> FetchResult:
    must = re.compile(options["title_must_match"], re.I) if options.get("title_must_match") else None
    must_not = re.compile(options["title_must_not_match"], re.I) if options.get("title_must_not_match") else None
    max_age = int(options.get("max_age_days", 60))
    oldest = (today - timedelta(days=max_age)).isoformat()
    feed_name = options.get("name", "feed")
    items, skipped = [], 0
    for post in posts:
        title = re.sub(r"\s+", " ", post["title"]).strip()
        if not title or not post["link"] or (post["date"] and post["date"] < oldest):
            continue
        if (must and not must.search(title)) or (must_not and must_not.search(title)):
            skipped += 1
            continue
        body = BeautifulSoup(post["html"] or "", "html.parser").get_text(" ", strip=True)
        deadlines, status, reason = [], None, ""
        match = _DEADLINE.search(f"{title}. {body}")
        if match:
            day, _ = parse_date_range(match.group(1), date.fromisoformat(post["date"]) if post["date"] else today)
            if day:
                deadlines.append(Deadline(kind="application", date=day, label="Apply by"))
                if day < today.isoformat():
                    status, reason = "closed", "Deadline has passed"
        location_match = _LOCATION.search(body)
        location = location_match.group(1).strip(" ,;") if location_match else ""
        remote = bool(re.search(r"\b(virtual|remote|online)\b", location or body[:300], re.I))
        items.append(
            Item(
                source_item_id=post["link"],
                title=title,
                organization=_organization(title, feed_name),
                url=post["link"],
                type=_type_for(title),
                fields=list(options.get("fields", [])),
                locations=[location] if location and not re.match(r"(?i)virtual|online", location) else [],
                remote=remote,
                posted_at=post["date"],
                text=body[:3000],
                deadlines=deadlines,
                status=status,
                status_reason=reason,
                extra={"notes": body[:400]},
            )
        )
    warnings = [f"skipped {skipped} posts that don't look like opportunities"] if skipped else []
    return FetchResult(items=items, warnings=warnings)
