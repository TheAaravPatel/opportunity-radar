"""Importing this package registers every source type (see base.py)."""

from radar.sources import badgeup, companies, jobseveryhour, listings_json, registries, student_opportunities, zshah  # noqa: F401
from radar.sources.base import SOURCE_TYPES, Source, build_source, register  # noqa: F401
