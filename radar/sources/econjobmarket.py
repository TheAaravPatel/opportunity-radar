"""EconJobMarket - only through its OFFICIAL public JSON feed. Ships DISABLED.

EJM's terms ban spiders/robots except its own tools; this JSON feed IS its own
tool (the documented feed behind github.com/econjobmarket/ad-display, meant for
showing EJM ads on other websites). Because our dashboard is public, ask EJM
before turning this on (enabled: true in sources.yaml).

https://backend.econjobmarket.org/data/zz_public/json/Ads -> a JSON array of ads:
  {"posid": 12474, "oid": 1, "adtitle": "...", "name": "Harvard University",
   "department": "Economics", "position_type": "Pre-doc", "position_country": "USA",
   "enddate": "2027-01-15 23:59:00", "adtext": "<p>HTML description</p>"}

Almost every ad is PhD-level, so we keep only pre-doc / RA / undergraduate ones.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from radar.models import Deadline, FetchResult, Item
from radar.sources.base import Source, register
from radar.timeutil import to_date_str

KEEP = re.compile(r"pre-?doc|research assistant|\bra\b|undergrad|intern|research professional", re.I)


@register("econjobmarket")
class EconJobMarketSource(Source):
    def fetch(self) -> FetchResult:
        return parse_ejm(self.http.get_json(self.options["url"]))


def parse_ejm(ads: list[dict]) -> FetchResult:
    items, skipped = [], 0
    for ad in ads or []:
        title = (ad.get("adtitle") or "").strip()
        kind = ad.get("position_type") or ""
        if not title or not KEEP.search(f"{title} {kind}"):
            skipped += 1
            continue
        deadline = to_date_str(ad.get("enddate"))
        country = ad.get("position_country") or ""
        items.append(
            Item(
                source_item_id=str(ad.get("posid")),
                title=title,
                organization=" - ".join(filter(None, [ad.get("name"), ad.get("department")])) or "EconJobMarket",
                url=f"https://econjobmarket.org/positions/{ad.get('posid')}",
                type="RA",
                fields=["econ"],
                subcategory=kind,
                locations=[country] if country else [],
                text=BeautifulSoup(ad.get("adtext") or "", "html.parser").get_text(" ", strip=True)[:3000],
                deadlines=[Deadline(kind="application", date=deadline, label="EJM deadline")] if deadline else [],
            )
        )
    return FetchResult(items=items, warnings=[f"skipped {skipped} faculty/postdoc ads"] if skipped else [])
