"""The page-watcher: checks web pages that have no feed or API.

Used for Stanford program pages (config/watched_pages.yaml -> pages) and
employer job-search pages with no public job API (-> job_pages). Once a day:

  fetch.py      download the page politely: robots.txt check, then a plain
                request; a headless browser (Playwright) only if the page
                needs JavaScript
  text.py       turn the HTML into clean lines of visible text
  snapshot.py   compare with the last saved text; save a readable diff when
                something changed (pages + page_snapshots tables)
  deadlines.py  find deadline / "applications open" dates in the text
                (regex + dateparser; Claude only for pages that defeat it)

The pipeline runs it as the `page_watcher` source (radar/sources/page_watcher.py).
"""
