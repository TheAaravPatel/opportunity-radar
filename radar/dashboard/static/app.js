// Opportunity Radar dashboard - plain JavaScript, no libraries.
//
// build.py puts all the data in <script id="radar-data"> as JSON. This file
// draws the tabs, filters, cards and calendars from it. Everything runs in the
// browser; there is no server.
//
// Safety: text from job feeds is always inserted with textContent (via el()),
// never as HTML, and only http(s) links are allowed - so a weird job title
// can't inject anything into the page.

"use strict";

const DATA = JSON.parse(document.getElementById("radar-data").textContent);
const TODAY = DATA.today;                         // "YYYY-MM-DD" (Pacific time)
const ME = DATA.profile.class_year;               // e.g. "freshman"
const NEXT = DATA.profile.next_class_year;        // e.g. "sophomore"
const PLURAL = { freshman: "freshmen", sophomore: "sophomores", junior: "juniors", senior: "seniors" };
const PAGE_SIZE = 60;
const ACTIVE_STATUSES = new Set(["open", "opening soon", "expected soon"]);
const CONFERENCE_TYPES = new Set(["conference", "hackathon", "travel scholarship"]);

// ------------------------------------------------------------------ helpers
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "href") { const url = safeUrl(value); if (url) node.href = url; }
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function safeUrl(url) {
  if (!url) return null;
  if (/^https?:\/\//i.test(url) || /^[\w.-]+\.(json|ics|html)$/.test(url) || url.startsWith("#")) return url;
  return null;
}

function link(text, url) {
  return safeUrl(url) ? el("a", { href: url, target: "_blank", rel: "noopener" }, text) : el("span", {}, text);
}

function dayNumber(iso) {           // days since 1970 for a YYYY-MM-DD string
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86400000;
}
const TODAY_N = dayNumber(TODAY);
function daysUntil(iso) { return dayNumber(iso) - TODAY_N; }

function fmtDate(iso, withYear = true) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d, 12));
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: withYear ? "numeric" : undefined, timeZone: "UTC" });
}

function hoursSince(ts) { return ts ? (Date.now() - Date.parse(ts)) / 3600000 : Infinity; }

function ago(ts) {
  const h = hoursSince(ts);
  if (!isFinite(h)) return "never";
  if (h < 1) return "just now";
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function inDays(n) {
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n < 0) return `${-n}d ago`;
  return `in ${n}d`;
}

function store(key, value) {   // localStorage can be unavailable (private mode) - ignore errors
  try { if (value === undefined) return JSON.parse(localStorage.getItem("radar:" + key)); localStorage.setItem("radar:" + key, JSON.stringify(value)); }
  catch (e) { return undefined; }
}

// ------------------------------------------------------------- item facts
const isConference = (it) => CONFERENCE_TYPES.has(it.type);
const isStanford = (it) => !!it.extra.stanford;
const isRegistryProgram = (it) => !!it.registry_id && (it.type === "early-career program" || it.type === "pipeline program");
const isEarly = (it) => it.early || isRegistryProgram(it);
const isActive = (it) => ACTIVE_STATUSES.has(it.status);
const isNew = (it, hours) => !it.baseline && hoursSince(it.first_seen) <= hours;
const hasField = (it, field) => it.fields.includes(field) || it.field_method === "unplaced";

function openToMe(it, includeNext) {
  if (!it.class_years.length) return true;           // no restriction found
  if (it.class_years.includes(ME)) return true;
  return includeNext && it.class_years.includes(NEXT);
}

// The next real deadline. A conference's own start date ("event") is shown
// separately on its card, so it doesn't count here.
function nextDeadline(it) {
  const upcoming = it.deadlines.filter((d) => d.date >= TODAY && d.kind !== "event");
  return upcoming.length ? upcoming[0] : null;
}

// Conferences page default: hide industry trade shows / PhD-only venues and
// hackathons that are neither in the Bay Area nor online (spec rules).
function recommendedConference(it) {
  if (it.extra.hidden_reason) return false;
  if (it.type === "hackathon") return it.bay_area || it.chicagoland || it.remote;
  return true;
}

// Your travel rule (settings.yaml -> travel): Bay Area, Chicagoland or remote,
// or - anywhere else - housing AND flights are paid for.
const FITS = new Set(["bay_area", "chicagoland", "remote", "covered", "no_location"]);
function travelFits(it, level) {
  // Not classified yet (e.g. a job its feed stopped listing): judge by location alone.
  if (!it.travel) return it.bay_area || it.chicagoland || it.remote || !it.location;
  if (FITS.has(it.travel)) return true;
  return level === "partial" && it.travel === "partial";
}
const DEFAULT_WHERE = DATA.profile.allow_partial ? "partial" : "fits";
const TRAVEL_CHIPS = {
  bay_area: ["📍 Bay Area", ""], chicagoland: ["📍 Chicagoland", ""], remote: ["remote", ""],
  covered: ["✈️ housing + travel covered", "new"], partial: ["partly funded", "warn"],
  outside: ["far away - housing not covered?", "warn"], no_location: ["location not listed", ""],
};
const COST_LABELS = { free: "free", scholarship: "scholarship-funded", student: "student price", full: "full price" };

