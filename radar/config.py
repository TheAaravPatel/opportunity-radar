"""Loads the YAML files in config/ so the rest of the code can use them.

Everything you might want to change lives in config/*.yaml, not in Python code:
  settings.yaml        global flags (Claude model, digest options, your grad year)
  sources.yaml         which feeds to read and how often
  keywords.yaml        field / eligibility / early-career keyword rules
  companies.yaml       companies polled through their public job-board APIs
  early_programs.yaml  freshman/sophomore program registry
  watched_pages.yaml   Stanford pages to watch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_DB_PATH = ROOT / "data" / "radar.db"


def load_yaml(path: Path) -> Any:
    """Read one YAML file; a missing file counts as empty."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass
class Config:
    """All configuration, loaded once at startup."""

    settings: dict
    sources: dict
    keywords: dict
    companies: list = field(default_factory=list)
    config_dir: Path = CONFIG_DIR

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Look up a nested setting, e.g. cfg.get("llm.model")."""
        node: Any = self.settings
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def load_file(self, name: str) -> Any:
        """Load another YAML file from the config directory (e.g. a registry)."""
        return load_yaml(self.config_dir / name)

    @property
    def grad_year(self) -> int:
        return int(self.get("profile.grad_year", 2030))


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = Path(config_dir) if config_dir else CONFIG_DIR
    return Config(
        settings=load_yaml(config_dir / "settings.yaml"),
        sources=(load_yaml(config_dir / "sources.yaml") or {}).get("sources", {}) or {},
        keywords=load_yaml(config_dir / "keywords.yaml"),
        companies=(load_yaml(config_dir / "companies.yaml") or {}).get("companies", []) or [],
        config_dir=config_dir,
    )
