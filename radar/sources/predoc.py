"""PREDOC (predoc.org) research-assistant listings.

Two pages, same layout:
  /ra-opportunities  part-time + summer RA roles and internships (some for
                     current undergrads) - the one you want now
  /opportunities     full-time post-baccalaureate predoc jobs (need a BA) -
                     shipped disabled until you're about to graduate

Each listing is an <article> (checked against the live page, Nov 2026):

  <article class="all sorted currentUnder postgrads ...">
    <div class="copy">
      <h2><a href="https://bit.ly/...">Summer 2027 Economics Internship</a></h2>
      <div class="swiss-text"><p>
        <strong>Sponsoring Researchers:</strong> Federal Reserve Board<br/>
        <strong>Sponsoring Institution:</strong> Federal Reserve Board<br/>
        <strong>Fields of Research:</strong> Microeconomics, Macroeconomics, ...<br/>
        <strong>Deadline:</strong> October 31, 2026</p>

The article's classes say who it's for: "currentUnder" = current undergrads,
"postgrads" = people who already have a BA. Old listings linger on the page, so
anything whose deadline has passed comes out "closed".
"""

from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup, NavigableString, Tag

from radar.models import Deadline, FetchResult, Item
from radar.sources.base import Source, register
from radar.sources.conferences_common import parse_date_range
from radar.timeutil import today_local

AUDIENCE = {"currentunder": "current undergraduates", "postgrads": "college graduates (post-baccalaureate)"}


def _labeled_fields(paragraph: Tag) -> dict[str, str]:
    """<strong>Label:</strong> value<br/> pairs -> {"label": "value"}."""
    fields: dict[str, str] = {}
    for strong in paragraph.find_all("strong"):
        label = strong.get_text(" ", strip=True).rstrip(":").strip().lower()
        if not label:
            continue
        parts = []
        for sibling in strong.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "strong":
                break
            parts.append(sibling if isinstance(sibling, NavigableString) else sibling.get_text(" "))
        value = re.sub(r"\s+", " ", " ".join(parts)).strip(" :")
        if value:
            fields[label] = value
    return fields


def _type_for(title: str) -> str:
    low = title.lower()
    if re.search(r"fellow|scholar", low) and "research assistant" not in low:
        return "fellowship"
    if re.search(r"research assistant|\bra\b|predoc|pre-doc|research (coordinator|associate|professional|specialist)", low):
        return "RA"
    return "research program"


@register("predoc")
class PredocSource(Source):
    def fetch(self) -> FetchResult:
        html = self.http.get_text(self.options["url"])
        return parse_predoc(html, self.options, today_local())


def parse_predoc(html: str, options: dict, today: date) -> FetchResult:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for article in soup.find_all("article"):
        heading = article.find(["h2", "h3"])
        if not heading:
            continue
        title = heading.get_text(" ", strip=True)
        link = heading.find("a")
        url = link.get("href") if link and link.get("href") else options.get("url", "")
        paragraph = article.select_one(".swiss-text p") or article.find("p")
        fields = _labeled_fields(paragraph) if paragraph else {}

        classes = [c.lower() for c in article.get("class") or []]
        audience = [AUDIENCE[c] for c in classes if c in AUDIENCE]
        institution = fields.get("sponsoring institution") or fields.get("sponsoring researchers") or "PREDOC listing"
        deadline_text = fields.get("deadline", "")

        deadlines, status, reason = [], "open", ""
        start, _ = parse_date_range(deadline_text, today)
        if start:
            kind = "priority" if "priority" in deadline_text.lower() else "application"
            deadlines.append(Deadline(kind=kind, date=start, label=deadline_text[:80]))
            if start < today.isoformat() and "rolling" not in deadline_text.lower():
                status, reason = "closed", f"Deadline {deadline_text} has passed"
        elif "rolling" in deadline_text.lower():
            reason = "Rolling applications"

        # Text for eligibility scoring: who it's for, in words the rules understand.
        who = ""
        if audience == ["college graduates (post-baccalaureate)"]:
            who = "For post-baccalaureate applicants who already have a bachelor's degree."
        elif "current undergraduates" in audience:
            who = "Open to current undergraduates."
        items.append(
            Item(
                source_item_id=url if url.startswith("http") else f"{institution}|{title}",
                title=title,
                organization=institution,
                url=url,
                type=_type_for(title),
                fields=list(options.get("fields", ["econ"])),
                subcategory=fields.get("fields of research", "")[:120],
                text=" ".join(filter(None, [who, fields.get("sponsoring researchers", ""), deadline_text])),
                deadlines=deadlines,
                status=status,
                status_reason=reason,
                extra={
                    "notes": "; ".join(f"{k.capitalize()}: {v}" for k, v in fields.items()),
                    "audience": audience,
                },
            )
        )
    return FetchResult(items=items)