function fmtRange(start, end) {
  if (!start) return "dates TBA";
  if (!end || end === start) return fmtDate(start);
  return `${fmtDate(start, start.slice(0, 4) !== end.slice(0, 4))} – ${fmtDate(end)}`;
}

// ------------------------------------------------------------- tabs/views
const TABS = [
  { id: "internships", label: "Internships", subs: [["cs", "CS"], ["biotech", "Biotech"], ["econ", "Econ"], ["early", "Early Programs"]] },
  { id: "stanford", label: "Stanford" },
  { id: "conferences", label: "Conferences & Hackathons", subs: [["upcoming", "Upcoming"], ["hackathons", "Hackathons"], ["scholarships", "Scholarship deadlines"], ["calendar", "Calendar"]] },
  { id: "deadlines", label: "Deadlines" },
  { id: "suggested", label: "Suggested sources" },
  { id: "sources", label: "Sources" },
];

function itemsFor(tab, sub) {
  const items = DATA.items;
  if (tab === "internships") {
    if (sub === "early") return items.filter((it) => isEarly(it) && !isStanford(it) && !isConference(it));
    return items.filter((it) => !isConference(it) && !isStanford(it) && !isRegistryProgram(it) && hasField(it, sub));
  }
  if (tab === "stanford") return items.filter(isStanford);
  if (tab === "conferences") {
    if (sub === "upcoming") return items.filter((it) => isConference(it) && it.type !== "hackathon");
    if (sub === "hackathons") return items.filter((it) => it.type === "hackathon");
    return items.filter(isConference);
  }
  return items;
}

const DEFAULT_FILTERS = {
  q: "", status: "active", type: "", elig: "", plan: false, deadline: "", fresh: "", term: "", cost: "", sort: "newest",
  field: "", showall: false, where: DEFAULT_WHERE,
};

function defaultsFor(view) {
  if (view === "internships/early") return { ...DEFAULT_FILTERS, elig: "me", sort: "deadline" };
  if (view === "stanford") return { ...DEFAULT_FILTERS, sort: "deadline" };
  if (view.startsWith("conferences")) return { ...DEFAULT_FILTERS, sort: "recommended" };
  return { ...DEFAULT_FILTERS };
}

const state = { tab: "internships", sub: "cs", limit: PAGE_SIZE, calMonth: TODAY.slice(0, 7), calDay: null };

function viewKey() { const tab = TABS.find((t) => t.id === state.tab); return tab.subs ? `${state.tab}/${state.sub}` : state.tab; }
function filters() {
  const key = viewKey();
  state.filters = state.filters || {};
  if (!state.filters[key]) state.filters[key] = { ...defaultsFor(key), ...(store("filters:" + key) || {}) };
  return state.filters[key];
}
function setFilter(name, value) {
  const f = filters(); f[name] = value; store("filters:" + viewKey(), f); state.limit = PAGE_SIZE; renderMain();
}

function applyFilters(items, f) {
  const q = f.q.trim().toLowerCase();
  let out = items.filter((it) => {
    if (f.status === "active" && !isActive(it)) return false;
    if (f.status === "open" && it.status !== "open") return false;
    if (f.type && it.type !== f.type) return false;
    if (f.elig === "me" && !openToMe(it, f.plan)) return false;
    if (f.elig === "50" && (it.score ?? 0) < 50) return false;
    if (f.elig === "70" && (it.score ?? 0) < 70) return false;
    if ((f.where === "fits" || f.where === "partial") && !travelFits(it, f.where)) return false;
    if (f.where === "bay" && !it.bay_area) return false;
    if (f.where === "chicago" && !it.chicagoland) return false;
    if (f.where === "remote" && !it.remote) return false;
    const next = nextDeadline(it);
    if (f.deadline === "has" && !next) return false;
    if ((f.deadline === "14" || f.deadline === "30") && !(next && daysUntil(next.date) <= Number(f.deadline))) return false;
    if (f.fresh === "1" && !isNew(it, 24)) return false;
    if (f.fresh === "7" && !isNew(it, 24 * 7)) return false;
    if (f.term === "summer" && !it.terms.some((t) => /summer/i.test(t))) return false;
    if (f.term === "offseason" && !it.extra.off_season) return false;
    const conf = it.extra.conference || {};
    if (f.cost === "funded" && !["free", "scholarship"].includes(conf.cost_tier || it.extra.cost_tier)) return false;
    if (f.cost === "student" && !["free", "scholarship", "student"].includes(conf.cost_tier)) return false;
    if (f.field && !hasField(it, f.field)) return false;
    if (CONFERENCE_TYPES.has(it.type) && !f.showall && !recommendedConference(it)) return false;
    if (q) {
      const hay = `${it.title} ${it.org} ${it.location} ${it.subcategory} ${it.terms.join(" ")} ${it.extra.notes || ""}`.toLowerCase();
      if (!q.split(/\s+/).every((word) => hay.includes(word))) return false;
    }
    return true;
  });
  const byDeadline = (it) => { const n = nextDeadline(it); return n ? n.date : "9999"; };
  const sorters = {
    newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || (b.posted || "").localeCompare(a.posted || ""),
    posted: (a, b) => (b.posted || "").localeCompare(a.posted || ""),
    deadline: (a, b) => byDeadline(a).localeCompare(byDeadline(b)) || (b.score ?? 0) - (a.score ?? 0),
    score: (a, b) => (b.score ?? 0) - (a.score ?? 0) || (b.first_seen || "").localeCompare(a.first_seen || ""),
    // Free/scholarship first, then student-priced, then full price; Bay Area or
    // virtual before far away; then soonest event.
    recommended: (a, b) => (a.extra.priority ?? 9) - (b.extra.priority ?? 9)
      || ((a.extra.conference || {}).start_date || "9999").localeCompare((b.extra.conference || {}).start_date || "9999"),
    event: (a, b) => ((a.extra.conference || {}).start_date || "9999").localeCompare((b.extra.conference || {}).start_date || "9999"),
  };
  return out.sort(sorters[f.sort] || sorters.newest);
}

