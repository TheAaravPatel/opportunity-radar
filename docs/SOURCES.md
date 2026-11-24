# Source verification report

Checked on **2026-11-24** before any fetcher was written. Re-run `python -m radar verify` (or the *Verify sources* workflow) to re-check live behaviour.

## What's different from the spec
- **Dead/stale:** BadgeUp has had no commits since Aug 8 (108 days), so I'll use it once as a seed
  list. The Stanford Econ dept RA page is stale; watch SIEPR, the King Center, CDDRL and SOLO instead.
- **Terms prohibit automation:** NBER's terms forbid monitoring, so no fetcher (it becomes a
  manual-check link). EconJobMarket's terms ban robots, so I'll use only its official JSON feed and
  ship that module disabled.
- **Format differs from described:**
  - Simplify Off-Season rows are in the same `listings.json` file.
  - zshah101's feed is Atom, not RSS.
  - Jobs Every Hour's JSON only holds the ~300 newest jobs.
  - The Fed Board uses its own Workday *Government* Cloud tenant.
  - JPMorgan and Goldman use Oracle, not Workday.
  - BofA and Morgan Stanley campus programs, Deloitte, Bain and McKinsey sit on systems with no
    public API, so the page-watcher covers them.
- **Workday blocks heavy pollers from GitHub Actions.** We stay low-volume and fall back on Simplify.
- **Biotech seeds:** 6 of 8 are already covered by the biotech repo. 10x Genomics' Greenhouse board
  is gone.
- **Programs:** Grace Hopper 2026 was cancelled. Meta University is discontinued. Google STEP, McKinsey
  sophomore and BCG early programs are possibly discontinued. Microsoft Explore is still active.


## How sources were checked

How I checked: this container can only reach GitHub (git + raw.githubusercontent.com), PyPI and
the Anthropic API — every other host is blocked by the environment's network policy. So GitHub
sources were cloned and inspected directly (real files, real commit history); everything else was
verified with web search + the health logs of zshah101's bot (which records every ATS request it
makes from GitHub Actions — great evidence of what works from Actions).

