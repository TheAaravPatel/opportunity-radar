"""Importing this package registers every source type (see base.py)."""

from radar.sources import (  # noqa: F401
    badgeup, companies, econjobmarket, jobseveryhour, listings_json, page_watcher, predoc, registries, rss_feed,
    student_opportunities, usajobs, zshah,
)
from radar.sources.base import SOURCE_TYPES, Source, build_source, register  # noqa: F401
