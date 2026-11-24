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
  early_programs.yaml / watched_pages.yaml / conferences.yaml   hand-curated registries
radar/
  cli.py           `python -m radar fetch|dashboard|digest|verify`
  pipeline.py      fetch → prepare (classify, score) → store → close missing → health
  store.py         DB writes: dedupe, sightings, deadlines, safe closing
  db.py            SQLite schema (one file, `CREATE TABLE IF NOT EXISTS`)
  sources/         one module per source type, registered with @register("type")
                   ats_*.py = one job-board API each; companies.py loops companies.yaml
                   rss_feed.py = generic RSS/Atom reader (config-only blog sources)
  ats_discovery.py guesses Greenhouse/Lever/Ashby board names for `ats: unknown` companies
  classify.py, eligibility.py, early_detect.py, normalize.py, timeutil.py
  early_match.py   early-career postings -> registry programs (+ keyword-detection suggestions)
  dashboard/       build.py + templates/index.html.j2 + static/app.js, app.css
  digest.py + templates/digest.*.j2
  watcher/         page-watcher: fetch.py (robots.txt, http, Playwright fallback), text.py,
                   snapshot.py (pages/page_snapshots + diffs), deadlines.py (regex + dateparser)
  llm.py           Claude wrapper: llm_cache, per-run caps, JSON-schema output, refusal fallback
  verify.py        live check of every source + registry URL (no DB writes)
  probe.py         outline any public URL's HTML/JSON/feed (Verify workflow `probe_urls` input)
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
- **Conferences**: dedupe key `conf|<name without year/parentheses>|<year or tba>`; a "tba"
  entry matches the same name with any year, and registry `aka` names are stored in
  `conference_aliases`. The registry runs first so it owns the display fields. Event start dates
  are deadlines of kind `event` (calendar only; never reminders). PhD-only venues and trade shows
  get `extra.hidden_reason`; far-away hackathons are hidden on the dashboard by default.
- **Travel rule** (`radar/travel.py`, settings `travel:`): every item gets a travel fit -
  bay_area / chicagoland / remote / covered / partial / outside / no_location. The dashboard's
  default "Where" filter and the digest only show bay_area, chicagoland, remote, covered and
  no_location (partial too if `allow_partial`). Regions check the state ("Richmond, VA" isn't
  the Bay Area); "covered" needs a registry `travel_covered: full` or description phrases for
  BOTH housing and travel (`keywords.yaml -> travel`, negations ignored, titles never used).
- **Secrets**: a source with `requires_env: [NAME]` is skipped (health "needs secret", nothing
  closed, not counted as failing) until the env var/GitHub Secret exists. Verify reports the same.
- **Econ job boards**: `title_org_map` names the real employer on shared boards (12 Reserve Banks,
  one Workday board), `location_map` fixes non-city locations, `skip_titles` drops off-field roles.
  Full-time post-BA RA roles are deliberately off (no `extra_titles`, predoc_fulltime disabled)
  until senior year; the post-bacc eligibility rule demotes any that slip in.
- **Page-watcher** (`page_watcher` source, daily): checks `watched_pages.yaml` `pages` (Stanford)
  and `job_pages` (employers with no API). It gets the DB as `self.conn` (None in Verify) to keep
  snapshots; the first check is a silent baseline; `only_lines_matching`/`ignore_lines` decide
  which changes count. Its Stanford items reuse the registry entry's name/org/"Stanford, CA" so
  they merge into the registry card; found deadlines (method `regex`/`llm`, `evidence` = the
  sentence) are scoped per page, so a page that fails keeps its dates. A date only counts right
  after deadline words, not for offers/reports/notifications; year-less dates need the year on
  the page (approx). Claude is asked only when a page mentions deadlines but no date matched
  (cached by text, `max_extract_calls_per_run`). After every run `refresh_registry_statuses`
  recomputes registry statuses from all active deadlines.
- **Early programs** (Phase 5): `early_detect` flags titles (strong words alone; weak words only
  right before a program word; never "experienced/MBA/PhD"). After every run
  `link_early_postings` matches open early postings to programs by firm (`match_orgs`, prefix
  "Citi"~"Citigroup") + distinctive name/aka word or `match_titles` (stored in `early_programs`);
  unmatched program-like postings become `suggestions` (expire when the posting closes).
  `refresh_registry_statuses` then applies clues in order - known dates win; a live posting
  (matched, or a feed sighting merged into the card) -> open; the page says "applications are
  open" -> open (never over a hand-set "possibly discontinued"); page 404 for 7 days -> possibly
  discontinued - and records status changes for the digest's "Now open" (first run = baseline).
  `early_career_only` boards keep only early titles or *strict* registry matches. Oracle fetcher
  in `ats_oracle.py` (JPMC site CX_1; Goldman's Oracle site has no campus jobs -> disabled).
- **Schema changes**: add new columns to `ADDED_COLUMNS` in db.py so the database already on
  the data branch is upgraded in place.
- **Claude API** (later phases): model from `settings.yaml` (`claude-opus-5-5`), only for
  items keyword rules can't place, cached in `llm_cache`, hard per-run caps, everything
  no-ops without `ANTHROPIC_API_KEY`.

## Adding things
- **A source**: write `radar/sources/<name>.py` with `@register("<type>")` + `fetch() ->
  FetchResult`, import it in `radar/sources/__init__.py`, add an entry in `config/sources.yaml`,
  add a test with a captured fixture. The pipeline handles the rest.
- **An RSS/Atom feed**: config only - an `rss_feed` entry in `sources.yaml` (title filters,
  `org_fallback`, `max_age_days`, `keep_missing_days`).
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
3. ✅ Econ: PREDOC RA page, Fed Board + regional Fed (Workday), USAJobs (needs secrets), econ RSS
   blogs (Econ UG Blog, Economic Misfit), EconJobMarket (disabled), Brattle/CRA/Cornerstone/RAND +
   Workday consulting boards; no-API employers are manual links until the page-watcher
4. ✅ Page-watcher: 22 Stanford pages + 3 employer job pages (Analysis Group, Brookings, Bates White);
   snapshot/diff, Playwright fallback, robots.txt, regex+dateparser deadlines, Claude fallback
5. ✅ Early programs: page-watching of 42 program pages (open/closed wording, gone pages), posting ->
   program matching + suggestions, `early_career_companies` boards (Workday, Greenhouse, Ashby, Oracle)
6. ✅ Conferences & hackathons (built before 3-5 at the owner's request): conferences.yaml (41), student-opportunities, BadgeUp; name+year dedupe with TBA matching and `aka` aliases
7. ⏳ Weekly discovery (Claude + web search) + approval flow
