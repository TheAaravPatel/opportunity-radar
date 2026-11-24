"""Turning messy source data into comparable values.

The same internship often appears in several feeds with small differences:
  Simplify:  "Illumio" / "Engineering Intern - Architecture Platform" / "Sunnyvale, CA"
  zshah101:  "Illumio" / "Engineering Intern - Architecture Platform (Summer 2027)"
             / "Sunnyvale, California - HQ"
To recognize them as one opportunity we compare *normalized* versions:
lowercase, no punctuation, no "Inc."/"LLC", no "Summer 2027", city only.
"""

from __future__ import annotations

import re
import unicodedata

# Company-name endings that don't help tell companies apart.
ORG_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "plc", "lp", "llp", "the", "group", "holdings", "usa", "us",
}

# "Summer 2027", "Fall 2026", "(2027)", "2026-2027", "Summer '27" ...
_SEASON_YEAR = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*(?:of\s*)?'?(?:20)?\d\d\b", re.I)
_YEAR = re.compile(r"\b20\d\d(?:\s*[-/]\s*(?:20)?\d\d)?\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Short city aliases seen in job feeds.
CITY_ALIASES = {
    "nyc": "new york",
    "new york city": "new york",
    "sf": "san francisco",
    "south san francisco": "south san francisco",
    "washington dc": "washington",
    "washington d c": "washington",
    "dc": "washington",
}

# Cities we count as "Bay Area" for location filters and conference priority.
BAY_AREA_CITIES = {
    "stanford", "palo alto", "menlo park", "mountain view", "sunnyvale", "santa clara",
    "san jose", "cupertino", "redwood city", "redwood shores", "san mateo", "foster city",
    "burlingame", "san carlos", "belmont", "south san francisco", "san francisco",
    "oakland", "berkeley", "emeryville", "alameda", "fremont", "newark", "milpitas",
    "hayward", "san bruno", "brisbane", "daly city", "pleasanton", "dublin", "livermore",
    "walnut creek", "san ramon", "los altos", "los gatos", "campbell", "saratoga",
    "half moon bay", "novato", "san rafael", "richmond", "union city", "san leandro",
}

# Chicago and its suburbs (Cook, DuPage, Lake, Kane, Will counties) - plus the
# national labs nearby.
CHICAGOLAND_CITIES = {
    "chicago", "north chicago", "evanston", "skokie", "wilmette", "glenview", "northbrook",
    "northfield", "deerfield", "riverwoods", "bannockburn", "lake forest", "mettawa",
    "lincolnshire", "vernon hills", "libertyville", "waukegan", "highland park", "des plaines",
    "rosemont", "park ridge", "elk grove village", "schaumburg", "hoffman estates",
    "arlington heights", "palatine", "rolling meadows", "itasca", "wood dale", "bensenville",
    "oak brook", "oakbrook terrace", "lombard", "downers grove", "lisle", "naperville",
    "wheaton", "aurora", "batavia", "elgin", "joliet", "bolingbrook", "romeoville", "lemont",
    "argonne", "burr ridge", "westchester", "oak park", "cicero", "evergreen park", "franklin park",
}

# City names that also exist in other states. Without a state ("Richmond")
# we can't tell Richmond, CA from Capital One's Richmond, VA, so they only
# count when the state is given.
AMBIGUOUS_CITIES = {
    "richmond", "newark", "dublin", "belmont", "campbell", "alameda", "fremont", "hayward",
    "aurora", "wheaton", "elgin", "batavia", "lake forest", "highland park", "oak park",
    "northfield", "deerfield", "westchester", "burlingame", "union city",
}
COUNTRY_WORDS = {"us", "usa", "united", "u s", "u s a"}


def _ascii(text: str) -> str:
    """'Forté' -> 'Forte' so accents don't break matching."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def simplify_text(text: str) -> str:
    """Lowercase, '&' -> 'and', punctuation -> spaces, collapse whitespace."""
    text = _ascii(text or "").lower().replace("&", " and ")
    return _NON_ALNUM.sub(" ", text).strip()


def normalize_org(name: str) -> str:
    words = [w for w in simplify_text(name).split() if w not in ORG_SUFFIXES]
    return " ".join(words) or simplify_text(name)


def normalize_title(title: str) -> str:
    """Drop season/year tags so 'SWE Intern (Summer 2027)' == 'SWE Intern'."""
    text = _SEASON_YEAR.sub(" ", title or "")
    text = _YEAR.sub(" ", text)
    return simplify_text(text)


def city_of(location: str) -> str:
    """'Sunnyvale, California - HQ' -> 'sunnyvale'; anything remote -> 'remote'."""
    text = simplify_text(location.split(",")[0].split(" - ")[0].split("(")[0])
    if not text:
        return ""
    if "remote" in simplify_text(location):
        return "remote"
    return CITY_ALIASES.get(text, text)


def cities_of(locations: list[str]) -> list[str]:
    """Normalized, de-duplicated city list in original order."""
    seen: list[str] = []
    for loc in locations or []:
        city = city_of(loc)
        if city and city not in seen:
            seen.append(city)
    return seen


def is_remote(locations: list[str]) -> bool:
    return any("remote" in (loc or "").lower() for loc in locations or [])


def _state_of(location: str) -> str | None:
    """'Sunnyvale, CA' -> 'ca'; 'Sunnyvale, California - HQ' -> 'california';
    'Austin, TX, United States' -> 'tx'; 'Sunnyvale, United States' -> None."""
    parts = [p for p in location.split(",")[1:] if p.strip()]
    if not parts:
        return None
    first = simplify_text(parts[0].split(" - ")[0].split("(")[0])
    word = first.split()[0] if first else ""
    if not word or word in COUNTRY_WORDS or first in COUNTRY_WORDS:
        return None
    return word


def region_of(location: str) -> str | None:
    """'bay_area', 'chicagoland' or None for one location string."""
    low = simplify_text(location or "")
    if "bay area" in low or "silicon valley" in low:
        return "bay_area"
    if "chicagoland" in low or "chicago" in low.split():
        return "chicagoland"
    city, state = city_of(location or ""), _state_of(location or "")
    if state is None and city in AMBIGUOUS_CITIES:
        return None
    if city in BAY_AREA_CITIES and state in (None, "ca", "california"):
        return "bay_area"
    if city in CHICAGOLAND_CITIES and state in (None, "il", "illinois"):
        return "chicagoland"
    return None


def is_bay_area(locations: list[str]) -> bool:
    return any(region_of(loc) == "bay_area" for loc in locations or [])


def is_chicagoland(locations: list[str]) -> bool:
    return any(region_of(loc) == "chicagoland" for loc in locations or [])


def match_key(org: str, title: str) -> str:
    """Org + title only. Combined with a city-overlap check for fuzzy dedupe."""
    return f"{normalize_org(org)}|{normalize_title(title)}"


def dedupe_key(org: str, title: str, locations: list[str]) -> str:
    """Org + title + first city: the exact-match key stored as UNIQUE in the DB."""
    cities = cities_of(locations)
    return f"{match_key(org, title)}|{cities[0] if cities else 'any'}"


def display_location(locations: list[str], limit: int = 3) -> str:
    """Short human-readable location string: 'Sunnyvale, CA · New York, NY +2'."""
    locations = [loc for loc in (locations or []) if loc]
    if not locations:
        return ""
    shown = " · ".join(locations[:limit])
    more = len(locations) - limit
    return f"{shown} +{more}" if more > 0 else shown