// ---------------------------------------------------------------- widgets
function select(name, label, options, value) {
  return el("label", {}, label,
    el("select", { onchange: (e) => setFilter(name, e.target.value) },
      options.map(([v, text]) => el("option", { value: v, selected: v === value }, text))));
}

function checkbox(name, label, value) {
  return el("label", { class: "check" },
    el("input", { type: "checkbox", checked: value, onchange: (e) => setFilter(name, e.target.checked) }), label);
}

function filterPanel(items, f, view) {
  const types = [...new Set(items.map((it) => it.type))].sort();
  const isInternship = view.startsWith("internships/") && view !== "internships/early";
  const search = el("input", { type: "search", placeholder: "Search title, company, location...", value: f.q });
  let timer = null;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => setFilter("q", search.value), 200); });

  const rows = [
    el("label", { class: "search-wide" }, "Search", search),
    select("sort", "Sort by", view.startsWith("conferences")
      ? [["recommended", "Best value first (free/funded, nearby)"], ["event", "Event date"], ["deadline", "Deadline soonest"], ["newest", "Newest first"]]
      : [["newest", "Newest first"], ["deadline", "Deadline soonest"], ["score", "Best eligibility"], ["posted", "Posted date"]], f.sort),
    select("status", "Status", [["active", "Open / opening"], ["open", "Open now"], ["all", "All (incl. closed)"]], f.status),
    select("elig", "Eligibility", [["", "Any"], ["me", `Open to ${PLURAL[ME] || "me"}`], ["50", "Score 50+"], ["70", "Score 70+"]], f.elig),
    select("fresh", "New", [["", "Any time"], ["1", "Last 24 hours"], ["7", "Last 7 days"]], f.fresh),
    select("deadline", "Deadline", [["", "Any"], ["has", "Has a deadline"], ["14", "Next 14 days"], ["30", "Next 30 days"]], f.deadline),
    select("where", "Where", [
      ["fits", "Bay Area / Chicagoland / remote, or housing + travel covered"],
      ["partial", "...plus partly funded"],
      ["bay", "Bay Area only"], ["chicago", "Chicagoland only"], ["remote", "Remote only"],
      ["", "Anywhere"]], f.where),
  ];
  if (types.length > 1) rows.push(select("type", "Type", [["", "All types"], ...types.map((t) => [t, t])], f.type));
  if (isInternship) rows.push(select("term", "Term", [["", "Any term"], ["summer", "Summer"], ["offseason", "Off-season (fall/winter/spring)"]], f.term));
  if (view === "stanford" || view.startsWith("conferences")) {
    rows.push(select("field", "Field", [["", "All fields"], ["cs", "CS"], ["econ", "Econ"], ["biotech", "Biotech"]], f.field));
  }
  if (view.startsWith("conferences")) {
    rows.push(select("cost", "Cost", [["", "Any cost"], ["funded", "Free or scholarship-funded"], ["student", "Free, funded or student price"]], f.cost));
    rows.push(checkbox("showall", "Also show industry / PhD-level events and far-away hackathons", f.showall));
  }
  if (f.elig === "me" && NEXT) rows.push(checkbox("plan", `Also show ${NEXT} programs (plan ahead)`, f.plan));

  const details = el("details", { class: "filters", open: window.innerWidth > 760 || undefined },
    el("summary", { text: "Filters & search" }), el("div", { class: "filter-row" }, rows));
  return details;
}

function statusChip(it) {
  return el("span", { class: `chip status-${it.status.replace(/\s+/g, "-")}`, text: it.status });
}

function scoreBadge(it) {
  if (it.score === null || it.score === undefined) return null;
  const level = it.score >= 70 ? "high" : it.score >= 45 ? "mid" : "low";
  return el("div", { class: `score ${level}`, title: "Eligibility score (0-100)" }, String(it.score), el("small", { text: "fit" }));
}

