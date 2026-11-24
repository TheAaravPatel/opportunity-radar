# CLAUDE.md — Opportunity Radar

Guidance for Claude (and humans) working in this repo.

## What this is
A personal tool for a Stanford freshman (class of 2030; econ + CS + biotech). It collects
internships, early-career (freshman/sophomore) programs, Stanford research opportunities and
conferences, refreshes itself on GitHub Actions, and publishes:
- a static dashboard on GitHub Pages (`site/index.html`, built by `radar/dashboard/`)
- a daily email digest (Gmail SMTP, `radar/digest.py`)
- a deadlines calendar feed (`site/deadlines.ics`)

**The owner is learning to code.** Keep code readable, comment the non-obvious parts, prefer
plain Python over clever abstractions, and explain decisions in commit messages and PRs.

## Core rules (from the spec — don't break these)
- Prefer sources that already run bots or publish data (GitHub repos, public JSON APIs, feeds,
  public ATS job-board APIs) over scraping websites ourselves.
- **Never** scrape LinkedIn, Handshake, Indeed, anything behind a login, or any site whose terms
  prohibit automated access. Current exclusions: NBER (terms forbid monitoring → manual link
  only), EconJobMarket HTML (terms ban robots → only its official JSON feed, shipped disabled),
  AEA JOE, Oleeo/tal.net, Avature and Eightfold career sites (no public API → watch the program
  page instead), the login-only bsurp.stanford.edu project list.
- Keep Workday polling small (≤ 20 tenants, ≥ 3 s apart): Workday's Cloudflare blocks heavy
  pollers from GitHub Actions. SimplifyJobs is the backstop for Workday-hosted employers.
- Descriptive User-Agent, per-host rate limits, retries with backoff (all in `radar/http.py`).
- Never put personal info (email, name) in requests, the dashboard or the committed database.
  The repo and Pages site are **public**.

## Layout
```
config/            all tunables (YAML) — edit these, not Python, for routine changes
  settings.yaml    grad year, Claude model + caps, digest options, HTTP politeness
  sources.yaml     every feed: type (module), group (jobs|daily), options
  keywords.yaml    field keywords, eligibility rules, early-career words
  companies.yaml   companies polled via public ATS APIs (Phase 2+)
  early_programs.yaml / watched_pages.yaml   hand-curated registries
radar/
  cli.py           `python -m radar fetch|dashboard|digest|verify`
  pipeline.py      fetch → prepare (classify, score) → store → close missing → health
  store.py         DB writes: dedupe, sightings, deadlines, safe closing
  db.py            SQLite schema (one file, `CREATE TABLE IF NOT EXISTS`)
  sources/         one module per source type, registered with @register("type")
                   ats_*.py = one job-board API each; companies.py loops companies.yaml
  ats_discovery.py guesses Greenhouse/Lever/Ashby board names for `ats: unknown` companies
  classify.py, eligibility.py, early_detect.py, normalize.py, timeutil.py
  dashboard/       build.py + templates/index.html.j2 + static/app.js, app.css
  digest.py + templates/digest.*.j2
  verify.py        live check of every source + registry URL (no DB writes)
tests/             pytest; fixtures are real samples captured from sources
docs/SOURCES.md    source verification report (what exists, formats, caveats)
scripts/           load-db.sh / save-db.sh (database ↔ `data` branch)
.github/workflows/ jobs.yml (6-hourly), daily.yml (pages + digest), verify.yml, ci.yml
```

## Commands
```bash
pip install -r requirements.txt
python -m pytest                                   # all tests (no network)
python -m radar fetch                              # all enabled sources -> data/radar.db
python -m radar fetch --group jobs                 # or: --source simplify
python -m radar dashboard                          # -> site/index.html
python -m radar digest --dry-run                   # -> out/digest.html (no email sent)
python -m radar verify                             # live report of every source + URL
```
Use `RADAR_NOW=2027-01-05T15:00:00Z` to pretend it's another date (tests do this).

## Key design decisions
- **Dedupe**: sighting (same source + id) → exact key `org|title|city` → fuzzy `org|title` with
  overlapping cities, never merging two items *from the same source*. The first source to
  create a row (`primary_source`) owns its display fields.
- **Safe closing**: an item closes only after a *successful* fetch stops listing it; if a fetch
  returns < 50 % of last run's count (and last run had ≥ 20), nothing is closed and a warning
  is shown. Multi-company sources report `failed_scopes` so one dead board doesn't close the
  others' items. Closing uses run ids (`last_run_id`), not timestamps.
- **Baseline**: rows created by a source's *first* successful run aren't "new" (dashboard +
  digest), so adding a source doesn't flood the email.
- **Eligibility** is relative to `profile.grad_year`; "rising sophomores" = current freshmen;
  "Junior/Senior" only count as class years when plural or followed by year/standing/students.
  Registry `class_years` are authoritative.
- **Registry statuses** (`radar/sources/registries.py`): possibly discontinued / opening soon /
  open / expected soon (2 weeks before `typical_open_month` through its end) / closed.
- **Digest triggers**: new items, a 14/7/2-day reminder for something you're eligible for, an
  early program opening/closing this week (first time only), a "usually opens in 2 weeks"
  warning, a changed page, or a *newly* failing source.
- **Database persistence**: `data/radar.db` is gitignored on code branches; workflows load it
  from the `data` branch and force-push a single fresh commit back (bounded repo size). The
  last 14 days of DBs are workflow artifacts. All data-writing workflows share the
  `radar-data` concurrency group.
- **Claude API** (later phases): model from `settings.yaml` (`claude-opus-5-5`), only for
  items keyword rules can't place, cached in `llm_cache`, hard per-run caps, everything
  no-ops without `ANTHROPIC_API_KEY`.

## Adding things
- **A source**: write `radar/sources/<name>.py` with `@register("<type>")` + `fetch() ->
  FetchResult`, import it in `radar/sources/__init__.py`, add an entry in `config/sources.yaml`,
  add a test with a captured fixture. The pipeline handles the rest.
- **A company / early program / Stanford page**: edit the YAML (format documented at the top of
  each file). `tests/test_config.py` validates them.

## Testing notes
- The development container's network only reaches GitHub (git + raw.githubusercontent.com),
  PyPI and the Anthropic API. Other hosts are tested via fixtures and confirmed live by running
  the **Verify sources** workflow on GitHub Actions.
- Screenshot the dashboard at phone + desktop width after UI changes (Playwright is available).

## Build phases (status)
1. ✅ SimplifyJobs end to end + zshah101 + Jobs Every Hour; registries' known deadlines
2. ✅ Biotech repo + Greenhouse/Lever/Ashby/Workday fetchers (`ats_companies` source, `skip_if_covered_by`, board discovery in verify)
3. ⏳ Econ: PREDOC, Fed (Workday), USAJobs, econ RSS, EconJobMarket (disabled), consulting ATS
4. ⏳ Page-watcher (snapshot/diff/Playwright, deadline extraction) for Stanford pages
5. ⏳ Early-programs registry watching + keyword detection + bank/trading ATS (incl. Oracle)
6. ⏳ Conferences & hackathons
7. ⏳ Weekly discovery (Claude + web search) + approval flow