### Section 1 — CS / data / AI / quant
| Source | Status | Real format | Notes |
|---|---|---|---|
| SimplifyJobs/Summer2027-Internships (dev) | ✅ live — bot commits every 30 min (last: today 06:32 UTC) | `.github/scripts/listings.json` on `dev`: one 13 MB JSON array, 17,646 rows, **5,076 active**. Fields: `company_name,title,locations[],terms[],category,active,is_visible,date_posted(unix),date_updated,url,degrees[],sponsorship,id` | **Off-Season is in the same file** (rows whose `terms` aren't Summer, e.g. Fall 2026 / Winter 2027) — no separate parsing of README-Off-Season.md needed. Categories are tech-only: Software, AI/ML/Data, Hardware, Product, Quant. |
| zshah101/Automated-List-…-Tech-Internships | ✅ live — bot runs daily-ish (last: today 00:21 UTC) | JSON API `…github.io/…/api/jobs.json` (also `docs/api/jobs.json` in repo): `{generated_at, count:1222, jobs:[{id,company,title,season,category,location,url,posted_at,first_seen_at,remote,salary,skills,source}]}` | **Different from described:** the feed is **Atom**, not RSS (`docs/feed.xml`). Use the JSON API; Atom only as fallback. MIT licensed. Also publishes `radar.json` with each company's typical opening window (useful later for "expected to open"). |
| Jobs Every Hour JSON | ✅ exists (couldn't fetch here; verified via search + its GitHub mirror Simba0315/tech-internships-2027, updated today) | `{total, jobs:[{company,title,url,location,remote,salary,posted}]}` — only the **~300 newest** of ~5.5k open | Free, no key; asks for a link back and ≤1 fetch/hour. Credit link goes in the dashboard footer + source panel. |

### Section 1 — Biotech
| Source | Status | Real format | Notes |
|---|---|---|---|
| vinaynarahari/Summer2027-Biotech-Internships | ✅ live — cron `17 */2 * * *`, last run today 04:51 UTC | `.github/scripts/listings.json` (same schema as Simplify + `ats_key,last_seen,misses`): 671 rows, **451 active**; Off-Season = `terms: "Co-op / Off-Cycle"` in the same file. `companies.json` = `{companies:[{name,ats,slug|tenant/host/site}]}` (176 companies) | Its Workday polling *works* (586 Workday rows seen today). |
| Seed list vs its companies.json | — | — | **Already covered:** Genentech, Gilead, Illumina, Arc Institute, Recursion, insitro. **Not covered → our own fetchers:** 10x Genomics, CZ Biohub (Greenhouse `biohub`). ⚠️ 10x Genomics' Greenhouse board now 404s (they left Greenhouse) — ATS needs re-discovery. Other uncovered Bay Area biotechs to add: GRAIL (Lever `grailbio`), Exelixis (Workday), Genesis Molecular AI (Ashby; old `genesis-therapeutics` slug 404s), Guardant, Denali, Calico, Verily, Cytokinetics, Bio-Rad, Vir, Allogene, Nkarta, Caribou, Mammoth, Scribe, EvolutionaryScale, Atomwise (ATS to confirm at build time). |

### ⚠️ Big finding: Workday blocks heavy pollers from GitHub Actions
zshah101's bot polls ~1,961 Workday tenants: **1,909 now fail** with a Cloudflare "bot challenge"
(429 `cf-mitigated`) — including the Fed (`rb/FRS`), Morgan Stanley, Citi, Capital One, BlackRock,
NVIDIA. But the biotech repo polls only 47 tenants every 2 h and succeeds. So it's volume-triggered.
**Plan:** keep our Workday list small (~15 tenants), slow (≥3 s between requests, search "intern"
only), treat Workday as *best-effort* in source health, and rely on SimplifyJobs as a backstop
(Simplify already has 1,507 active Workday-hosted listings incl. Federal Reserve, Citi, Goldman,
Citadel, Jane Street). Other ATS health from Actions is good: Greenhouse/Lever/Ashby mostly fine,
**Oracle Cloud HCM 0/164 failing** (JPMorgan + Goldman use Oracle, not Workday).

### Section 1 — Economics
| Source | Status | How data is exposed | Plan |
|---|---|---|---|
| PREDOC RA opportunities | ✅ current (deadlines into Jan 2027) | Plain HTML (Chicago Booth CMS), labeled fields per listing (Sponsoring researcher / institution / fields / Deadline); no API/RSS. Full-time predocs are on a separate page `/opportunities` | HTML parser for both pages (label-split + regex fallback), drop expired deadlines. Confirm server-rendered on first Actions run. |
| NBER RA listings | ⚠️ **Different from described — terms prohibit it.** Pages exist (`/career-resources/research-assistant-positions-nber` and `…-not-nber`), but NBER's Terms forbid monitoring/copying site material without written consent | HTML + linked PDFs; no jobs RSS | **Don't build a fetcher** (your rule: no sites whose terms prohibit automated access). Dashboard shows it as a "check manually" link. Most of these are full-time post-bacc roles anyway; many also appear on PREDOC. |
| Federal Reserve | ✅ Summer 2027 postings live | Regional banks incl. SF Fed: Workday `rb.wd5…/FRS`. **Board of Governors: separate Workday *Government* Cloud** `bd.wd104.myworkdayjobsgov.com/frb` (old Taleo is legacy); the Board's 2-yr RA program uses its own login portal → not fetchable | Workday fetcher for both tenants (search "intern" + "research"; bank name from title prefix). Note: the SF Fed econ research intern targets 2027–28 grads (deadline Oct 15 passed); there **is** a "2027 Summer Intern – Research Group – Sophomore Intern" posting. Workday may be bot-blocked → Simplify backstop. |
| USAJobs API | ✅ verified headers: `Host`, `User-Agent: <registered email>`, `Authorization-Key` | `GET data.usajobs.gov/api/search`, ≤500/page | Query `JobCategoryCode` 0110 (economist) + 0101, 1530, 1515, 1599/0199 student-trainee series; student filter via `HiringPath` codes fetched at runtime from `/api/codelist/hiringpaths` (exact codes unverified, so don't hard-code). Fed doesn't post here. |
| EconJobMarket | ⚠️ **Terms ban spiders/robots** except EJM's own tools; content is essentially all post-bacc/PhD | Official public JSON feed (EJM's own `ad-display` tool): `backend.econjobmarket.org/data/zz_public/json/Ads`; RA/predoc ads are on `/market` | Build the module against the **official JSON feed only** (never HTML), but ship it **disabled** until you get EJM's OK (a public dashboard re-displaying ads may need permission). Low value for a freshman either way. |
| Econ-consulting / policy employers | ✅ ATS identified | **Greenhouse:** Brattle (`thebrattlegroup`, has a Summer 2027 RA Intern), Charles River Associates (`charlesriverassociates`, Econ Intern Summer 2027). **Workday:** Cornerstone Research (`cornerstone.wd501`), Compass Lexecon (FTI `fticonsulting.wd108`/`CompassLexeconCareers`), NERA (`mmc.wd1`), Urban Institute, RAND. **iCIMS (no public API):** Analysis Group, Brookings. **Other, no API:** Bates White, Edgeworth (Paylocity), Mathematica | ATS fetchers for Greenhouse/Workday ones; iCIMS + no-API employers go to the **page-watcher** (snapshot + diff of their public job-search page) instead of a scraper. |

**Replacements/additions for econ:** econ job blogs that publish RSS (made for automated reading):
Econ UG Blog (WordPress), The Economic Misfit, appliedeconjobs (Substack) → one generic `rss`
source module, many feeds in config (feed URLs to confirm on first Actions run). Skip AEA JOE (terms
prohibit scraping; PhD-level). Freshman-eligible econ items found: Cornerstone First-Year
Foundations + Sophomore Summit (2027 apps open **Mar 1, 2027**), Fed sophomore research intern,
SIEPR UGRF, PREDOC part-time RA roles.

### Section 1B — Early-career programs
Status of the programs you named (2026–27 cycle; mostly verified via official pages + trackers):
- **Active & open to you (freshman) this cycle:** Microsoft Explore (*not* discontinued — separate
  first-year posting, monthly windows through Feb 2027), **BofA Student Leaders** (repurposed for
  college students entering 2nd year in fall 2027; **Dec 14, 2026 – Feb 15, 2027**), **Jane Street
  FTTP** (first-years; **due Jan 31, 2027**), Goldman Possibilities Series (first-years; ~Feb 2027),
  **Discover Citadel / Citadel Securities** (grads Dec 2028–Jun 2030; **due Mar 5, 2027**), Citi
  Freshman Discovery (~late Mar 2027), Two Sigma First-Year SWE (~late Jan), Inside HRT (spring),
  SIG first-year Discovery (May), Deloitte Discovery Intern (Freshman/Sophomore), NVIDIA Ignite
  (fresh/soph; next window ~Oct 2027), Capital One Summits (invite-only).
- **Sophomore-only (plan ahead for next year):** Morgan Stanley Early Insights (Sep–Oct), Goldman
  Emerging Leaders (Sep–Oct), Citi Early ID, Bain BEL (Oct–Nov), IMC Launchpad, Capital One Early
  Internship (Aug), JPMorgan Advancing Black Pathways → **renamed "JPMorganChase Fellowship"**.
- **Possibly discontinued:** Google STEP, BofA sophomore insight/analyst track, Goldman Virtual
  Insight Series, Citadel Launch, HRT sophomore internship, McKinsey Sophomore BA, BCG Bridge to
  Consulting. **Discontinued (confirmed):** Meta University. Name mismatch: Jane Street "FIRST" →
  the program is **FTTP**; Goldman has no "Early Insights" (its sophomore program is Emerging Leaders).
- **Pipeline orgs:** SEO Career (first-years OK; **SEO Tech Developer First Year Academy Dec 14 –
  Feb 7**), **MLT Ascend (freshmen; closes Jan 4, 2027, needs a nomination)** + MLT Career Prep
  (sophomores), Forté (Emerging Leaders, Fast Track to Finance), Girls Who Invest (next ~Sep 2027),
  ColorStack (rolling), Code2040 (~Feb 1), Out for Undergrad, Rewriting the Code. Basta and Break
  Through Tech don't fit (eligibility).

ATS reality check — **many campus programs live on systems with no public API**: Bank of America and
Morgan Stanley campus (Oleeo `tal.net`), Deloitte / Bain / McKinsey (Avature), BCG / Microsoft /
Capital One summits (Eightfold), Google (own). For these, the **registry page-watcher (layer 1) is the
mechanism**. Keyword detection over ATS feeds (layer 2) covers the ones with public APIs: Citi
(Workday `Citi_Early_Careers_Events_Site`), Capital One, BlackRock early careers, NVIDIA (Workday);
JPMorgan (Oracle `CX_1001`), Goldman (Oracle `CampusHiring`); Jane Street, HRT, IMC, Optiver, DRW,
Five Rings, Akuna, Jump, Tower, Point72 (Greenhouse). Citadel's ATS unconfirmed (own site) →
page-watcher. Eightfold has a JSON endpoint behind its sites; possible later add.

**Time-sensitive:** several deadlines land before the later build phases would surface them (MLT
Ascend Jan 4, Bio-X Jan 12, BSURP Jan 25, Jane Street FTTP Jan 31, BofA Student Leaders Feb 15).
So Phase 1 will load the `deadlines:` already written into the registry YAML files, which puts
them in the dashboard and digest right away, before the page-watcher exists.

### Section 2 — Stanford pages
| Page | Watch URL | Render | Current cycle |
|---|---|---|---|
| CURIS | curis.stanford.edu + `/summer/`, `/applications/`, PURE `/pure_student/` | **JS app (confirmed)** → Playwright | 2027 dates not posted; last year apps Jan 27–Feb 10 |
| Bio-X USRP | biox.stanford.edu/research/undergraduate-research (stable) + yearly `/usrp2027` | HTML | **Deadline Jan 12, 2027 8am** |
| Biology BSURP | biology.stanford.edu/…/biology-summer-undergraduate-research-program-bsurp | HTML | **Deadline Jan 25, 2027**; priority to 1st/2nd-years. ⚠️ bsurp.stanford.edu project list requires Stanford login → watch only the public page |
| SIEPR UGRF | siepr.stanford.edu/programs/undergraduate-student-opportunities/undergraduate-research-fellows-program (+ `/siepr-summer`) | HTML | Rolling review; still shows 2026 info |
| VPUE / Undergrad Research | undergradresearch.stanford.edu/fund-your-project/explore-departmental-funding + grant pages (Major, Small, Chappell Lougee) | HTML | Small Grant Jan 15 & Apr 9, 2027; Major Grant Mar 1, 2027 (Chappell Lougee Dec 1 is sophomores-only) |
| bioBUDS | biobuds.stanford.edu/resources/research-opportunities + `/course-content/internship` | HTML | Page may be stale; spring internship was due late Feb |
| ESO undergrad programs | eso.stanford.edu/programs/undergraduate-students | HTML | Could only see 2024 dates — possibly stale. SSRP-Amgen actually lives at biosciences.stanford.edu/pathways/ssrp-amgen-scholars-program (Feb 1, 2027) |
| Stanford Econ dept | economics.stanford.edu/undergraduate/undergraduate-research-opportunities | HTML | ⚠️ **Stale**: dept Summer RA program not offered since 2024 |

**Suggested additions (high value for a freshman):** SOLO (solo.stanford.edu/opportunities — Stanford's
central opportunity database, per-listing deadlines), Stanford Summer Fellows (for rising
sophomores!), King Center summer RA (econ, posts Feb 2027), CDDRL summer RA, DataSURF, Wu Tsai
Human Performance URP (Jan 22, 2027), CS+Social Good fellowship (Feb 2, 2027), SSRP-Amgen.

### Section 3 — Conferences & hackathons
| Source | Status | Real format | Notes |
|---|---|---|---|
| gkarpe2204/student-opportunities | ✅ live — daily bot commits (last: yesterday 22:39 UTC) | `data/conferences.json` (also on its Pages site): `{updated, count:531, conferences:[{id,name,url,category,source,start,end,location,country,state,region,online,deadline,deadline_label,deadline_url,topics[],funding,notes,first_seen}]}`. Categories: academic 258 (CCF paper deadlines — mostly PhD-level), hackathon 120 (MLH + Devpost), industry 117, tech/business/premed/student ~36 | Its hand-curated `data/watchlist.yml` is a good seed for our registry. Notes **Grace Hopper 2026 was cancelled** (confirmed by press: cancelled 2 weeks out over a funding shortfall; 2027 not announced). |
| Jose-Gael-Cruz-Lopez/BadgeUp | ⚠️ **STALE** — last commit Aug 8, 2026 (108 days) | No data file: hand-edited README markdown tables (106 rows: Status, Conference, Focus, Dates & Location, Access, Audience, Register, Deadline); statuses are outdated (still lists GHC 2026 as "OPEN") | Use it once as a **seed** for config/conferences.yaml (it's a good curated undergrad list across majors), plus a weekly low-priority README parse flagged "stale source". Replacement for freshness: our own registry + page-watcher. |

## Live check from GitHub Actions (2026-11-24, first *Verify sources* run)

All five sources worked from GitHub's network: Simplify (4,311 active listings),
zshah101's Pages API (1,222), **Jobs Every Hour (300 newest of 5,749; its real format
matched the parser)**, and both registries. Of the 73 registry URLs:

| Problem | Entries | What we did |
|---|---|---|
| Wrong / moved URL | Morgan Stanley (2), JPMorganChase Fellowship (program page 404s after the 2026 cycle → programs list), Capital One Summits (`campus.capitalone.com` no longer resolves), Stanford ESO (neither `eso.` nor `oso.stanford.edu` resolves → Stanford Medicine's undergraduate programs page), King Center, Deloitte | URLs corrected and re-verified |
| Redirects to a Stanford login | Econ dept undergrad research page, ChemH Undergraduate Scholars | Econ page: `watch: false`; ChemH: watch its public SOLO listing instead |
| Bot protection blocks GitHub's servers | Citadel + Citadel Securities (4), Bio-X USRP, HRT (HTTP 403) | `watch: false` (deadlines kept by hand; Citadel/HRT postings come from Simplify/zshah101 and HRT's Greenhouse board) |
| Unreachable from GitHub's servers | CURIS (3 pages: connection times out on every run, likely campus-only), McKinsey (read timeout every run) | `watch: false`; CURIS deadlines added by hand when posted (usually late January); the CS research-opportunities page is watched instead |
| Consistent with "possibly discontinued" | Goldman Virtual Insight (404), Google STEP + Meta University (empty JavaScript shells) | Already marked |
| No 2026/2027 mention (evergreen pages) | SEO, ColorStack, Code2040, O4U, IMC Launchpad, CDDRL, HAI, VPUE departmental list, CS research page | Fine for now; the page-watcher will flag real changes |

## Conferences (built 2026-11-24)

- **student-opportunities** parses 531 events (258 PhD-level CS paper venues and 117 industry
  dev conferences are hidden by default; 120 hackathons, of which ~40 are Bay Area or online).
- **BadgeUp** README tables give 56 events in our fields; most 2026 ones are now past (closed).
  Its Status column is ignored because it's out of date.
- **config/conferences.yaml** holds 41 hand-checked entries (CS, hackathons, biotech, econ,
  Stanford-hosted) with travel-scholarship, abstract and registration deadlines verified by web
  search on 2026-11-24. 17 of them also appear in the feeds and are merged into one card.

## Economics (built 2026-11-24, checked live from GitHub Actions)

| Source | Result | Notes |
|---|---|---|
| PREDOC part-time & summer RA page | ✅ 14 listings | Server-rendered `<article>` cards with "Label: value" pairs; past deadlines come out closed (e.g. the Fed Board summer internship, due Oct 31). Full-time page parsed by the same module, disabled until senior year. |
| Econ UG Blog (WordPress RSS) | ✅ 7 of 10 posts kept | UC San Diego's econ opportunities blog. Title filters drop info sessions and UCSD-only events; "Apply by" dates become deadlines. |
| The Economic Misfit (RSS) | ✅ 1 of 10 posts kept | Mostly research articles and postdoc ads, filtered out by title. |
| appliedeconjobs (Substack) | ❌ HTTP 403 | Substack's bot protection blocks GitHub's servers → disabled. |
| USAJobs | ⏸ needs secrets | The hiring-path code list (public) answered; searches need `USAJOBS_API_KEY` + `USAJOBS_EMAIL`. Student hiring path code is `STUDENT`. |
| EconJobMarket JSON | ✅ 311 ads (disabled) | Official public feed works; almost all ads are PhD/faculty. Disabled until EJM agrees to re-display. |
| Federal Reserve Board (Workday Gov Cloud) | ✅ 18 internships | Location text is "Federal Reserve Board" → `location_map` to Washington, DC; photography / graphic design / EEO / public affairs roles skipped. |
| Brattle (Greenhouse) / CRA (Greenhouse) | ✅ 7 / 8 internships | CRA's are mostly for 2028 graduates (scored low for you, correctly). |
| Cornerstone Research (Workday wd501) | ✅ 0 internships right now | Its First-Year Foundations / Sophomore Summit programs are in the early-program registry (open Mar 1, 2027). |
| RAND (Workday wd5) | ✅ / 🚫 intermittently | Only posting is the PhD Summer Associate program → skipped. |
| Regional Fed `rb/FRS`, Compass Lexecon, NERA, Urban, Pew (Workday) | 🚫 bot challenge (HTTP 429) | Reported as failed sub-feeds; they never close jobs. Simplify lists many Fed roles. |
| NBER, Analysis Group, Brookings, Bates White, Edgeworth, Mathematica | manual links | NBER's terms forbid monitoring; the others have no public job API (page-watcher later). |

## Page-watcher (built 2026-11-24, checked live from GitHub Actions)

All 25 watched pages loaded from GitHub's servers and none is disallowed by robots.txt. 24 are
plain HTML; the HAI student-opportunities page is built by JavaScript and was read with the
headless browser. The iCIMS job lists (Analysis Group, Brookings) are watched through their
`?in_iframe=1` page, which has the job titles in plain HTML.

Deadlines read off pages on the first run (dates already in the YAML aren't repeated):

| Page | Found | Sentence on the page |
|---|---|---|
| BSURP | opens Dec 16, 2026 | "Application will open : Wednesday, December 16, 2026, at 12 am PST" |
| VPUE Major Grant | Mar 8, 2027 | "Faculty Mentor Letter Deadline: March 8, 2027" (student deadline Mar 1 is in the YAML) |
| Chappell Lougee | Dec 8, 2026 | "Faculty Mentor Letter Deadline: December 8, 2026" |
| SSRP-Amgen | opened Nov 1, 2026 | "The 2026 SSRP application will open November 1, 2026." |

False positives found in that run and fixed by rule: BSURP's "Deadline for candidates to accept
their offer: March 13" and the King Center's "Submit a final report ... September 20" (dates for
offers, notifications, reports and reimbursements are now skipped).

Not watched: Edgeworth (openings load from Paylocity in a JavaScript frame) and Mathematica
(careers site returns HTTP 403 to bots) stay manual links; CURIS, Bio-X, the Econ department page
and the Citadel/HRT/McKinsey program pages are `watch: false` as before.

## Early-career programs (built 2026-11-24, checked live from GitHub Actions)

**Program pages** (page-watcher): 41 of 42 watched pages load; Goldman's Virtual Insight page
returns 404 (already marked possibly discontinued). Evercore and SIG pages need the browser.
Wording found on the pages:

| Page | Says | Effect |
|---|---|---|
| Goldman Possibilities Series, Emerging Leaders | "Applications are now closed." | "page says: closed" chip |
| Optiver FutureFocus | "Applications are open" | open |
| MLT Career Prep | "The Career Prep Application is now open!" | open |
| BCG Bridge to Consulting | "Graduate recruitment applications are open year-round." | ignored (not about student programs) |

**Job boards** (`early_career_companies`, early-career postings only):

| Board | Result |
|---|---|
| JPMorganChase (Oracle `jpmc.fa.oraclecloud.com`, site CX_1) | ✅ works; no freshman/sophomore titles right now |
| Goldman Sachs (Oracle `hdpc`, site CX_1) | experienced hires only (0 "intern" results) → disabled; program pages watched |
| Jane Street, HRT, IMC, Optiver, DRW, Five Rings, Akuna, Jump, Tower, Point72 (Greenhouse), Old Mission (Ashby) | ✅ all answer; no early-career titles right now (their regular internships come from Simplify) |
| Citi, Capital One, BlackRock, NVIDIA (Workday) | 🚫 Workday bot challenge from GitHub's servers (best effort; never closes items) |

Lessons from the first run: being a firm's only registry program is not evidence - it let all
of IMC's (22) and HRT's (6) internships through the board filter and linked Bain's CREW posting
to Bain's BEL program, so a match now always needs a shared distinctive word or `match_titles`; and "Point72 Academy ... for Experienced Professionals" is excluded by the
experienced/MBA/PhD rule. The 7 early-career flags in the existing data were all biotech false
positives ("Internship Program: ... Drug Discovery Intern"), fixed by requiring the weak word to
come right before the program word.

