"""Shared test helpers.

Tests never touch the real network: `fake_http` builds an Http client whose
transport answers from a dict of {url: response}. They also pin "now" with the
RADAR_NOW environment variable so date logic is predictable.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from radar import db
from radar.config import load_config
from radar.http import Http

FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = "2026-11-24T18:00:00Z"   # 10am Pacific, Tuesday Nov 24 2026


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setenv("RADAR_NOW", FIXED_NOW)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def make_http(routes: dict, calls: list | None = None) -> Http:
    """routes: url -> (status, body) or a callable(request) -> httpx.Response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, text="not found")
        if callable(route):
            return route(request)
        status, body = route
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    return Http("test-agent", transport=httpx.MockTransport(handler), sleep=lambda s: None, min_delay=0)


@pytest.fixture
def fake_http():
    return make_http
