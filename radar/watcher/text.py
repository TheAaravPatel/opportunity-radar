"""HTML -> clean lines of visible text, so two snapshots can be compared."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

# Never part of the content we care about.
NOISE_TAGS = ["script", "style", "noscript", "template", "svg", "nav", "header", "footer", "form", "iframe", "button"]
# Cookie banners and similar pop-ups (matched against id / class).
NOISE_SELECTOR = "[id*=cookie], [class*=cookie], [id*=onetrust], [class*=onetrust], [class*=consent], [id*=consent]"
# Each of these starts a new line in the text.
BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "dt", "dd", "br",
              "section", "article", "table", "ul", "ol", "blockquote", "pre", "hr", "main"]
# Lines that change on every visit without meaning anything.
NOISE_LINE = re.compile(r"^(skip to (main )?content|back to top|share( this)?|print|menu|search|close)$", re.I)


def page_lines(html: str, selector: str | None = None) -> list[str]:
    """Visible text of the page as a list of lines.

    `selector` (CSS, e.g. "main" or "li.iCIMS_JobCardItem h3") limits it to
    part of the page; by default <main> is used when the page has one.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.select(NOISE_SELECTOR):
        tag.decompose()
    # Mark block boundaries with newlines so "Deadline: <b>Jan 12</b>" stays
    # on one line but two paragraphs don't run together.
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))

    roots = soup.select(selector) if selector else (soup.select("main") or [soup.body or soup])
    lines = _lines(roots)
    if not lines and not selector:
        lines = _lines([soup.body or soup])   # an empty <main> (content elsewhere on the page)
    return lines


def _lines(roots) -> list[str]:
    lines: list[str] = []
    for root in roots:
        for raw in root.get_text(" ").split("\n"):
            line = " ".join(raw.split())
            if line and not NOISE_LINE.match(line) and (not lines or lines[-1] != line):
                lines.append(line)
    return lines
