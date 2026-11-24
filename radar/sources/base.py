"""The interface every source implements, plus a registry of source types.

To add a new kind of source:
  1. create radar/sources/my_source.py with a class like

        @register("my_source")
        class MySource(Source):
            def fetch(self) -> FetchResult:
                data = self.http.get_json(self.options["url"])
                return FetchResult(items=[Item(...) for row in data])

  2. import it in radar/sources/__init__.py
  3. add an entry to config/sources.yaml with `type: my_source`

The pipeline does everything else (dedupe, classification, eligibility,
closing items that disappear, health tracking).
"""

from __future__ import annotations

from radar.config import Config
from radar.http import Http
from radar.models import FetchResult

SOURCE_TYPES: dict[str, type["Source"]] = {}


def register(type_name: str):
    """Class decorator: makes a source type usable from sources.yaml."""

    def decorator(cls):
        cls.type_name = type_name
        SOURCE_TYPES[type_name] = cls
        return cls

    return decorator


class Source:
    type_name = "base"

    def __init__(self, source_id: str, options: dict, cfg: Config, http: Http):
        self.id = source_id
        self.options = options or {}
        self.cfg = cfg
        self.http = http

    @property
    def name(self) -> str:
        return self.options.get("name", self.id)

    @property
    def homepage(self) -> str:
        return self.options.get("homepage", self.options.get("url", ""))

    def fetch(self) -> FetchResult:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def get_json_with_fallback(self) -> tuple[object, str]:
        """GET options["url"]; if that fails, try options["fallback_url"].

        Returns (parsed JSON, the URL that worked). Raises the first error if
        every URL fails.
        """
        urls = [u for u in (self.options.get("url"), self.options.get("fallback_url")) if u]
        first_error: Exception | None = None
        for url in urls:
            try:
                return self.http.get_json(url), url
            except Exception as exc:  # noqa: BLE001 - try the next URL
                first_error = first_error or exc
        raise first_error or ValueError(f"source {self.id} has no url")


def build_source(source_id: str, options: dict, cfg: Config, http: Http) -> Source:
    type_name = (options or {}).get("type")
    if type_name not in SOURCE_TYPES:
        known = ", ".join(sorted(SOURCE_TYPES))
        raise ValueError(f"source '{source_id}' has unknown type '{type_name}' (known: {known})")
    return SOURCE_TYPES[type_name](source_id, options, cfg, http)
