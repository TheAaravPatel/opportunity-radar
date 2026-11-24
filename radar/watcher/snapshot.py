"""Remembering each page's text and spotting meaningful changes.

State lives in two tables (radar/db.py):
  pages           one row per watched page: last check, last change, hash...
  page_snapshots  the page's text (zlib-compressed) each time it changed,
                  with a readable diff. Only the last KEEP_SNAPSHOTS are kept.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import zlib

KEEP_SNAPSHOTS = 10
MAX_DIFF_LINES = 40


def content_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def compress(lines: list[str]) -> bytes:
    return zlib.compress("\n".join(lines).encode("utf-8"))


def decompress(blob: bytes | None) -> list[str]:
    return zlib.decompress(blob).decode("utf-8").split("\n") if blob else []


def line_diff(old: list[str], new: list[str], only: str | None = None, ignore: str | None = None) -> list[str]:
    """Readable changes: "+ added line" / "- removed line".

    `only`   regex: only changed lines matching it count (job pages: "intern|summer")
    `ignore` regex: changed lines matching it never count ("Updated on ...")
    Lines that merely moved (removed in one place, added in another) are skipped.
    """
    added, removed = [], []
    for line in difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        (added if line.startswith("+") else removed).append(line[1:])
    moved = set(added) & set(removed)
    only_re = re.compile(only, re.I) if only else None
    ignore_re = re.compile(ignore, re.I) if ignore else None

    def counts(line: str) -> bool:
        if line in moved or (ignore_re and ignore_re.search(line)):
            return False
        return not only_re or bool(only_re.search(line))

    changes = [f"- {line[:240]}" for line in removed if counts(line)] + [f"+ {line[:240]}" for line in added if counts(line)]
    if len(changes) > MAX_DIFF_LINES:
        changes = changes[:MAX_DIFF_LINES] + [f"... and {len(changes) - MAX_DIFF_LINES} more changed lines"]
    return changes


def load_page(conn, page_id: str):
    return conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()


def last_text(conn, page_id: str) -> list[str]:
    row = conn.execute(
        "SELECT text_z FROM page_snapshots WHERE page_id = ? ORDER BY id DESC LIMIT 1", (page_id,)
    ).fetchone()
    return decompress(row["text_z"]) if row else []


def save_page_info(conn, page_id: str, entry: dict, kind: str) -> None:
    """Create/refresh the page's row with what the YAML says about it."""
    conn.execute(
        """INSERT INTO pages (id, kind, url, render, name, org, fields, link)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, url=excluded.url, render=excluded.render,
               name=excluded.name, org=excluded.org, fields=excluded.fields, link=excluded.link""",
        (page_id, kind, entry["url"], entry.get("render", "auto"), entry.get("name", page_id),
         entry.get("org", ""), ",".join(entry.get("fields") or []), entry.get("link") or entry["url"]),
    )


def record_check(conn, page_id: str, lines: list[str], now: str, status: int, method: str,
                 only: str | None = None, ignore: str | None = None) -> list[str] | None:
    """Store this check. Returns the diff if the page changed meaningfully,
    [] if it didn't, and None the first time (that snapshot is the baseline)."""
    page = load_page(conn, page_id)
    new_hash = content_hash(lines)
    previous_hash = page["content_hash"] if page else None
    diff: list[str] | None
    if previous_hash is None:
        diff = None
    elif previous_hash == new_hash:
        diff = []
    else:
        diff = line_diff(last_text(conn, page_id), lines, only, ignore)

    if previous_hash != new_hash:
        # Save the new text even for unimportant changes, so the next diff is
        # against what the page says now.
        conn.execute(
            "INSERT INTO page_snapshots (page_id, fetched_at, content_hash, text_z, diff, changed) VALUES (?, ?, ?, ?, ?, ?)",
            (page_id, now, new_hash, compress(lines), "\n".join(diff or []) or None, int(bool(diff))),
        )
        conn.execute(
            """DELETE FROM page_snapshots WHERE page_id = ? AND id NOT IN
                   (SELECT id FROM page_snapshots WHERE page_id = ? ORDER BY id DESC LIMIT ?)""",
            (page_id, page_id, KEEP_SNAPSHOTS),
        )
    conn.execute(
        """UPDATE pages SET last_checked_at=?, last_status=?, content_hash=?, consecutive_failures=0,
               last_error=NULL, fetch_method=?, text_chars=?,
               last_changed_at=CASE WHEN ? THEN ? ELSE last_changed_at END
           WHERE id=?""",
        (now, status, new_hash, method, sum(len(line) for line in lines), int(bool(diff)), now, page_id),
    )
    return diff


def record_failure(conn, page_id: str, now: str, error: str) -> None:
    conn.execute(
        """UPDATE pages SET last_checked_at=?, last_error=?, consecutive_failures=consecutive_failures + 1
           WHERE id=?""",
        (now, error[:500], page_id),
    )