function deadlineLine(it) {
  const next = nextDeadline(it);
  if (!next) {
    const past = it.deadlines.filter((d) => d.date < TODAY).pop();
    return past ? el("div", { class: "deadline-line" }, `Last deadline ${fmtDate(past.date)} (passed)`) : null;
  }
  const n = daysUntil(next.date);
  const cls = n <= 7 ? "soon" : n <= 14 ? "upcoming" : "";
  const kind = next.kind === "opens" ? "Opens" : next.kind === "event" ? "Event" : "Deadline";
  const more = it.deadlines.filter((d) => d.date >= TODAY).length - 1;
  return el("div", { class: "deadline-line", title: next.evidence ? `From the page: "${next.evidence}"` : "" },
    `${kind}: ${fmtDate(next.date)}${next.approx ? " (approx.)" : ""} `,
    el("span", { class: cls, text: `(${inDays(n)})` }),
    next.label ? ` - ${next.label}` : "",
    more > 0 ? ` · +${more} more` : "");
}

function whyDetails(it) {
  if (!it.reasons || !it.reasons.length) return null;
  return el("details", { class: "why" }, el("summary", { text: "Why this score?" }),
    el("ul", {}, it.reasons.map((r) => el("li", {},
      el("span", { class: r.points >= 0 ? "plus" : "minus", text: (r.points >= 0 ? "+" : "") + r.points }), " ", r.reason))));
}

function card(it) {
  const chips = [statusChip(it)];
  if (isNew(it, 24)) chips.push(el("span", { class: "chip new", text: "new" }));
  else if (isNew(it, 24 * 7)) chips.push(el("span", { class: "chip new", text: "this week" }));
  if (it.extra.stale) chips.push(el("span", { class: "chip warn", text: "page may be stale" }));
  if (it.extra.watch === false) chips.push(el("span", { class: "chip", title: it.extra.watch_note || "", text: "not auto-checked" }));

  const meta = [];
  it.fields.forEach((f) => meta.push(el("span", { class: "chip field", text: f })));
  if (it.field_method === "unplaced") meta.push(el("span", { class: "chip field", text: "field?" }));
  if (it.subcategory && it.subcategory !== it.type) meta.push(el("span", { class: "chip", text: it.subcategory }));
  if (it.type !== "industry internship") meta.push(el("span", { class: "chip", text: it.type }));
  it.terms.slice(0, 3).forEach((t) => meta.push(el("span", { class: "chip", text: t })));
  const [travelText, travelClass] = TRAVEL_CHIPS[it.travel] || [];
  if (travelText) meta.push(el("span", { class: `chip ${travelClass}`, title: it.travel_reason || "", text: travelText }));
  if (it.class_years.length) meta.push(el("span", { class: "chip", text: it.class_years.map((y) => PLURAL[y]).join(" / ") }));

  const conf = it.extra.conference;
  if (conf) {
    if (COST_LABELS[conf.cost_tier]) meta.push(el("span", { class: "chip", text: COST_LABELS[conf.cost_tier], title: conf.cost_note || "" }));
    if (conf.scholarship) meta.push(el("span", { class: "chip new", text: "travel scholarship", title: conf.scholarship_covers || "" }));
    if (conf.poster_track) meta.push(el("span", { class: "chip", text: "undergrad poster track" }));
    if (conf.virtual) meta.push(el("span", { class: "chip", text: "virtual" }));
  }
  if (it.extra.hidden_reason) meta.push(el("span", { class: "chip warn", text: it.extra.hidden_reason }));
  const where = [it.org !== it.title ? it.org : "", it.location].filter(Boolean).join(" · ");
  // The status reason repeats the deadline line for open items, so only show
  // it when it adds something ("usually opens in March", "discontinued"...).
  const showReason = it.status_reason && (it.status !== "open" || !nextDeadline(it)) && !(it.status === "opening soon" && nextDeadline(it));
  const foot = [
    it.sources.length ? `via ${it.sources.join(", ")}` : null,
    it.first_seen ? `first seen ${ago(it.first_seen)}` : null,
    it.posted ? `posted ${fmtDate(it.posted)}` : null,
  ].filter(Boolean);

  return el("article", { class: `card${it.status === "closed" ? " is-closed" : ""}` },
    el("div", { class: "card-top" },
      el("div", {}, el("div", { class: "card-meta" }, chips),
        el("h3", { class: "card-title" }, link(it.title, it.url)),
        el("div", { class: "card-org", text: where })),
      scoreBadge(it)),
    el("div", { class: "card-meta" }, meta),
    conf ? el("div", { class: "deadline-line" }, `📅 ${fmtRange(conf.start_date, conf.end_date)}`,
      conf.start_date && conf.start_date >= TODAY ? ` (${inDays(daysUntil(conf.start_date))})` : "") : null,
    conf && conf.scholarship_covers ? el("div", { class: "card-reason", text: `Funding: ${conf.scholarship_covers}` }) : null,
    ["covered", "partial"].includes(it.travel) && it.travel_reason && !(conf && conf.scholarship_covers)
      ? el("div", { class: "card-reason", text: `Travel: ${it.travel_reason}` }) : null,
    deadlineLine(it),
    showReason ? el("div", { class: "card-reason", text: it.status_reason }) : null,
    it.extra.notes ? el("details", { class: "why" }, el("summary", { text: "Notes" }), el("p", { text: it.extra.notes })) : null,
    whyDetails(it),
    el("div", { class: "card-foot" }, foot.map((t) => el("span", { text: t }))));
}

