# Opportunity Radar

[![Tests](https://github.com/TheAaravPatel/opportunity-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/TheAaravPatel/opportunity-radar/actions/workflows/ci.yml) · **[Open the dashboard](https://theaaravpatel.github.io/opportunity-radar/)**

Collects the most up-to-date **internships, early-career (freshman/sophomore) programs, Stanford
research opportunities, and conferences** in economics, computer science and biotech; refreshes
itself on GitHub Actions; and shows what's **new** and what's **due soon**:

- **Dashboard** (GitHub Pages): `https://theaaravpatel.github.io/opportunity-radar/`. Tabs for
  Internships (CS / Biotech / Econ / Early Programs), Stanford, Conferences & Hackathons,
  Deadlines (calendar), Suggested sources, and Source health. Works on a phone.
- **Daily email digest** each morning, Pacific time, sent only when something is new or due.
- **Calendar feed** of every deadline: add `…/deadlines.ics` to Google Calendar ("From URL").

It reads from sources that already run bots or publish data (GitHub repos, public JSON APIs,
feeds, public job-board APIs) and never scrapes LinkedIn/Handshake/Indeed, login-only pages, or
sites whose terms forbid it. See [docs/SOURCES.md](docs/SOURCES.md) for what each source is,
what format it uses, and its caveats.

## How it runs

| Workflow | When | What it does |
|---|---|---|
| **Job feeds** (`jobs.yml`) | every 6 hours | reads the job/internship feeds, rebuilds the dashboard |
| **Daily pages + digest** (`daily.yml`) | ~6:47 am Pacific | registries + watched pages, dashboard, email digest |
| **Weekly discovery** (`discovery.yml`) | Sundays ~9:23 am Pacific | Claude searches the web (max 15 searches) for programs, pages, conferences and sources we don't track yet → Suggested tab |
| **Approve or reject a suggestion** (`approve.yml`) | when you open an "Approve/Reject suggestion #N" issue | adds the approved entry to the config (or hides a rejected one), replies, closes the issue |
| **Verify sources** (`verify.yml`) | when you click "Run workflow" | checks every source + URL once and writes a report |
| **Tests** (`ci.yml`) | every push | runs `pytest` |

The SQLite database (`radar.db`) lives on the **`data` branch**. Each run downloads it, updates
it, and pushes it back as a single fresh commit, so history (first-seen dates, closed items)
survives between runs without the repo growing forever. Backups of the last 14 days are kept as
workflow artifacts.

## One-time setup

1. **Turn on GitHub Pages**: repo **Settings → Pages → Build and deployment → Source:
   "GitHub Actions"**.
2. **Add secrets**: **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | What it is | Needed for |
   |---|---|---|
   | `GMAIL_ADDRESS` | the Gmail address that sends the digest | email digest |
   | `GMAIL_APP_PASSWORD` | a Gmail **app password**, not your normal password: Google Account → Security → 2-Step Verification (must be on) → App passwords → create one named "Opportunity Radar" | email digest |
   | `DIGEST_TO` | where to send it (optional; defaults to `GMAIL_ADDRESS`) | email digest |
   | `USAJOBS_API_KEY` | free key from <https://developer.usajobs.gov/apirequest/> | federal student (Pathways) econ/statistics roles; until both are set the source shows "needs secret" |
   | `USAJOBS_EMAIL` | the email you registered the USAJobs key with (USAJobs requires it as the User-Agent) | USAJobs |
   | `ANTHROPIC_API_KEY` | from <https://console.anthropic.com/> | optional: weekly discovery (about 15 web searches + 2 Claude requests a week) and Claude reading deadlines on pages the date rules can't (max 5 small requests a day, cached) |

   Anything missing just turns that feature off. Nothing else breaks.
3. **Run it once**: **Actions → Job feeds (every 6 hours) → Run workflow**, then
   **Daily pages + digest → Run workflow**. The first digest is a short "welcome" email.

## Run it on your own computer

```bash
pip install -r requirements.txt
python -m radar fetch              # update data/radar.db from every enabled source
python -m radar dashboard          # build site/index.html - open it in a browser
python -m radar digest --dry-run   # write out/digest.html instead of emailing
python -m pytest                   # run the tests
```

Useful options: `python -m radar fetch --source simplify` (one source),
`python -m radar verify` (live check of every source and URL), `-v` for detailed logs.

## How to…

### Add a source
Each source is one Python module plus one config entry.
1. Create `radar/sources/my_source.py`:
   ```python
   from radar.models import FetchResult, Item
   from radar.sources.base import Source, register

   @register("my_source")
   class MySource(Source):
       def fetch(self) -> FetchResult:
           data = self.http.get_json(self.options["url"])     # polite, retrying HTTP client
           items = [Item(source_item_id=row["id"], title=row["title"],
                         organization=row["company"], url=row["url"], fields=["cs"])
                    for row in data["jobs"]]
           return FetchResult(items=items)
   ```
2. Import it in `radar/sources/__init__.py`.
3. Add it to `config/sources.yaml`:
   ```yaml
     my_source:
       type: my_source
       group: jobs          # jobs = every 6 hours, daily = each morning
       name: My new source
       homepage: https://example.com
       url: https://example.com/jobs.json
   ```
4. Add a test in `tests/` using a saved sample of the real response.

Need a GitHub Secret (an API key)? Add `requires_env: [MY_API_KEY]` to the entry: until the
secret exists the source shows "needs secret" instead of failing.

The pipeline does the rest: deduplication, field classification, eligibility scoring, marking
items closed when they disappear, and source health.

### Add a company (public job-board API)
Add an entry to `config/companies.yaml`. Find the ATS from the company's careers page links:

| Careers link looks like | Entry |
|---|---|
| `boards.greenhouse.io/acme` or `job-boards.greenhouse.io/acme` | `{name: Acme, ats: greenhouse, slug: acme, group: biotech, fields: [biotech]}` |
| `jobs.lever.co/acme` | `ats: lever, slug: acme` |
| `jobs.ashbyhq.com/acme` | `ats: ashby, slug: acme` |
| `acme.wd5.myworkdayjobs.com/AcmeCareers` | `ats: workday, tenant: acme, host: wd5, site: AcmeCareers` |

Optional per-company settings (all explained at the top of the file): `search` (Workday search
words), `skip_titles` (drop roles outside your fields, like the Fed Board's photography intern),
`location_map` (turn a board's "Federal Reserve Board" into "Washington, DC"), `title_org_map`
(name the real employer on a shared board - all 12 Federal Reserve Banks use one), and
`extra_titles` (also keep e.g. "Research Assistant" roles; off for now because those are
full-time jobs that need a degree).

Biotech companies already covered by the biotech GitHub repo don't need an entry (a test checks,
and the fetcher re-checks that repo's `companies.json` every run). Keep the Workday list short (see
the warning at the top of the file). Don't know the ATS? Add it with `ats: unknown`: the
**Verify sources** report then tries likely Greenhouse/Lever/Ashby board names and lists the hits.

### Add a blog or news feed (no code)
Any site with an RSS or Atom feed (WordPress sites have one at `/feed/`) needs only a config
entry, using the generic `rss_feed` type:
```yaml
  my_econ_blog:
    type: rss_feed
    group: daily
    name: My econ blog
    homepage: https://myeconblog.org/
    url: https://myeconblog.org/feed/
    fields: [econ]
    title_must_match: "intern|research assistant|fellowship|program"   # keep only these posts
    title_must_not_match: "postdoc|faculty"                            # ...and never these
    max_age_days: 60
    keep_missing_days: 60      # feeds only show their newest posts
```
"Apply by <date>" / "Deadline: <date>" and "Location: ..." lines in a post become the deadline
and location. Run **Verify sources** with the source id to check the feed works from GitHub.

### Add an early-career program
Add an entry to `config/early_programs.yaml` (every field is explained at the top of that file):
```yaml
  - id: acme-sophomore-insight
    firm: Acme
    program: Sophomore Insight Day
    fields: [econ]
    class_years: [sophomore]          # your CURRENT year when applying
    format: insight day
    url: https://acme.com/students/insight
    typical_open_month: 9             # drives "expected soon" + the 2-week warning
    status_hint: active
    deadlines:
      - {kind: application, date: 2027-10-01, label: Application deadline}
    match_orgs: [Acme Corp]           # optional: other names job feeds use for the firm
```
Never delete an old program: set `status_hint: possibly discontinued` instead.

What happens to it automatically:
- **Its page is watched daily** (like Stanford pages): new dates on the page are added, the
  wording "Applications are now open" marks it open, and a page that has been gone (HTTP 404)
  for a week marks it "possibly discontinued".
- **Job postings are matched to it.** When a feed (SimplifyJobs, a bank's or trading firm's job
  board) posts an early-career job at the same firm whose title shares a distinctive word with
  the program's name or `aka` ("Sophomore Insight Day 2027"), the card shows "Live posting" with
  the link, the status becomes open, and the email says "Now open". If a program's postings are
  titled differently, add `match_titles: ["phrase"]`.
- **Unknown programs are suggested.** An early-career posting that looks like a program (insight
  day, summit, discovery program...) at a firm that isn't in the registry appears on the
  **Suggested sources** tab with a draft entry you can paste into this file.

### Add a Stanford page to watch
Add an entry to `config/watched_pages.yaml` under `pages:` with `id`, `name`, `org`, `url`,
`fields`, `type`, and any known `deadlines` (all fields are explained at the top of the file).
Every morning the **page-watcher** then:
- loads the page (it respects robots.txt; pages built by JavaScript are opened in a headless
  browser automatically, or set `render: playwright`)
- compares its text with yesterday's and, if it changed, shows what changed on the Stanford tab
  and in the email (`+` lines are new, `-` lines were removed)
- reads deadlines off the page ("Applications are due February 1, 2027", "Application will
  open December 16") and adds them to the program's card, the calendar and the 14/7/2-day
  reminders. Hover a deadline on the dashboard to see the sentence it came from. Dates you write
  in `deadlines:` yourself are always kept.

If a page changes for unimportant reasons (a news box, a date stamp), add `selector: main` (or a
narrower CSS selector) or `ignore_lines: "^Updated"`. If a page blocks bots or needs a login, set
`watch: false` and a `watch_note`.

### Watch an employer's job page (no job API)
Add it under `job_pages:` in the same file, with `only_lines_matching` so only relevant new lines
count, e.g. `"intern|summer|research assistant"`. New matching lines show up above the Internships
list for that field and in the email. Analysis Group, Brookings and Bates White are set up this way.

### Add a conference, hackathon or travel scholarship
Add an entry to `config/conferences.yaml` (all fields are explained at the top of the file):
```yaml
  - id: acme-bio-conference
    name: Acme Biology Conference
    type: conference                 # or hackathon / travel scholarship
    fields: [biotech]
    url: https://acmebio.org/2027
    start: 2027-04-10
    end: 2027-04-12
    location: San Diego, CA
    cost: student                    # free / scholarship / student / full / unknown
    travel_scholarship: true
    scholarship_covers: Registration + up to $500 travel
    undergrad_eligible: true
    poster_track: true
    deadlines:
      - {kind: scholarship, date: 2027-01-15, label: Travel award}
      - {kind: abstract, date: 2027-01-15, label: Poster abstracts}
    aka: [ABC 2027]                  # other names feeds use, so they merge into this entry
```
The dashboard ranks free or scholarship-funded events first, then student-priced, then full
price, with Bay Area or virtual events ahead of far-away ones at the same cost. Hackathons have
their own tab, showing Bay Area and online ones by default. Two conference feeds are also read
automatically: [student-opportunities](https://github.com/gkarpe2204/student-opportunities)
(daily bot: conferences, MLH and Devpost hackathons) and
[BadgeUp](https://github.com/Jose-Gael-Cruz-Lopez/BadgeUp) (stale, used as a seed only).

### Where opportunities can be (your travel rule)
`config/settings.yaml -> travel` controls it. By default the dashboard's **Where** filter and the
email only show things in the **Bay Area or Chicagoland**, **remote/virtual**, or anywhere else
**only if housing and travel are both paid for**:
- conferences/programs: set `travel_covered: full | partial | none` (+ `travel_note`) in the
  registry YAMLs
- job postings: detected from the description ("relocation assistance", "housing stipend" +
  "travel reimbursement"...). Phrase lists live in `config/keywords.yaml -> travel`.

Many job feeds (like SimplifyJobs) don't include descriptions, so far-away jobs from them show as
"far away - housing not covered?" and are hidden by default. Choose **Where → Anywhere** to see
everything. Set `allow_partial: true` to also include partly funded ones, or `allow_remote: false`
to hide remote roles.

### Review suggestions (weekly discovery)
Every Sunday, Claude searches the web for things the radar doesn't track yet, one area per week
in turn: early-career programs, Stanford research programs, conferences/travel scholarships, and
new data sources. Early-career postings in the feeds that look like unknown programs are added
too. They appear on the dashboard's **Suggested sources** tab, each with the exact config entry
approving would add.

- **Approve** opens a GitHub issue titled "Approve suggestion #N" - just submit it. The approve
  workflow adds the entry to the right config file (comments kept), commits it, replies on the
  issue and closes it. The next scheduled run starts tracking it.
- **Reject** works the same way and hides the suggestion for good.
- Only issues **you** open do anything (the repo is public, so anyone can open issues), and what
  gets added always comes from the database, never from the issue text.
- On your computer: `python -m radar approve 12` / `python -m radar reject 12`.
- Turn discovery off with `discovery.enabled: false` in `settings.yaml`; `max_searches` and
  `max_suggestions` set how much it does per week.

### Change what the email or scores do
- `config/settings.yaml` → `digest:` controls the deadline window, the reminder days (14/7/2)
  and how many new items to list.
- `config/keywords.yaml` → field keywords, eligibility boosts and penalties (with the reason
  text shown on the dashboard), and early-career words.
- `profile.grad_year` in `settings.yaml` drives everything class-year related, and it moves
  forward automatically each school year.

## Troubleshooting
- **A source shows "failing"** on the Sources tab: the error is shown there and in the digest.
  Run **Verify sources** to see the live response. A failing source never closes items or
  stops other sources.
- **Workday sources fail with "bot protection challenge"**: Workday's Cloudflare is blocking
  GitHub's servers. That's expected sometimes (it comes and goes per board), a blocked board
  never closes its jobs, and SimplifyJobs still covers many of those employers.
- **A source shows "needs secret"**: add the GitHub Secret(s) it names (see the table above).
- **No email arrived**: check the `GMAIL_*` secrets and the "Send the email digest" step log.
  No email also simply means nothing was new or due.
