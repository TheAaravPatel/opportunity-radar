"""The shapes of data passed between sources, the pipeline and the database."""

from __future__ import annotations

from dataclasses import dataclass, field

# The three fields this tool cares about.
FIELDS = ("cs", "econ", "biotech")
FIELD_LABELS = {"cs": "CS / data / AI", "econ": "Econ / finance / consulting", "biotech": "Biotech / life sciences"}

# Every opportunity gets exactly one of these types.
TYPES = (
    "industry internship",
    "early-career program",
    "pipeline program",
    "research program",
    "RA",
    "fellowship",
    "grant",
    "conference",
    "hackathon",
    "travel scholarship",
)

# Status vocabulary (from the spec).
STATUSES = ("open", "opening soon", "expected soon", "closed", "possibly discontinued")

# Class years, in order. Used for eligibility ("open to freshmen?").
CLASS_YEARS = ("freshman", "sophomore", "junior", "senior")

# Kinds of deadline we track.
DEADLINE_KINDS = ("application", "priority", "opens", "event", "scholarship", "abstract", "registration")


@dataclass
class Deadline:
    kind: str          # one of DEADLINE_KINDS
    date: str          # "YYYY-MM-DD"
    label: str = ""
    approx: bool = False   # True when the date is an estimate


@dataclass
class Item:
    """One opportunity exactly as a source reports it.

    Sources only fill in what they know; the pipeline adds the rest
    (normalized keys, field classification, eligibility score).
    """

    source_item_id: str            # the source's own id for this item (stable between runs)
    title: str
    organization: str
    url: str = ""
    type: str = "industry internship"
    fields: list[str] = field(default_factory=list)        # fields the source implies
    subcategory: str = ""                                    # e.g. Simplify's "AI/ML/Data"
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    posted_at: str | None = None                             # "YYYY-MM-DD"
    terms: list[str] = field(default_factory=list)           # e.g. ["Summer 2027"]
    degrees: list[str] = field(default_factory=list)         # e.g. ["Bachelor's", "Master's"]
    class_years: list[str] | None = None                     # explicit (registries); None = detect from text
    text: str = ""                                           # extra text for keyword rules (description etc.)
    deadlines: list[Deadline] = field(default_factory=list)
    status: str | None = None                                # source-computed status (registries)
    status_reason: str = ""                                  # why that status, in plain English
    registry_id: str = ""                                    # registry entry this item comes from
    scope: str = ""                                          # sub-feed, e.g. one company in an ATS source
    extra: dict = field(default_factory=dict)                # anything else worth showing (notes, format...)


@dataclass
class FetchResult:
    """What a source returns from one fetch."""

    items: list[Item]
    upstream_updated_at: str | None = None   # when the upstream data last changed, if known
    failed_scopes: set[str] = field(default_factory=set)  # e.g. companies whose board failed
    warnings: list[str] = field(default_factory=list)
