"""Approving or rejecting a suggestion (Suggested tab -> GitHub issue -> here).

How it works from your phone:
  1. On the dashboard's Suggested tab, tap Approve (or Reject). That opens a
     new GitHub issue titled "Approve suggestion #42".
  2. The approve workflow (.github/workflows/approve.yml) runs, but only for
     issues YOU opened - the repo is public, so anyone can open issues.
  3. It runs `python -m radar decide "<issue title>"`: the title only gives
     the action and the number. What gets added is read from OUR database
     (the suggestion's draft entry), never from the issue text.
  4. Approve -> the entry is appended to the right config file, keeping all
     its comments; the workflow commits it and closes the issue with a reply.
     Reject -> the suggestion is marked rejected and never suggested again.

Locally: `python -m radar approve 42` / `python -m radar reject 42`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from radar.config import ROOT
from radar.models import CLASS_YEARS, DEADLINE_KINDS, FIELDS, TYPES
from radar.timeutil import now_iso, today_local

# config file -> the top-level key whose list (or, for sources, mapping) gets the entry
LIST_KEYS = {
    "config/early_programs.yaml": "programs",
    "config/watched_pages.yaml": "pages",
    "config/conferences.yaml": "conferences",
    "config/companies.yaml": "companies",
    "config/sources.yaml": "sources",
}
TITLE = re.compile(r"^\s*(approve|reject)\s+suggestion\s+#?(\d+)\b", re.I)
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class DecisionError(ValueError):
    pass


def parse_title(title: str) -> tuple[str, int]:
    match = TITLE.match(title or "")
    if not match:
        raise DecisionError(f"not a suggestion decision: {title[:80]!r} (expected 'Approve suggestion #42')")
    return match.group(1).lower(), int(match.group(2))


# ------------------------------------------------------------ YAML editing
def _render(entry, key: str) -> str:
    """The entry as YAML text, indented to sit under `key:`."""
    if key == "sources":                                  # a mapping: {source_id: {...}}
        text = yaml.safe_dump(entry, sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)
    else:                                                 # a list item
        text = yaml.safe_dump([entry], sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)
    return "".join(f"  {line}" if line.strip() else line for line in text.splitlines(keepends=True))


def insert_entry(path: Path, key: str, entry, comment: str) -> None:
    """Append `entry` at the end of the `key:` block - not simply the end of the
    file, because watched_pages.yaml has another list (job_pages) after pages.
    Text is inserted (not re-dumped), so every existing comment stays."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(key)}:\s*(#.*)?$", line)), None)
    if start is None:
        raise DecisionError(f"{path.name} has no top-level '{key}:'")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_]", lines[i])), len(lines))
    # Comment/blank lines just before the next top-level key belong to that next section.
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    if end and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    block = f"\n  # {comment}\n" + _render(entry, key)
    updated = "".join(lines[:end]) + block + "".join(lines[end:])

    # Check before saving: the file must still load, with the new entry in it.
    data = yaml.safe_load(updated)
    if key == "sources":
        assert all(k in data[key] for k in entry), "entry missing after insert"
    elif entry not in data[key]:
        raise DecisionError("the entry did not end up in the list - nothing changed")
    path.write_text(updated, encoding="utf-8")


def validate(entry, key: str, existing) -> None:
    """The same rules tests/test_config.py enforces, so an approval can't break CI."""
    if key == "sources":
        (source_id, body), = entry.items()
        if source_id in existing:
            raise DecisionError(f"source id {source_id!r} already exists")
        if not str(body.get("url", "")).startswith("https://"):
            raise DecisionError("feed URL must start with https://")
        return
    if key == "companies":
        if any(c.get("name", "").lower() == entry["name"].lower() for c in existing):
            raise DecisionError(f"{entry['name']} is already in companies.yaml")
        return
    if not SLUG.match(entry.get("id", "")):
        raise DecisionError(f"bad id {entry.get('id')!r}")
    if not str(entry.get("url", "")).startswith("https://"):
        raise DecisionError("url must start with https://")
    if not set(entry.get("fields") or []) <= set(FIELDS) or not set(entry.get("class_years") or []) <= set(CLASS_YEARS):
        raise DecisionError("unknown field or class year")
    if entry.get("type") and entry["type"] not in TYPES:
        raise DecisionError(f"unknown type {entry['type']!r}")
    if any(d.get("kind") not in DEADLINE_KINDS for d in entry.get("deadlines") or []):
        raise DecisionError("unknown deadline kind")


def _unique_id(entry: dict, existing: list) -> dict:
    taken = {e.get("id") for e in existing}
    base, n = entry["id"], 2
    while entry["id"] in taken:
        entry = {**entry, "id": f"{base}-{n}"}
        n += 1
    return entry


# --------------------------------------------------------------- decisions
def decide(conn, action: str, suggestion_id: int, root: Path = ROOT) -> str:
    """Approve or reject one suggestion. Returns a short message (the issue reply)."""
    row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not row:
        raise DecisionError(f"there is no suggestion #{suggestion_id}")
    if row["status"] not in ("pending", "expired"):
        return f"Suggestion #{suggestion_id} ({row['name']}) was already {row['status']} - nothing changed."

    if action == "reject":
        conn.execute("UPDATE suggestions SET status = 'rejected', decided_at = ? WHERE id = ?", (now_iso(), suggestion_id))
        conn.commit()
        return f"Rejected suggestion #{suggestion_id}: {row['name']}. It won't be suggested again."

    payload = json.loads(row["payload"]) if row["payload"] else None
    target = row["target_config"]
    if not payload or target not in LIST_KEYS:
        conn.execute("UPDATE suggestions SET status = 'approved', decided_at = ? WHERE id = ?", (now_iso(), suggestion_id))
        conn.commit()
        return (f"Marked suggestion #{suggestion_id} ({row['name']}) as approved, but it needs code to add "
                f"(it's not a config entry), so nothing was changed. Link: {row['url']}")

    key = LIST_KEYS[target]
    path = root / target
    existing = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(key) or ([] if key != "sources" else {})
    entry = payload if key in ("sources", "companies") else _unique_id(payload, existing)
    if target.endswith("early_programs.yaml"):
        entry.setdefault("last_verified", f"{today_local().isoformat()} approved from suggestion #{suggestion_id}")
    validate(entry, key, existing)
    insert_entry(path, key, entry, f"Added {today_local().isoformat()} from suggestion #{suggestion_id} ({row['source']})")
    conn.execute("UPDATE suggestions SET status = 'approved', decided_at = ? WHERE id = ?", (now_iso(), suggestion_id))
    conn.commit()
    what = entry.get("id") or entry.get("name") or next(iter(entry))
    return (f"Approved suggestion #{suggestion_id}: {row['name']}.\n\nAdded `{what}` to `{target}`. "
            f"It's picked up by the next scheduled run; edit the entry there to fill in anything missing.")
