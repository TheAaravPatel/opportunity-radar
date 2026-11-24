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
   | `USAJOBS_API_KEY` | free key from <https://developer.usajobs.gov/apirequest/> | federal econ internships (Phase 3) |
   | `USAJOBS_EMAIL` | the email you registered the USAJobs key with (USAJobs requires it as the User-Agent) | Phase 3 |
   | `ANTHROPIC_API_KEY` | from <https://console.anthropic.com/> | Claude fallbacks + weekly discovery (later phases) |

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

Biotech companies already covered by the biotech GitHub repo don't need an entry. Keep the
Workday list short (see the warning at the top of the file). Company polling turns on in Phase 2.

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
```
Never delete an old program: set `status_hint: possibly discontinued` instead.

### Add a Stanford page to watch
Add an entry to `config/watched_pages.yaml` with `id`, `name`, `org`, `url`, `fields`, `type`,
and any known `deadlines`. Use `render: playwright` for pages that need JavaScript.

### Add a conference
*(Coming in Phase 6: `config/conferences.yaml` will hold name, field, dates, location, cost,
travel scholarship and its deadline, abstract and registration deadlines, undergrad
eligibility, and URL.)*

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
  GitHub's servers. That's expected sometimes, and SimplifyJobs still covers many of those
  employers.
- **No email arrived**: check the `GMAIL_*` secrets and the "Send the email digest" step log.
  No email also simply means nothing was new or due.