function cardList(items) {
  const shown = items.slice(0, state.limit);
  const wrap = el("div", {},
    el("div", { class: "result-count", text: `${items.length} result${items.length === 1 ? "" : "s"}` }),
    shown.length ? el("div", { class: "cards" }, shown.map(card)) : el("div", { class: "empty", text: "Nothing matches these filters." }));
  if (items.length > shown.length) {
    wrap.append(el("button", { class: "more-btn", onclick: () => { state.limit += PAGE_SIZE; renderMain(); } }, `Show more (${items.length - shown.length} left)`));
  }
  return wrap;
}

// ---------------------------------------------------------------- calendar
function deadlineEntries(items) {
  const entries = [];
  for (const it of items) for (const d of it.deadlines) entries.push({ ...d, item: it });
  return entries.sort((a, b) => a.date.localeCompare(b.date));
}

function calendar(entries) {
  const [year, month] = state.calMonth.split("-").map(Number);
  const first = new Date(Date.UTC(year, month - 1, 1));
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const startPad = first.getUTCDay();
  const byDay = {};
  for (const e of entries) (byDay[e.date] = byDay[e.date] || []).push(e);

  const shift = (delta) => {
    const d = new Date(Date.UTC(year, month - 1 + delta, 1));
    state.calMonth = d.toISOString().slice(0, 7); state.calDay = null; renderMain();
  };
  const title = first.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const grid = el("div", { class: "cal-grid" }, ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => el("div", { class: "cal-dow", text: d })));
  for (let i = 0; i < startPad; i++) grid.append(el("div", {}));
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${state.calMonth}-${String(day).padStart(2, "0")}`;
    const list = byDay[iso] || [];
    const classes = ["cal-day", iso === TODAY ? "today" : "", iso === state.calDay ? "selected" : ""].join(" ");
    grid.append(el("button", { class: classes, title: list.map((e) => e.item.title).join("\n") || null,
      onclick: () => { state.calDay = state.calDay === iso ? null : iso; renderMain(); } },
      el("div", { class: "cal-num", text: String(day) }),
      el("div", { class: "cal-dots" }, list.slice(0, 6).map(() => el("i", {}))),
      list.slice(0, 3).map((e) => el("div", { class: "cal-label", text: e.item.title })),
      list.length > 3 ? el("div", { class: "cal-label", text: `+${list.length - 3} more` }) : null));
  }

  const inMonth = entries.filter((e) => e.date.startsWith(state.calMonth) && (!state.calDay || e.date === state.calDay));
  // An empty month isn't useful: show what's coming up next instead.
  const upcomingNext = entries.filter((e) => e.date >= TODAY).slice(0, 8);
  const showNext = !inMonth.length && !state.calDay && upcomingNext.length;
  return el("div", {},
    el("div", { class: "cal-head" },
      el("button", { onclick: () => shift(-1), "aria-label": "Previous month" }, "‹"),
      el("h2", { text: title }),
      el("button", { onclick: () => shift(1), "aria-label": "Next month" }, "›")),
    grid,
    showNext ? agenda(upcomingNext, `Nothing in ${title} - coming up next`)
      : agenda(inMonth, state.calDay ? `Deadlines on ${fmtDate(state.calDay)}` : `All deadlines in ${title}`));
}

function agenda(entries, heading) {
  if (!entries.length) return el("div", { class: "agenda" }, el("h3", { text: heading }), el("div", { class: "empty", text: "No deadlines." }));
  const rows = entries.map((e) => {
    const n = daysUntil(e.date);
    const kind = { opens: "Opens", application: "Deadline", event: "Event starts" }[e.kind] || e.kind[0].toUpperCase() + e.kind.slice(1);
    return el("div", { class: "agenda-item" },
      el("span", { class: "when" }, fmtDate(e.date, false), el("br"), el("small", { text: inDays(n) })),
      el("span", { class: "what" }, `${kind}: `, link(e.item.title, e.item.url), e.item.org && e.item.org !== e.item.title ? ` - ${e.item.org}` : "",
        e.label ? ` (${e.label}${e.approx ? ", approx." : ""})` : e.approx ? " (approx.)" : ""));
  });
  return el("div", { class: "agenda" }, el("h3", { text: heading }), rows);
}

// ------------------------------------------------------------------- views
function listView(items) {
  const f = filters();
  const filtered = applyFilters(items, f);
  return el("div", {}, filterPanel(items, f, viewKey()), cardList(filtered));
}

function earlyIntro() {
  return el("div", { class: "panel" },
    el("p", {}, "Insight days, discovery programs, summits and pipeline programs for first- and second-years. ",
      "Statuses come from the registry (config/early_programs.yaml) and change as dates pass: ",
      el("b", { text: "expected soon" }), " means the program usually opens around now but nothing is posted yet."));
}

function deadlinesView() {
  const f = filters();
  const pool = DATA.items.filter((it) => (f.status === "all" || it.status !== "closed")
    && (!isConference(it) || recommendedConference(it))
    && (f.where === "" || travelFits(it, f.where === "partial" ? "partial" : "fits")));
  const groups = {
    internships: (it) => !isConference(it) && !isStanford(it) && !isEarly(it),
    early: (it) => isEarly(it) && !isStanford(it),
    stanford: isStanford,
    conferences: isConference,
  };
  const items = pool.filter((it) => (!f.type || groups[f.type](it)) && (!f.field || it.fields.includes(f.field)));
  const entries = deadlineEntries(items).filter((e) => !f.kind || e.kind === f.kind);
  const soon = entries.filter((e) => { const n = daysUntil(e.date); return e.kind !== "event" && n >= 0 && n <= 14; });
  const controls = el("details", { class: "filters", open: true }, el("summary", { text: "Filters" }),
    el("div", { class: "filter-row" },
      select("field", "Field", [["", "All fields"], ["cs", "CS"], ["econ", "Econ"], ["biotech", "Biotech"]], f.field || ""),
      select("type", "Kind of opportunity", [["", "Everything"], ["internships", "Internships"], ["early", "Early programs"], ["stanford", "Stanford"], ["conferences", "Conferences"]], f.type),
      select("kind", "Deadline type", [["", "All"], ["application", "Applications"], ["opens", "Opening dates"], ["scholarship", "Scholarships"], ["abstract", "Abstracts"], ["registration", "Registration"]], f.kind || ""),
      select("status", "Status", [["active", "Hide closed"], ["all", "Include closed"]], f.status),
      select("where", "Where", [["fits", "Fits your travel rule"], ["partial", "...plus partly funded"], ["", "Anywhere"]], f.where)));
  return el("div", {}, controls,
    el("div", { class: "panel" }, el("h2", { text: "Next 14 days" }), agenda(soon, `${soon.length} deadline${soon.length === 1 ? "" : "s"}`)),
    el("div", { class: "panel" }, calendar(entries)),
    el("p", { class: "result-count" }, "Subscribe in Google Calendar: ", link("deadlines.ics", "deadlines.ics"), " (add by URL)."));
}

function changeList(changes) {
  return changes.map((c) => el("details", {},
    el("summary", {}, `${c.title || c.id} - changed ${ago(c.last_changed_at)} `, link("open page", c.url)), diffBlock(c.diff)));
}

// Employer job pages (no API) that changed, shown above the Internships list.
function jobPageChanges(field) {
  const changes = (DATA.page_changes || []).filter((c) => c.kind === "job_page" && (!field || c.fields.includes(field)));
  if (!changes.length) return null;
  return el("div", { class: "panel" }, el("h2", { text: "Employer job pages that changed" }),
    el("p", { class: "result-count", text: "These employers have no job feed, so the radar watches their job pages. Lines with + are new." }),
    changeList(changes));
}

function stanfordView() {
  const items = itemsFor("stanford");
  const changes = (DATA.page_changes || []).filter((c) => c.kind !== "job_page");
  const changePanel = el("div", { class: "panel" }, el("h2", { text: "Page changed alerts" }),
    changes.length
      ? changeList(changes)
      : el("p", { class: "result-count", text: "No watched page has changed in the last 14 days." }));
  const entries = deadlineEntries(items.filter((it) => it.status !== "closed"));
  return el("div", {}, changePanel,
    el("details", { class: "panel", open: true }, el("summary", {}, el("b", { text: "Deadline calendar" })), calendar(entries)),
    listView(items));
}

function diffBlock(diff) {
  if (!diff) return null;
  return el("pre", { class: "diff" }, diff.split("\n").slice(0, 80).map((line) =>
    el("span", { class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "", text: line + "\n" })));
}

function conferencesView() {
  const items = itemsFor("conferences", state.sub);
  if (!items.length) return el("div", { class: "empty", text: "No conferences loaded yet." });
  if (state.sub === "upcoming") return el("div", {}, conferenceIntro(), listView(items));
  if (state.sub === "hackathons") {
    return el("div", {}, el("p", { class: "result-count", text: "Bay Area, Chicagoland and online hackathons by default; tick the box in Filters to see the rest." }), listView(items));
  }
  const shown = items.filter((it) => it.status !== "closed" && recommendedConference(it));
  const entries = deadlineEntries(shown);
  if (state.sub === "scholarships") {
    // For freshmen the travel scholarship is usually the deadline that matters -
    // often months before the event itself.
    const kinds = new Set(["scholarship", "abstract", "registration", "application"]);
    const upcoming = entries.filter((e) => kinds.has(e.kind) && e.date >= TODAY);
    return el("div", { class: "panel" },
      agenda(upcoming.filter((e) => e.kind === "scholarship"), "Travel scholarship deadlines"),
      agenda(upcoming.filter((e) => e.kind !== "scholarship"), "Abstract, application and registration deadlines"));
  }
  return el("div", { class: "panel" }, calendar(entries));
}

function conferenceIntro() {
  return el("p", { class: "result-count" },
    "Conferences and travel scholarships, best value first: free or scholarship-funded, then student-priced, then full price; ",
    "near you (Bay Area / Chicagoland) or virtual before far away. Far-away events only show if they pay for housing + travel ",
    "(change \"Where\" in Filters). Industry trade shows and PhD-only venues are hidden unless you tick the box in Filters. ",
    "Hackathons have their own tab.");
}

function suggestedView() {
  const list = DATA.suggestions || [];
  if (!list.length) return el("div", { class: "empty", text: "No suggestions waiting. The weekly discovery job will add new programs and conferences here for you to approve." });
  return el("div", { class: "cards" }, list.map((s) => el("article", { class: "card" },
    el("h3", { class: "card-title" }, link(s.name, s.url)),
    el("div", { class: "card-org", text: `${s.kind} · found ${ago(s.found_at)} · would go in ${s.target_config || "config"}` }),
    s.why ? el("p", { class: "card-reason", text: s.why }) : null)));
}

function sourcesView() {
  // data-label lets the phone layout show each cell as "Label: value".
  const rows = DATA.sources.map((s) => el("tr", {},
    el("td", { "data-label": "Source" }, el("span", { class: `dot ${s.state.replace(/\s+/g, "-")}` }), link(s.name, s.homepage)),
    el("td", { "data-label": "Status", text: s.state + (s.failures > 1 ? ` (${s.failures}x)` : "") }),
    el("td", { "data-label": "Last success", text: s.last_success ? ago(s.last_success) : "never" }),
    el("td", { "data-label": "Upstream updated", text: s.upstream_updated ? ago(s.upstream_updated) : "-" }),
    el("td", { "data-label": "Items", text: s.items ?? "-" }),
    el("td", { "data-label": "Notes" }, s.error ? el("div", { class: "err", text: s.error }) : null,
      (s.warnings || []).map((w) => el("div", { class: "result-count", text: w })))));
  const manual = DATA.manual_links || [];
  const pages = DATA.watched_pages || [];
  const pageRows = pages.map((p) => el("tr", {},
    el("td", { "data-label": "Page" }, el("span", { class: `dot ${p.consecutive_failures ? "failing" : p.last_checked_at ? "ok" : "never-run"}` }),
      link(p.name, p.url), p.kind === "job_page" ? el("span", { class: "result-count", text: " (job page)" }) : null),
    el("td", { "data-label": "Last checked", text: p.last_checked_at ? ago(p.last_checked_at) : "never" }),
    el("td", { "data-label": "Last changed", text: p.last_changed_at ? ago(p.last_changed_at) : "-" }),
    el("td", { "data-label": "Notes" },
      p.consecutive_failures ? el("div", { class: "err", text: `${p.last_error || "failed"}${p.consecutive_failures > 1 ? ` (${p.consecutive_failures}x)` : ""}` }) : null,
      p.fetch_method === "browser" ? el("div", { class: "result-count", text: "needs a browser (JavaScript page)" }) : null)));
  return el("div", {},
    el("div", { class: "panel" }, el("h2", { text: "Source health" }),
      el("table", { class: "health" },
        el("thead", {}, el("tr", {}, ["Source", "Status", "Last success", "Upstream updated", "Items", "Notes"].map((h) => el("th", { text: h })))),
        el("tbody", {}, rows)),
      el("p", { class: "result-count", text: "\"Upstream updated\" is when the source itself last changed; a source counts as stale after 14 days without updates." })),
    pages.length ? el("details", { class: "panel" }, el("summary", {}, el("b", { text: `Watched pages (${pages.length})` }),
      ` - ${pages.filter((p) => p.consecutive_failures).length} failing`),
      el("table", { class: "health" },
        el("thead", {}, el("tr", {}, ["Page", "Last checked", "Last changed", "Notes"].map((h) => el("th", { text: h })))),
        el("tbody", {}, pageRows)),
      el("p", { class: "result-count", text: "Checked once a day. Pages marked \"not auto-checked\" on their cards block bots or need a login." })) : null,
    manual.length ? el("div", { class: "panel" }, el("h2", { text: "Check these by hand" }),
      manual.map((m) => el("p", {}, link(m.name, m.url), ` - ${m.why}`))) : null,
    el("div", { class: "panel" }, el("h2", { text: "Data" }),
      el("p", {}, "Raw data: ", link("data.json", "data.json"), " · Deadline calendar feed: ", link("deadlines.ics", "deadlines.ics"))));
}

// --------------------------------------------------------------- top level
function counts() {
  const out = {};
  for (const tab of TABS) {
    if (tab.subs && tab.id === "internships") {
      for (const [sub] of tab.subs) out[`${tab.id}/${sub}`] = itemsFor(tab.id, sub).filter((it) => isActive(it) && travelFits(it, DEFAULT_WHERE)).length;
    }
  }
  const shown = (it) => isActive(it) && travelFits(it, DEFAULT_WHERE) && (!isConference(it) || recommendedConference(it));
  out.stanford = itemsFor("stanford").filter(shown).length;
  out.conferences = itemsFor("conferences").filter(shown).length;
  out["conferences/upcoming"] = itemsFor("conferences", "upcoming").filter(shown).length;
  out["conferences/hackathons"] = itemsFor("conferences", "hackathons").filter(shown).length;
  out.suggested = (DATA.suggestions || []).length;
  return out;
}

function renderTabs() {
  const c = counts();
  const tabs = document.getElementById("tabs");
  tabs.replaceChildren(...TABS.map((t) => el("button", {
    class: "tab", role: "tab", "aria-selected": String(t.id === state.tab),
    onclick: () => { state.tab = t.id; state.sub = t.subs ? t.subs[0][0] : null; state.limit = PAGE_SIZE; state.calDay = null; update(); },
  }, t.label, c[t.id] ? el("span", { class: "count", text: c[t.id] }) : null)));

  const tab = TABS.find((t) => t.id === state.tab);
  const subtabs = document.getElementById("subtabs");
  subtabs.replaceChildren(...(tab.subs || []).map(([id, label]) => el("button", {
    class: "subtab", "aria-selected": String(id === state.sub),
    onclick: () => { state.sub = id; state.limit = PAGE_SIZE; state.calDay = null; update(); },
  }, label, c[`${tab.id}/${id}`] !== undefined ? el("span", { class: "count", text: c[`${tab.id}/${id}`] }) : null)));
}

function renderMain() {
  const main = document.getElementById("main");
  let view;
  if (state.tab === "internships") {
    view = el("div", {}, state.sub === "early" ? earlyIntro() : jobPageChanges(state.sub), listView(itemsFor("internships", state.sub)));
  }
  else if (state.tab === "stanford") view = stanfordView();
  else if (state.tab === "conferences") view = conferencesView();
  else if (state.tab === "deadlines") view = deadlinesView();
  else if (state.tab === "suggested") view = suggestedView();
  else view = sourcesView();
  main.replaceChildren(view);
}

function renderHeader() {
  const fresh = DATA.items.filter((it) => isNew(it, 24) && travelFits(it, DEFAULT_WHERE)).length;
  const due = DATA.items.filter((it) => it.status !== "closed" && travelFits(it, DEFAULT_WHERE) && (!isConference(it) || recommendedConference(it)))
    .flatMap((it) => it.deadlines)
    .filter((d) => { const n = daysUntil(d.date); return d.kind !== "event" && n >= 0 && n <= 14; }).length;
  const failing = DATA.sources.filter((s) => s.state === "failing").length;
  const built = new Date(DATA.generated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  document.getElementById("updated").textContent = `Updated ${ago(DATA.generated_at)} · ${built}`;
  document.getElementById("headline").replaceChildren(
    el("strong", { text: fresh }), " new in 24h · ",
    el("strong", { text: due }), ` deadline${due === 1 ? "" : "s"} in the next 14 days`,
    failing ? el("span", {}, " · ", el("a", { href: "#sources", text: `${failing} source${failing === 1 ? "" : "s"} failing` })) : "");

  const credits = DATA.sources.filter((s) => s.credit && s.enabled);
  document.getElementById("footer").replaceChildren(
    el("div", {}, "Data from ", ...DATA.sources.filter((s) => s.enabled && s.homepage).flatMap((s, i) => [i ? ", " : "", link(s.name, s.homepage)]), "."),
    ...credits.map((s) => el("div", {}, s.credit, " ", link(s.homepage, s.homepage))),
    el("div", {}, `Built ${fmtDate(DATA.generated_at)} · class of ${DATA.profile.grad_year} (${ME || "-"})`));
}

function update() {
  location.hash = state.sub ? `${state.tab}/${state.sub}` : state.tab;
  renderTabs();
  renderMain();
}

function init() {
  const [tab, sub] = (location.hash.slice(1) || store("tab") || "internships").split("/");
  const known = TABS.find((t) => t.id === tab);
  if (known) { state.tab = known.id; state.sub = known.subs ? (known.subs.some(([s]) => s === sub) ? sub : known.subs[0][0]) : null; }
  window.addEventListener("hashchange", () => {
    const [t, s] = location.hash.slice(1).split("/");
    const k = TABS.find((x) => x.id === t);
    if (k && (k.id !== state.tab || s !== state.sub)) { state.tab = k.id; state.sub = k.subs ? (s || k.subs[0][0]) : null; renderTabs(); renderMain(); }
  });
  renderHeader();
  update();
}

init();
