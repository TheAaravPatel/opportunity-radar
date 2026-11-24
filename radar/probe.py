"""`python -m radar probe URL [URL ...]`: show what a public page or feed looks like.

A debugging aid for writing new sources. It fetches each URL once (with the
same polite HTTP client as everything else) and prints a compact outline:
  * HTML  -> headings, and the surrounding markup of the first few elements
             that contain words like "Deadline" (so a parser can target them)
  * JSON  -> top-level structure and the first item
  * RSS / Atom -> the first few entries' title / link / date
Run it from the Verify sources workflow ("probe_urls" input) to see pages this
development container can't reach. Nothing is stored.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from radar.http import Http

MARKER = "=== PROBE"
LABEL_WORDS = re.compile(r"deadline|apply by|sponsoring|institution|due date|application", re.I)


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def outline_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [f"title: {_short(soup.title.get_text() if soup.title else '', 120)}"]
    text = soup.get_text(" ", strip=True)
    lines.append(f"visible text: {len(text)} chars")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    lines.append(f"headings ({len(headings)}):")
    lines += [f"  <{h.name} class={'.'.join(h.get('class') or [])}> {_short(h.get_text(), 100)}" for h in headings[:40]]

    hits = [el for el in soup.find_all(string=LABEL_WORDS)][:4]
    lines.append(f"elements containing label words: {len(soup.find_all(string=LABEL_WORDS))}")
    for i, hit in enumerate(hits, 1):
        chain, node = [], hit.parent
        for _ in range(6):
            if node is None or node.name in (None, "[document]"):
                break
            chain.append(f"{node.name}.{'.'.join(node.get('class') or [])}")
            node = node.parent
        block = hit.parent
        for _ in range(3):              # climb to a block big enough to show one listing
            if block.parent is not None and len(block.get_text()) < 400:
                block = block.parent
        lines.append(f"--- hit {i}: path (inner->outer): {' < '.join(chain)}")
        lines.append(_short(str(block), 1800))
    return lines


def outline_json(data) -> list[str]:
    if isinstance(data, dict):
        lines = [f"object keys: {list(data)[:20]}"]
        first_list = next((v for v in data.values() if isinstance(v, list) and v), None)
        if first_list:
            lines.append(f"first list: {len(first_list)} items; first item:")
            lines.append(_short(json.dumps(first_list[0])[:3000], 2500))
        else:
            lines.append(_short(json.dumps(data)[:3000], 2500))
        return lines
    if isinstance(data, list):
        return [f"array of {len(data)}; first item:", _short(json.dumps(data[0]) if data else "", 2500)]
    return [f"scalar: {_short(str(data), 200)}"]


def outline_feed(text: str) -> list[str]:
    root = ET.fromstring(text)
    tag = root.tag.split("}")[-1]
    entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    lines = [f"feed root: {tag}; {len(entries)} entries"]
    for entry in entries[:5]:
        fields = {child.tag.split("}")[-1]: (child.text or child.get("href") or "") for child in entry}
        lines.append(f"  - {_short(fields.get('title', ''), 110)} | {fields.get('pubDate') or fields.get('updated') or fields.get('published', '')} | {fields.get('link', '')}")
        lines.append(f"    {_short(BeautifulSoup(fields.get('description') or fields.get('summary') or fields.get('content') or '', 'html.parser').get_text(' '), 300)}")
    return lines


def probe(urls: list[str], http: Http) -> int:
    for url in urls:
        print(f"{MARKER} {url}")
        try:
            response = http.get(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {type(exc).__name__}: {exc}")
            continue
        kind = response.headers.get("content-type", "")
        print(f"  HTTP {response.status_code} | {kind} | {len(response.content)} bytes | final URL {response.url}")
        body = response.text
        try:
            if "json" in kind or body.lstrip().startswith(("{", "[")):
                lines = outline_json(response.json())
            elif "xml" in kind or body.lstrip().startswith("<?xml") or "<rss" in body[:500]:
                lines = outline_feed(body)
            else:
                lines = outline_html(body)
        except Exception as exc:  # noqa: BLE001
            lines = [f"could not outline: {type(exc).__name__}: {exc}", _short(body, 1500)]
        for line in lines:
            print(f"  {line}")
    return 0
