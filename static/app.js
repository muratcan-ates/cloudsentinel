/* CloudSentinel ledger — fetches /anomalies and /costs/summary and typesets the panels.
   The full agent chain runs live: section III triages with the Analyst and
   files Recommender proposals, section IV is the real HITL inbox
   (approve / reject / simulated execute against /actions), and section V
   keeps the audit trail. Nothing ever executes without an operator decision,
   and execution is simulated by design. */

/* ======================================================================
   CONTENTS
   01 · config & state — themes, demo narratives, the app state
   02 · dom lookups — the fixed elements every section shares
   03 · small utils — escapeHtml, formatters, fetch/post, shared builders
        · motion — the one governor every moving thing answers to
   04 · svg helpers — precise static ink shared by every chart
   05 · charts — daily trend + the detection backtest bars
   06 · radar — the pixel-radar centerpiece
   07 · watch room — summary cards, anomalies, costs, unified watch (I–II)
   08 · investigation — signal rail, evidence pack, agent verbs (III)
   09 · decision desk — HITL inbox and the reflex/conscious split (IV)
   10 · ledger & audit — the persisted decision trail (V)
   11 · intelligence & handover — /analytics aggregates, print brief (VI)
   12 · brain room — insights, routines, runbooks, identity
   13 · agent feed — the live right rail
   14 · scan, pulse & health — the app verbs that refresh everything
   15 · tour & routing — guided tour, room navigation, permalinks
   16 · events & boot — every listener and imperative step, in load order

   Only definitions moved: top-level constants and function declarations
   hoist safely, so they are grouped by feature. Every imperative
   statement (listener registration, interval, immediate call) kept its
   original relative execution order inside section 16.
   ====================================================================== */

/* ======================================================================
   01 · CONFIG & STATE
   ====================================================================== */

/* Palette: ?theme=mission|paper|horizon|dawn still wins so review links keep
   working; otherwise the choice persisted from the colophon switch applies.
   The default identity stays horizon — the switch promotes night (mission)
   and paper from hidden preview flags to first-class modes. */
const THEMES = ["horizon", "mission", "paper", "dawn", "vivid"];

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  markPressed("[data-theme-choice]", "themeChoice", theme);
}

/* Pre-analysis placeholder for section III: shown only until the Analyst
   runs on a signal; live agent output replaces it. */
const detailsByService = {
  compute: {
    asset: "prod-api-cluster / i-0a9c2",
    reason: "The compute bill rose without a comparable increase in request volume. Idle capacity is the most likely immediate driver.",
    security: "No public exposure or identity-policy change was found in the current demo signal set.",
    proposal: "Right-size the overprovisioned production node group from 8 to 5 instances during the next low-traffic window.",
    savings: "$428 / month",
    risk: "medium",
    rollback: "available — restore the previous desired capacity",
    confidence: 87,
  },
  database: {
    asset: "orders-db / primary cluster",
    reason: "Database spend exceeded its baseline while connection counts stayed steady — consistent with a tier change or inefficient storage configuration.",
    security: "The demo policy review found one broad read role; it should be narrowed before applying infrastructure changes.",
    proposal: "Review the last parameter-group change, then move the idle read replica to a lower tier after a maintenance-window check.",
    savings: "$315 / month",
    risk: "high",
    rollback: "available — restore the original replica class within the approved change window",
    confidence: 82,
  },
  storage: {
    asset: "archive-bucket / retention policy",
    reason: "Object growth is higher than its seasonal baseline, with a large share of files outside the required access window.",
    security: "No access-control anomaly is linked to this spend signal.",
    proposal: "Apply the reviewed lifecycle rule to move eligible objects to a lower-cost archival class.",
    savings: "$92 / month",
    risk: "low",
    rollback: "available — restore the original storage class for selected objects",
    confidence: 78,
  },
  network: {
    asset: "egress-gateway / prod",
    reason: "Outbound transfer increased above the baseline; the destination breakdown requires verification before any blocking action.",
    security: "A destination review is recommended before a routing or firewall change is proposed.",
    proposal: "Request deeper destination analysis and compare CDN routing alternatives before making a configuration change.",
    savings: "$74 / month",
    risk: "medium",
    rollback: "not applicable — analysis only",
    confidence: 72,
  },
};

const state = {
  anomalies: [],
  allAnomalies: [], // unfiltered set — feeds the all-services trend marks
  costs: null,
  daily: null,
  sortMode: "cost",
  anomalySort: "z", // z | date | service — orders section I and the signal rail
  lastScan: null, // last successful /anomalies report — re-renders on sort changes
  selectedIndex: 0,
  analyses: new Map(), // event id → Analyst agent report; survives re-renders
  analystBusy: new Set(), // event ids with an analyze request in flight
  recommendBusy: new Set(), // event ids with a recommend request in flight
  hitlBusy: new Set(), // action ids with a decision request in flight
  actions: [], // live HITL actions from GET /actions — feeds section IV
  knownActionIds: null, // ids this session has seen — null until the first inbox load
  freshActionIds: new Set(), // ids that just appeared — their cards enter with a bloom
  analytics: null, // GET /analytics/decisions — funnel, quality, telemetry (section VI)
  trend: null, // GET /analytics/costs/trend — window-over-window comparison (section VI)
  intelStale: false, // last intelligence fetch failed — section VI must say so
  aiUsage: null, // GET /analytics/ai — self-FinOps quota strip (section VI)
  forecast: null, // GET /analytics/costs/forecast — month-end line (section II)
  security: null, // GET /security/signals — unified watch strip (section I)
  fraud: null, // GET /fraud/signals — unified watch strip (section I)
  watchStale: false, // last watch fetch failed on at least one lane
  whatif: new Map(), // action id → /analytics/whatif — decision-moment numbers
  calibration: null, // GET /analytics/calibration — confidence vs verdicts (VI)
  headline: null, // GET /analytics/headline — one-line jury brief (copy button)
  roi: null, // GET /analytics/roi — realized vs estimated savings (section VI)
  detection: null, // GET /metrics/detection — detector precision from verdicts (VI)
  reflexSuggestions: null, // GET /reflex/suggestions — learned reflex candidates (VI)
  env: "local", // deploy environment from /health — drives the LIVE banner
  provider: "fake", // GET /health provider — fake (dormant Gemini) vs live
  readonly: false, // SENTINEL_READONLY showcase mode — writes are disabled
  dataSources: {}, // GET /health data_sources — lane → mock | self | feed
  auditExpanded: false, // section V shows the newest entries until asked
  audit: [
    { time: "ledger", title: "Loading the decision ledger…", copy: "Operator verdicts, persisted across restarts, appear here on load." },
  ],
};

/* ======================================================================
   02 · DOM LOOKUPS
   ====================================================================== */

const thresholdInput = document.getElementById("threshold");
const thresholdValue = document.getElementById("threshold-value");
const serviceFilter = document.getElementById("service-filter");
const rescanButton = document.getElementById("rescan");
const pulseButton = document.getElementById("pulse-run");
const operatorInput = document.getElementById("operator-name");
const pulseNote = document.getElementById("pulse-note");
const editionLine = document.getElementById("chip-system");
const anomalyList = document.getElementById("anomaly-list");
const costBars = document.getElementById("cost-bars");
const signalRail = document.getElementById("signal-rail");
const invDetail = document.getElementById("inv-detail");
const decisionList = document.getElementById("decision-list");
const auditList = document.getElementById("audit-list");
const feedToggle = document.getElementById("feed-toggle");
const feedBody = document.getElementById("feed-body");
const feedList = document.getElementById("feed-list");
const feedEmpty = document.getElementById("feed-empty");
const runbookInput = document.getElementById("runbook-query");

/* ======================================================================
   03 · SMALL UTILS — formatters, fetch helpers, shared builders
   ====================================================================== */

const fmtNumber = (value) =>
  value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

/* ----------------------------------------------------------------------
   MOTION — one governor, one animation frame, nothing left spinning

   Everything on this page that moves (the radar beam, the trend drawing
   itself in, the backtest bars growing, every rolling figure, the tape's
   ticking rates) runs through here. The rules it enforces, once, for all
   of them:

   · Two switches can veto motion — the operating system's
     `prefers-reduced-motion` and the accessibility panel's own
     `[data-a11y-motion="off"]`. Either one and an animation never starts;
     flip one mid-flight and every running animation is settled to its
     finished frame immediately. "Motion off" and "motion finished" are
     deliberately the same DOM, so nothing is ever left half-drawn.
   · One requestAnimationFrame drives the lot, and it only exists while
     something is actually moving. No setInterval anywhere near a pixel:
     a background tab, a hidden room or a discarded chart cannot leave a
     loop running, because the loop stops the moment its register empties
     and every animator drops itself when its element leaves the document.
   · Animations are named. Starting "trend-reveal" twice cannot produce
     two of them fighting over the same path.
   ---------------------------------------------------------------------- */

/* The single question every animation asks before it starts, and that the
   pump re-asks on every frame. The panel writes `data-a11y-motion="off"`
   on <html>; the media query is the operating system's own answer. */
function motionAllowed() {
  return !REDUCED_MOTION.matches && document.documentElement.dataset.a11yMotion !== "off";
}

const animators = new Map(); // name → { tick(now) → false when finished, settle() }
let motionFrame = 0;

function pumpMotion(now) {
  motionFrame = 0;
  if (!motionAllowed()) return settleAllMotion();
  for (const [name, animator] of [...animators]) {
    let keep = false;
    try {
      keep = animator.tick(now) !== false;
    } catch {
      keep = false; // a broken animator is dropped, never repeated 60× a second
    }
    if (!keep) animators.delete(name);
  }
  if (animators.size) motionFrame = requestAnimationFrame(pumpMotion);
}

/* Start (or restart) a named animation. Returns false — having already
   settled the element to its finished state — when motion is not allowed
   or the tab is in the background. */
function animate(name, animator) {
  stopMotion(name);
  if (!motionAllowed() || document.hidden) {
    animator.settle?.();
    return false;
  }
  animators.set(name, animator);
  if (!motionFrame) motionFrame = requestAnimationFrame(pumpMotion);
  return true;
}

function stopMotion(name) {
  const animator = animators.get(name);
  if (!animator) return;
  animators.delete(name);
  animator.settle?.();
}

function settleAllMotion() {
  for (const name of [...animators.keys()]) stopMotion(name);
  if (motionFrame) cancelAnimationFrame(motionFrame);
  motionFrame = 0;
}

/* An element inside a closed room is not worth animating: the rooms are
   display:none, so the animation would be a private performance. */
function inClosedRoom(el) {
  return Boolean(el?.closest?.(".view-hidden"));
}

const easeOutCubic = (t) => 1 - (1 - t) ** 3;
/* A small overshoot before settling — what makes a marker "pop" instead
   of merely appearing. The cubic coefficient is the quadratic one plus 1,
   which is what pins f(0) to exactly 0 (no jump off the start) while the
   peak stays around 1.05× — a nudge, not a bounce. */
const easeOutBack = (t) => 1 + 2.1 * (t - 1) ** 3 + 1.1 * (t - 1) ** 2;

/* A one-shot tween. `frame(progress)` paints 0→1; returning false from it
   aborts (the element was replaced by a repaint). `settle()` is the
   finished frame — the pump calls it at the end, the governor calls it
   instead of ever starting when motion is off. */
function tween(name, duration, frame, settle) {
  const started = performance.now();
  return animate(name, {
    settle,
    tick: (now) => {
      const t = Math.min(1, (now - started) / duration);
      if (frame(t) === false) return false;
      if (t < 1) return true;
      settle();
      return false;
    },
  });
}

/* Ambient motion: the loops that should simply be running whenever the
   page is visible and motion is allowed. Re-entered from the a11y switch,
   from a tab coming back to the foreground, and from the OS media query. */
function resumeAmbientMotion() {
  startRadarSweep();
}

function syncMotion() {
  if (motionAllowed() && !document.hidden) resumeAmbientMotion();
  else settleAllMotion();
}

/* Polling is data, not decoration, so it is NOT gated on the motion
   switch — but it is still driven by requestAnimationFrame rather than
   setInterval, which buys the right behaviour for free: a backgrounded
   tab stops asking instead of queueing a minute of missed requests, and
   resumes the moment it is looked at again. */
const rafTimers = new Map();

function rafDelay(name, delayMs, run) {
  cancelRafDelay(name);
  const due = performance.now() + delayMs;
  const handle = { id: 0 };
  const step = (now) => {
    if (rafTimers.get(name) !== handle) return; // superseded
    if (now < due) {
      handle.id = requestAnimationFrame(step);
      return;
    }
    rafTimers.delete(name);
    run();
  };
  rafTimers.set(name, handle);
  handle.id = requestAnimationFrame(step);
}

function cancelRafDelay(name) {
  const handle = rafTimers.get(name);
  if (!handle) return;
  cancelAnimationFrame(handle.id);
  rafTimers.delete(name);
}

/* Living figures: numbers ROLL to their new value instead of snapping —
   the page moves because the data moves. Keyed by the element's id (or an
   explicit data-roll-key) so a second update mid-roll replaces the first
   instead of racing it. Falls back to a plain set under either motion
   switch, or on the very first paint of a figure. */
let rollSequence = 0;

function rollFigure(el, value, render) {
  if (!el) return;
  // a figure with neither an id nor an explicit key gets one, so two
  // anonymous figures can never share a name and cancel each other
  if (!el.id && !el.dataset.rollKey) el.dataset.rollKey = `fig-${(rollSequence += 1)}`;
  const key = `roll:${el.id || el.dataset.rollKey}`;
  stopMotion(key);
  const previous = Number(el.dataset.v);
  el.dataset.v = String(value);
  const settle = () => {
    el.innerHTML = render(value);
  };
  if (Number.isNaN(previous) || previous === value) {
    settle();
    return;
  }
  tween(
    key,
    550,
    (t) => {
      if (!el.isConnected) return false;
      el.innerHTML = render(previous + (value - previous) * easeOutCubic(t));
    },
    settle
  );
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>'"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[ch]));

const utcNow = () =>
  new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";

/* The edition line's data badge tells the truth per lane: "MOCK DATA" only
   while every lane serves its bundled fixture; live lanes are named
   (self = the app's own telemetry, feed = external URL). A lane whose feed
   fell back reports "mock (feed unavailable)" — that is mock, not live. */
function dataBadge() {
  const entries = Object.entries(state.dataSources || {});
  const sim = entries
    .filter(([, source]) => String(source).startsWith("sim"))
    .map(([lane, source]) => `${lane}: ${source}`);
  const live = entries
    .filter(
      ([, source]) =>
        source && !String(source).startsWith("mock") && !String(source).startsWith("sim")
    )
    .map(([lane, source]) => `${lane}: ${source}`);
  const parts = [];
  if (live.length) parts.push(`LIVE DATA (${live.join(", ")})`);
  // sim is deliberately NOT "live data": the badge says what it is
  if (sim.length) parts.push(`SIMULATED LIVE (${sim.join(", ")})`);
  return parts.length ? parts.join(" — ") : "MOCK DATA";
}

/* "4d ago" for a YYYY-MM-DD — relative context without touching the data. */
function daysAgo(dateStr) {
  const then = new Date(`${dateStr}T00:00:00Z`);
  if (Number.isNaN(then.getTime())) return "";
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "1d ago" : `${days}d ago`;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

/* POST a JSON body and return the raw Response — callers keep their own
   status handling (409 name conflicts, 403 read-only) exactly as before. */
const postJson = (url, body) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

function detailFor(service) {
  return detailsByService[String(service || "").trim().toLowerCase()] || detailsByService.network;
}

const anomalyComparators = {
  z: (a, b) => b.z_score - a.z_score,
  date: (a, b) => a.date.localeCompare(b.date) || b.z_score - a.z_score,
  service: (a, b) => a.service.localeCompare(b.service) || b.z_score - a.z_score,
};

/* Sorts in place: report.anomalies and state.anomalies are the same array,
   so the investigate indexes stay honest across re-renders. */
function sortAnomalies() {
  state.anomalies.sort(anomalyComparators[state.anomalySort] || anomalyComparators.z);
}

/* aria-pressed radio behavior for a row of toggle buttons — the theme
   switch, the anomaly sort row and the cost sort row share the idiom. */
function markPressed(selector, datasetKey, value) {
  document.querySelectorAll(selector).forEach((button) =>
    button.setAttribute("aria-pressed", String(button.dataset[datasetKey] === value))
  );
}

/* Scannable stat chips — one builder for the anomaly meta row, the cost
   meta row and the decision-split strip. */
const statChip = (html) => `<span class="stat-chip">${html}</span>`;
const chipStrong = (value) => `<span class="chip-strong">${value}</span>`;

/* The shared <details class="transcript"> fold: one scaffold for the
   skeptic transcript, the orchestration trace and the decision memory. */
const buildFold = (summary, innerHtml) =>
  `<details class="transcript"><summary>${summary}</summary>${innerHtml}</details>`;

/* Empty-state line for the brain-room lists — one `<li class="meta">` note. */
function listPlaceholder(list, text) {
  const li = document.createElement("li");
  li.className = "meta";
  li.textContent = text;
  list.appendChild(li);
}

/* One list row for the routine panels: a text label followed by small
   head-action verbs — the shape the suggestion and saved lists share. */
function listRow(list, labelText, actions) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  label.textContent = labelText;
  li.appendChild(label);
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "head-action";
    button.textContent = action.label;
    if (action.title) button.title = action.title;
    button.addEventListener("click", action.onClick);
    li.appendChild(button);
  }
  list.appendChild(li);
}

/* Every ledger entry lands the same way: newest first, stamped now. */
function auditNote(title, copy) {
  state.audit.unshift({ time: utcNow(), title, copy });
}

/* ======================================================================
   04 · SVG HELPERS (precise static ink)
   ====================================================================== */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text != null) el.textContent = text;
  return el;
}

const fmtShort = (value) =>
  Math.abs(value) >= 1000 ? `$${(value / 1000).toFixed(1)}k` : `$${Math.round(value)}`;

/* Round tick steps to 1/2/5 × 10^n so axis labels read as human numbers. */
function niceTicks(min, max, count = 3) {
  if (min === max) { min -= 1; max += 1; }
  const rawStep = (max - min) / count;
  const power = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const error = rawStep / power;
  const step = power * (error >= 7.5 ? 10 : error >= 3.5 ? 5 : error >= 1.5 ? 2 : 1);
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step / 1e6; v += step) {
    ticks.push(v);
  }
  return ticks;
}

function buildScale(values, width, height, { left, right, top, bottom }) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const innerWidth = width - left - right;
  return {
    min,
    max,
    x: (index) =>
      left + (values.length > 1 ? (index * innerWidth) / (values.length - 1) : innerWidth / 2),
    y: (value) => height - bottom - ((value - min) / range) * (height - top - bottom),
  };
}

/* Monotone cubic segments (Fritsch–Carlson tangents): the curve is smooth
   but never overshoots the data, so a spike still reads as a spike and no
   dip is invented between two flat days — precision before prettiness. */
function smoothPath(points) {
  if (points.length < 3) {
    return points
      .map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`)
      .join(" ");
  }
  const count = points.length;
  const dx = [];
  const slope = [];
  for (let i = 0; i < count - 1; i += 1) {
    dx.push(points[i + 1].x - points[i].x);
    slope.push((points[i + 1].y - points[i].y) / dx[i]);
  }
  const tangent = [slope[0]];
  for (let i = 1; i < count - 1; i += 1) {
    if (slope[i - 1] * slope[i] <= 0) {
      tangent.push(0); // local extremum: flatten so the curve stays inside the data
    } else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      tangent.push((w1 + w2) / (w1 / slope[i - 1] + w2 / slope[i]));
    }
  }
  tangent.push(slope[count - 2]);
  let d = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
  for (let i = 0; i < count - 1; i += 1) {
    const h = dx[i] / 3;
    d +=
      ` C${(points[i].x + h).toFixed(1)},${(points[i].y + tangent[i] * h).toFixed(1)}` +
      ` ${(points[i + 1].x - h).toFixed(1)},${(points[i + 1].y - tangent[i + 1] * h).toFixed(1)}` +
      ` ${points[i + 1].x.toFixed(1)},${points[i + 1].y.toFixed(1)}`;
  }
  return d;
}

function drawEmptyChart(svg, width, height) {
  svg.replaceChildren(
    svgEl(
      "text",
      { class: "tick-label", x: (width / 2).toFixed(1), y: (height / 2).toFixed(1), "text-anchor": "middle" },
      "not enough data to draw"
    )
  );
  return { points: [], scale: null };
}

/* One renderer for both charts:
   - axes: y ticks + hairline grid + $ labels, sparse x date labels (trend)
   - band: mean ± sigma envelope + dashed baseline (sparkline) */
function drawSeries(svg, values, { spikes = [], area = false, axes = null, band = null } = {}) {
  svg.replaceChildren();
  const [, , width, height] = svg.getAttribute("viewBox").split(" ").map(Number);
  if (!values || values.length < 2) return drawEmptyChart(svg, width, height);

  const pad = axes
    ? { left: 40, right: 8, top: 10, bottom: 18 }
    : { left: 6, right: 6, top: 8, bottom: 8 };
  const scale = buildScale(values, width, height, pad);

  if (axes) {
    for (const tick of niceTicks(scale.min, scale.max)) {
      const y = scale.y(tick).toFixed(1);
      svg.append(
        svgEl("line", { class: "grid", x1: pad.left, x2: width - pad.right, y1: y, y2: y }),
        svgEl("text", { class: "tick-label", x: pad.left - 6, y: Number(y) + 3, "text-anchor": "end" }, fmtShort(tick))
      );
    }
    const dateCount = axes.dates.length;
    const labelIndexes = [...new Set([0, Math.round((dateCount - 1) / 3), Math.round(((dateCount - 1) * 2) / 3), dateCount - 1])];
    for (const index of labelIndexes) {
      svg.append(
        svgEl(
          "text",
          { class: "tick-label", x: scale.x(index).toFixed(1), y: height - 4, "text-anchor": index === 0 ? "start" : index === dateCount - 1 ? "end" : "middle" },
          axes.dates[index].slice(5)
        )
      );
    }
  }

  if (band) {
    const topY = scale.y(Math.min(band.mean + band.sigma, scale.max));
    const bottomY = scale.y(Math.max(band.mean - band.sigma, scale.min));
    svg.append(
      svgEl("rect", { class: "band", x: pad.left, width: width - pad.left - pad.right, y: topY.toFixed(1), height: Math.max(bottomY - topY, 0).toFixed(1) }),
      svgEl("line", { class: "baseline", x1: pad.left, x2: width - pad.right, y1: scale.y(band.mean).toFixed(1), y2: scale.y(band.mean).toFixed(1) })
    );
  }

  const points = values.map((value, index) => ({ x: scale.x(index), y: scale.y(value) }));
  const path = smoothPath(points);
  let areaEl = null;

  if (area) {
    const defs = svgEl("defs", {});
    const gradient = svgEl("linearGradient", { id: "trend-fill", x1: 0, y1: 0, x2: 0, y2: 1 });
    // presentation attributes, not inline styles: the palette variable
    // resolves the same way and nothing here writes to style=""
    gradient.append(
      svgEl("stop", { offset: "0", "stop-color": "var(--chart-area-hi)" }),
      svgEl("stop", { offset: "1", "stop-color": "var(--chart-area-lo)" })
    );
    defs.append(gradient);
    const floor = height - pad.bottom;
    areaEl = svgEl("path", { class: "area", d: `${path} L ${points[points.length - 1].x.toFixed(1)},${floor} L ${points[0].x.toFixed(1)},${floor} Z` });
    svg.append(defs, areaEl);
  }

  const lineEl = svgEl("path", { class: "line", d: path });
  svg.append(lineEl);
  // Handed back so the caller can draw the series in: the reveal animates
  // these exact nodes rather than repainting the chart 60 times a second.
  const marks = [];
  const lastIndex = Math.max(1, points.length - 1);
  for (const spike of spikes) {
    const point = points[spike.index];
    if (point) {
      const el = svgEl("circle", { class: `spike ${spike.severity}`, cx: point.x.toFixed(1), cy: point.y.toFixed(1), r: SPIKE_R });
      svg.append(el);
      // squeezed into [0, 1 − POP_WINDOW] so even a marker on the very last
      // day finishes its pop inside the draw instead of snapping at the end
      marks.push({ el, at: (spike.index / lastIndex) * (1 - POP_WINDOW) });
    }
  }
  return { points, scale, line: lineEl, area: areaEl, marks };
}

/* The series draws itself in: the stroke is revealed left to right, the
   area fades up behind it, and each anomaly marker pops the moment the
   drawing front reaches its day — so the eye reads the shape in the order
   the estate lived it, and the marks land as events rather than as dots
   that were always there. */
const SPIKE_R = 3.5;
const DRAW_MS = 760;
const POP_WINDOW = 0.16; // fraction of the draw a single marker takes to pop

function revealSeries(name, { line, area, marks }) {
  if (!line) return;
  const settle = () => {
    line.removeAttribute("stroke-dasharray");
    line.removeAttribute("stroke-dashoffset");
    if (area) area.removeAttribute("opacity");
    for (const mark of marks) mark.el.setAttribute("r", String(SPIKE_R));
  };
  let length = 0;
  try {
    length = inClosedRoom(line) ? 0 : line.getTotalLength();
  } catch {
    length = 0; // detached or unmeasurable — show it finished
  }
  if (!length) {
    settle();
    return;
  }
  line.setAttribute("stroke-dasharray", length.toFixed(1));
  line.setAttribute("stroke-dashoffset", length.toFixed(1));
  if (area) area.setAttribute("opacity", "0");
  for (const mark of marks) mark.el.setAttribute("r", "0");
  tween(
    name,
    DRAW_MS,
    (t) => {
      if (!line.isConnected) return false; // a repaint replaced the chart
      const front = easeOutCubic(t);
      line.setAttribute("stroke-dashoffset", (length * (1 - front)).toFixed(1));
      if (area) area.setAttribute("opacity", (front * front).toFixed(3));
      for (const mark of marks) {
        const p = (front - mark.at) / POP_WINDOW;
        const grown = p <= 0 ? 0 : p >= 1 ? 1 : easeOutBack(p);
        mark.el.setAttribute("r", (SPIKE_R * grown).toFixed(2));
      }
    },
    settle
  );
}

/* Grouped bars on a fixed 0→1 scale (precision/recall live there): hairline
   grid, one <rect> per bar, the exact value printed above each cap — the
   measurement is the ornament. A null value prints "—" instead of a bar.
   groups: [{label, bars: [{cls, value, note, title}]}] */
function drawGroupedBars(svg, groups, { grow = false } = {}) {
  svg.replaceChildren();
  const columns = []; // {el, y, height} — the caps the grow-in animates to
  const [, , width, height] = svg.getAttribute("viewBox").split(" ").map(Number);
  const pad = { left: 34, right: 8, top: 14, bottom: 18 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const yFor = (value) => pad.top + (1 - value) * innerHeight;
  for (const tick of [0, 0.5, 1]) {
    const y = yFor(tick).toFixed(1);
    svg.append(
      svgEl("line", { class: "grid", x1: pad.left, x2: width - pad.right, y1: y, y2: y }),
      svgEl("text", { class: "tick-label", x: pad.left - 6, y: Number(y) + 3, "text-anchor": "end" }, tick.toFixed(1))
    );
  }
  const groupWidth = innerWidth / (groups.length || 1);
  const barGap = 6;
  const captions = []; // appended after every bar so no column paints over a label
  groups.forEach((group, groupIndex) => {
    const barCount = group.bars.length || 1;
    const barWidth = Math.min(26, (groupWidth - barGap * (barCount + 1)) / barCount);
    const rowWidth = barCount * barWidth + (barCount - 1) * barGap;
    const start = pad.left + groupIndex * groupWidth + (groupWidth - rowWidth) / 2;
    group.bars.forEach((bar, barIndex) => {
      const x = start + barIndex * (barWidth + barGap);
      const center = x + barWidth / 2;
      if (bar.value == null) {
        captions.push(
          svgEl("text", { class: "tick-label", x: center.toFixed(1), y: (yFor(0) - 4).toFixed(1), "text-anchor": "middle" }, "—")
        );
        return;
      }
      const y = yFor(bar.value);
      // "bt-bar", not "bar": the cost ledger's global .bar rule (height: 2px)
      // would override the rect's geometry attribute in SVG2
      const rect = svgEl("rect", {
        class: `bt-bar ${bar.cls}`,
        x: x.toFixed(1),
        y: y.toFixed(1),
        width: barWidth.toFixed(1),
        height: Math.max(yFor(0) - y, 1).toFixed(1),
      });
      if (bar.title) rect.append(svgEl("title", {}, bar.title));
      svg.append(rect);
      columns.push({ el: rect, y, height: Math.max(yFor(0) - y, 1) });
      // two stacked lines — a combined "0.50 · FN 1" caption is wider than
      // the bar pitch and collided with its neighbor's when values were close
      captions.push(
        svgEl("text", { class: "tick-label bar-value", x: center.toFixed(1), y: (y - 4).toFixed(1), "text-anchor": "middle" }, bar.value.toFixed(2))
      );
      if (bar.note) {
        captions.push(
          svgEl("text", { class: "tick-label bar-value", x: center.toFixed(1), y: (y - 14).toFixed(1), "text-anchor": "middle" }, bar.note)
        );
      }
    });
    captions.push(
      svgEl(
        "text",
        { class: "tick-label group-label", x: (pad.left + groupIndex * groupWidth + groupWidth / 2).toFixed(1), y: height - 4, "text-anchor": "middle" },
        group.label
      )
    );
  });
  svg.append(...captions);
  if (grow) growBars(columns, yFor(0));
}

/* The measurement arrives: bars rise out of the zero line left to right,
   so a re-measured backtest reads as a fresh reading rather than as a
   silent swap of one static picture for another. The captions do not
   move — the numbers are the point, and a sliding number is unreadable. */
function growBars(columns, floor) {
  if (!columns.length || inClosedRoom(columns[0].el)) return;
  const settle = () => {
    for (const column of columns) {
      column.el.setAttribute("y", column.y.toFixed(1));
      column.el.setAttribute("height", column.height.toFixed(1));
    }
  };
  // stagger small enough that the last bar still has most of the window
  const stagger = Math.min(0.5 / columns.length, 0.05);
  const span = 1 - stagger * (columns.length - 1); // not `window` — that name is taken
  for (const column of columns) {
    column.el.setAttribute("y", (floor - 1).toFixed(1));
    column.el.setAttribute("height", "1");
  }
  tween(
    "backtest-grow",
    620,
    (t) => {
      if (!columns[0].el.isConnected) return false; // redrawn under us
      columns.forEach((column, index) => {
        const grown = easeOutCubic(
          Math.max(0, Math.min(1, (t - index * stagger) / span))
        );
        const height = Math.max(1, column.height * grown);
        column.el.setAttribute("y", (floor - height).toFixed(1));
        column.el.setAttribute("height", height.toFixed(1));
      });
    },
    settle
  );
}

/* ======================================================================
   05 · CHARTS — daily trend + detection backtest
   ====================================================================== */

/* The trend redraws on every ten-second scan and on every tape frame, so
   the draw-in is deliberately NOT tied to "a render happened": a chart
   that re-animates twice a minute is a distraction, not a signal. It
   draws in when the reader arrives at it, when a hand asks for a refresh
   (rescan, pulse, sensitivity, service), and when the series itself is a
   different shape — a new day, a new window, a new panel width. */
let trendShape = "";
let trendWantsReveal = true;

function markTrendForReveal() {
  trendWantsReveal = true;
}

function renderTrend() {
  const svg = document.getElementById("cost-trend");
  const readout = document.getElementById("trend-readout");
  const note = document.getElementById("trend-note");
  if (!state.daily || state.daily.totals.length === 0) {
    svg.replaceChildren();
    svg.onmousemove = null;
    svg.onmouseleave = null;
    readout.textContent = "—";
    note.textContent = "";
    trendShape = "";
    return;
  }
  const { dates, totals, currency } = state.daily;
  // Match the viewBox to the real pixel box so preserveAspectRatio="none" maps
  // 1:1 — the old fixed 460×132 box stretched the line, markers and labels
  // horizontally (the "squashed / janky" look). Recomputed on resize below.
  const boxW = Math.max(320, Math.round(svg.clientWidth || 460));
  const boxH = Math.max(120, Math.round(svg.clientHeight || 184));
  svg.setAttribute("viewBox", `0 0 ${boxW} ${boxH}`);
  // Dots come from the unfiltered anomaly set — the totals line always shows
  // all services, so its marks must too (the service filter only narrows
  // sections I/III/IV). Per-date max severity wins.
  const severityByDate = new Map();
  for (const anomaly of state.allAnomalies) {
    const current = severityByDate.get(anomaly.date);
    if (current !== "critical") severityByDate.set(anomaly.date, anomaly.severity);
  }
  const spikes = dates
    .map((date, index) => ({ index, severity: severityByDate.get(date) }))
    .filter((spike) => spike.severity);
  const series = drawSeries(svg, totals, { spikes, area: true, axes: { dates } });
  const { points } = series;

  const shape = `${dates[0]}→${dates[dates.length - 1]}·${dates.length}·${boxW}×${boxH}·${spikes.length}`;
  if (trendWantsReveal || shape !== trendShape) revealSeries("trend-reveal", series);
  trendShape = shape;
  trendWantsReveal = false;

  const peakIndex = totals.indexOf(Math.max(...totals));
  const low = Math.min(...totals);
  const defaultReadout = `peak ${dates[peakIndex]} — ${fmtNumber(totals[peakIndex])} ${currency}`;
  readout.textContent = defaultReadout;
  svg.setAttribute(
    "aria-label",
    `Daily total spend from ${dates[0]} to ${dates[dates.length - 1]}, ranging ` +
      `${fmtNumber(low)} to ${fmtNumber(totals[peakIndex])} ${currency}; ` +
      `${spikes.length} anomaly day${spikes.length === 1 ? "" : "s"} marked.`
  );

  const half = Math.floor(totals.length / 2);
  const firstHalf = totals.slice(0, half).reduce((sum, v) => sum + v, 0);
  const secondHalf = totals.slice(half).reduce((sum, v) => sum + v, 0);
  const delta = firstHalf ? ((secondHalf - firstHalf) / firstHalf) * 100 : 0;
  note.textContent = `spend ${delta >= 0 ? "rose" : "fell"} ${Math.abs(delta).toFixed(1)}% versus the first half of the period`;

  if (!points.length) return;
  const [, , viewWidth, viewHeight] = svg.getAttribute("viewBox").split(" ").map(Number);

  // On the simulated lane the last point is TODAY and it keeps moving. Its
  // drift is a few dollars against an axis that spans the estate's biggest
  // spike, so it needs a mark to be legible — but only the mark. The figure
  // itself lives on the run-rate line under the hero, where it cannot land
  // on the series or on an anomaly dot.
  if (String(state.dataSources?.costs || "").startsWith("sim")) {
    const last = points[points.length - 1];
    svg.append(svgEl("circle", { class: "live-point", cx: last.x, cy: last.y, r: 3.5 }));
  }
  const probe = svgEl("line", { class: "probe", x1: 0, x2: 0, y1: 10, y2: viewHeight - 18, visibility: "hidden" });
  const balloon = svgEl("g", { class: "balloon", visibility: "hidden" });
  const balloonRect = svgEl("rect", { width: 108, height: 34, x: 0, y: 0 });
  const balloonMain = svgEl("text", { x: 8, y: 14 });
  const balloonSub = svgEl("text", { class: "balloon-sub", x: 8, y: 27 });
  balloon.append(balloonRect, balloonMain, balloonSub);
  svg.append(probe, balloon);

  svg.onmousemove = (event) => {
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * viewWidth;
    let nearest = 0;
    points.forEach((p, i) => { if (Math.abs(p.x - x) < Math.abs(points[nearest].x - x)) nearest = i; });
    const point = points[nearest];
    probe.setAttribute("x1", point.x.toFixed(1));
    probe.setAttribute("x2", point.x.toFixed(1));
    probe.setAttribute("visibility", "visible");
    const severity = severityByDate.get(dates[nearest]);
    balloonMain.textContent = `${fmtNumber(totals[nearest])} ${currency}`;
    balloonSub.textContent = `${dates[nearest]}${severity ? ` · ${severity} anomaly` : ""}`;
    // Size the box to its widest line (measured, not guessed) so the label —
    // e.g. "2026-06-29 · critical anomaly" — never spills past the background,
    // then flip it left when the probe nears the right edge.
    const boxWidth = Math.max(
      balloonMain.getComputedTextLength(),
      balloonSub.getComputedTextLength()
    ) + 16;
    balloonRect.setAttribute("width", boxWidth.toFixed(1));
    const flip = point.x > viewWidth - (boxWidth + 16);
    const bx = flip ? point.x - (boxWidth + 8) : point.x + 8;
    const by = Math.max(10, Math.min(point.y - 17, viewHeight - 54));
    balloon.setAttribute("transform", `translate(${bx.toFixed(1)}, ${by.toFixed(1)})`);
    balloon.setAttribute("visibility", "visible");
  };
  svg.onmouseleave = () => {
    probe.setAttribute("visibility", "hidden");
    balloon.setAttribute("visibility", "hidden");
    readout.textContent = defaultReadout;
  };
}

/* Detection backtest: recall on planted ground truth, drawn as grouped bars —
   one group per scenario, one bar per detector mode, at the sensitivity the
   slider currently holds. Precision and FN ride each bar's tooltip, and the
   server's own caveat note (why MAD wins the contaminated baseline) is
   surfaced verbatim instead of being dropped. */
/* Two families, and the colours say so: the flat-baseline scorers in ink and
   turquoise, the trend-fitting ones in sky blue. Every scorer the API
   reports gets a bar — a mode missing here would vanish from the chart
   while the endpoint still measured it. */
const BACKTEST_MODES = [
  { mode: "zscore", cls: "zscore" },
  { mode: "mad", cls: "mad" },
  { mode: "zscore+loo", cls: "loo" },
  { mode: "residual", cls: "residual" },
  { mode: "residual+loo", cls: "residual-loo" },
];

let backtestSequence = 0; // last-writer-wins: a stale backtest must never overwrite a newer one
let backtestGroups = null; // last successful groups, redrawn on host resize without a refetch

/* Draw (or redraw) the cached groups with the viewBox matched 1:1 to the
   host's real pixel width — the same pattern the trend chart uses; a fixed
   460 box centered with gutters on desktop and letterboxed on phones. */
function drawBacktestChart(grow = false) {
  const host = document.getElementById("backtest-table");
  if (!host || !backtestGroups) return;
  let svg = host.querySelector("svg.backtest-svg");
  if (!svg) {
    svg = svgEl("svg", {
      class: "backtest-svg",
      role: "img",
      "aria-label":
        "Detection backtest — recall per scenario for z-score, MAD, " +
        "z-score with leave-one-out, the forecast-residual scorer and " +
        "residual with leave-one-out",
    });
    host.textContent = "";
    host.appendChild(svg);
  }
  const boxW = Math.max(320, Math.round(host.clientWidth || 460));
  svg.setAttribute("viewBox", `0 0 ${boxW} 150`);
  drawGroupedBars(svg, backtestGroups, { grow });
}

async function renderBacktest() {
  const host = document.getElementById("backtest-table");
  if (!host) return;
  const sequence = ++backtestSequence;
  try {
    const threshold = parseFloat(thresholdInput?.value) || 2;
    const data = await fetchJson(`/metrics/backtest?threshold=${threshold}`);
    if (sequence !== backtestSequence) return; // superseded by a newer slider move
    const rows = data.rows || [];
    const scenarios = [...new Set(rows.map((row) => row.scenario))];
    backtestGroups = scenarios.map((scenario) => ({
      label: scenario,
      bars: BACKTEST_MODES.flatMap(({ mode, cls }) => {
        const row = rows.find((r) => r.scenario === scenario && r.mode === mode);
        if (!row) return [];
        // the note line carries only the FN count — anything longer collides
        // with a neighboring caption at bar pitch; precision rides the tooltip
        return [{
          cls,
          value: row.recall,
          note: row.false_negatives > 0 ? `FN ${row.false_negatives}` : "",
          title:
            `${scenario} · ${mode} — precision ${row.precision ?? "—"}, ` +
            `recall ${row.recall ?? "—"}, false negatives ${row.false_negatives}`,
        }];
      }),
    }));
    drawBacktestChart(true); // a fresh measurement grows in; a resize redraw does not
    const legend = document.getElementById("backtest-legend");
    if (legend) legend.hidden = false;
    const note = document.getElementById("backtest-note");
    if (note) note.textContent = data.note ? `threshold ${data.threshold} — ${data.note}` : "";
  } catch {
    /* first load stays quiet (empty panel); a failed refetch marks the
       previously drawn chart as no longer current instead of lying */
    if (sequence !== backtestSequence) return;
    const note = document.getElementById("backtest-note");
    if (note && note.textContent && !note.textContent.startsWith("stale — ")) {
      note.textContent = `stale — last measured at ${note.textContent}`;
    }
  }
}

/* ======================================================================
   06 · SENTINEL RADAR
   ----------------------------------------------------------------------
   The moving centerpiece: a pixel radar whose blips ARE the current
   signals — cost anomalies in accent/alert, security in sky.

   The beam's angle is owned by this module, not by a stylesheet: it is one
   registered animation in the motion governor, so it obeys both motion
   switches, parks itself when the tab goes to the background, and cannot
   be left spinning by anything. That is also why the beam group carries no
   `radar-sweep` class — a CSS keyframe rotation on the same element would
   fight this loop, and the two would beat against each other.

   The contacts are the reason the beam exists. Each one remembers its
   bearing, and lights when the beam crosses it: a ring pings outward and
   the blip itself flares, then both decay over the next second and a bit.
   The radar is therefore reporting a fact — "the sweep just passed this
   signal" — rather than decorating the corner.
   ====================================================================== */

const RADAR_PERIOD_MS = 7000; // one full turn
const RADAR_DECAY_MS = 1500; // how long a contact stays lit behind the beam
const RADAR_BLIP = 6; // resting blip size, in viewBox units
/* Mirrors the fill rules the stylesheet gives `.radar-blip.critical`
   / `.warning` / `.security` — the ping ring is drawn by this module, so
   it names the same palette variables rather than inventing a colour. */
const RADAR_INK = {
  critical: "var(--alert)",
  warning: "var(--accent)",
  security: "var(--info)",
};

const radarContacts = []; // {deg, cx, cy, ping, blip}
let radarBeam = null;

function radarAngleDeg(name) {
  // deterministic bearing per service/date so blips hold their post
  let hash = 0;
  for (const ch of String(name)) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return hash;
}

/* energy: 1 the instant the beam crosses, decaying to 0. */
function paintContact(contact, energy) {
  const flare = RADAR_BLIP + energy * 5;
  contact.blip.setAttribute("x", (contact.cx - flare / 2).toFixed(1));
  contact.blip.setAttribute("y", (contact.cy - flare / 2).toFixed(1));
  contact.blip.setAttribute("width", flare.toFixed(1));
  contact.blip.setAttribute("height", flare.toFixed(1));
  contact.ping.setAttribute("r", energy ? (4 + (1 - energy) * 13).toFixed(1) : "0");
  contact.ping.setAttribute("opacity", (energy * 0.7).toFixed(3));
}

function settleRadar() {
  if (radarBeam) radarBeam.setAttribute("transform", "rotate(0 100 100)");
  for (const contact of radarContacts) paintContact(contact, 0);
}

function radarTick(now) {
  if (!radarBeam || !radarBeam.isConnected) return false;
  const turn = ((now % RADAR_PERIOD_MS) / RADAR_PERIOD_MS) * 360;
  radarBeam.setAttribute("transform", `rotate(${turn.toFixed(2)} 100 100)`);
  // the wedge's bright edge starts at twelve o'clock, which is −90° in the
  // same bearing convention the contacts use (0° = east, clockwise)
  const beamDeg = turn - 90;
  for (const contact of radarContacts) {
    const behind = (((beamDeg - contact.deg) % 360) + 360) % 360;
    const since = (behind / 360) * RADAR_PERIOD_MS;
    paintContact(contact, since < RADAR_DECAY_MS ? 1 - since / RADAR_DECAY_MS : 0);
  }
  return true;
}

function startRadarSweep() {
  if (!radarBeam || !radarBeam.isConnected) return;
  // a beam turning inside a room nobody is in is a leak, not a feature —
  // the ten-second scan re-renders the radar whichever room is open
  if (inClosedRoom(radarBeam)) return;
  if (animators.has("radar")) return; // already turning — never restart mid-sweep
  animate("radar", { tick: radarTick, settle: settleRadar });
}

function renderRadar() {
  const svg = document.getElementById("sentinel-radar");
  if (!svg) return;

  // The rings, cross-hair, beam and core are static ink — built ONCE. The
  // auto-scan calls this every ten seconds; rebuilding the whole SVG would
  // throw away the beam element mid-turn. Only the contacts re-render.
  let blipLayer = svg.querySelector("#radar-blips");
  if (!blipLayer) {
    svg.replaceChildren();
    const gradient = svgEl("linearGradient", { id: "sweep-grad", x1: 0, y1: 0, x2: 1, y2: 0 });
    gradient.append(
      svgEl("stop", { offset: "0", "stop-color": "var(--accent)", "stop-opacity": "0.38" }),
      svgEl("stop", { offset: "1", "stop-color": "var(--accent)", "stop-opacity": "0" })
    );
    const defs = svgEl("defs", {});
    defs.append(gradient);
    svg.append(defs);
    for (const r of [30, 58, 86]) {
      svg.append(svgEl("circle", { class: "ring", cx: 100, cy: 100, r }));
    }
    svg.append(
      svgEl("line", { class: "cross", x1: 100, y1: 10, x2: 100, y2: 190 }),
      svgEl("line", { class: "cross", x1: 10, y1: 100, x2: 190, y2: 100 })
    );
    radarBeam = svgEl("g", { class: "radar-beam", transform: "rotate(0 100 100)" });
    radarBeam.append(
      svgEl("path", { d: "M100,100 L100,12 A88,88 0 0 1 152,29 Z", fill: "url(#sweep-grad)" })
    );
    blipLayer = svgEl("g", { id: "radar-blips" });
    svg.append(
      radarBeam,
      blipLayer,
      svgEl("rect", { class: "radar-core", x: 97, y: 97, width: 6, height: 6 })
    );
  }

  const signals = [
    ...state.anomalies.map((anomaly) => ({
      bearing: anomaly.service + anomaly.date,
      severity: anomaly.severity,
      radius: anomaly.severity === "critical" ? 44 : 72,
    })),
    ...((state.security && state.security.signals) || []).map((signal) => ({
      bearing: signal.service + signal.date,
      severity: "security",
      radius: 86,
    })),
  ];

  radarContacts.length = 0;
  blipLayer.replaceChildren();
  for (const signal of signals) {
    const deg = radarAngleDeg(signal.bearing);
    const radians = (deg * Math.PI) / 180;
    const cx = 100 + Math.cos(radians) * signal.radius;
    const cy = 100 + Math.sin(radians) * signal.radius;
    const ping = svgEl("circle", {
      class: `radar-ping ${signal.severity}`,
      cx: cx.toFixed(1),
      cy: cy.toFixed(1),
      r: 0,
      fill: "none",
      stroke: RADAR_INK[signal.severity] || RADAR_INK.warning,
      "stroke-width": "1",
      opacity: 0,
    });
    const blip = svgEl("rect", { class: `radar-blip ${signal.severity}` });
    blipLayer.append(ping, blip);
    const contact = { deg, cx, cy, ping, blip };
    paintContact(contact, 0);
    radarContacts.push(contact);
  }

  startRadarSweep();
}

/* ======================================================================
   07 · WATCH ROOM — summary cards, anomalies, costs, unified watch
   ====================================================================== */

let watchSequence = 0; // last-writer-wins: stale watch responses never overwrite newer

async function loadWatch() {
  const sequence = ++watchSequence;
  // Independent lanes: a fraud-only failure must not discard a security
  // response that already succeeded (and vice versa).
  const [security, fraud] = await Promise.all([
    fetchJson("/security/signals").catch(() => null),
    fetchJson("/fraud/signals").catch(() => null),
  ]);
  if (sequence !== watchSequence) return;
  if (security) state.security = security;
  if (fraud) state.fraud = fraud;
  state.watchStale = !security || !fraud;
}

function renderWatch() {
  const securityBox = document.getElementById("security-watch");
  const fraudBox = document.getElementById("fraud-watch");
  const staleLine = document.getElementById("watch-stale");
  staleLine.textContent = state.watchStale
    ? "watch feed unreachable — showing the last successful signals"
    : "";

  if (!state.security) {
    securityBox.innerHTML = `<p class="meta watch-head">security — loads with the first scan</p>`;
  } else {
    const report = state.security;
    // cross-lane correlation: a login storm on a spend-spike day is one
    // story told by two lanes — the badge joins them by calendar date
    const costSpikeDates = new Set(state.allAnomalies.map((anomaly) => anomaly.date));
    securityBox.innerHTML =
      `<p class="meta watch-head">security — ${report.signal_count} signal${report.signal_count === 1 ? "" : "s"} · ${escapeHtml(report.metric)} · mission ${escapeHtml(report.mission ?? "—")}</p>` +
      report.signals
        .map(
          (signal) => `
      <div class="watch-row ${signal.severity === "critical" ? "critical" : ""}">
        <div class="watch-top">
          <span><span class="watch-glyph" aria-hidden="true">▣</span><span class="watch-strong">${escapeHtml(signal.service)}</span></span>
          <span class="watch-tag">${escapeHtml(signal.severity)}</span>
        </div>
        <p class="watch-detail">${escapeHtml(signal.date)} · ${fmtNumber(signal.count)} events vs ${fmtNumber(signal.baseline)} baseline · z ${signal.z_score.toFixed(2)}${costSpikeDates.has(signal.date) ? ` · <span class="watch-strong">⇄ cost spike same day</span>` : ""}</p>
      </div>`
        )
        .join("");
  }

  if (!state.fraud) {
    fraudBox.innerHTML = `<p class="meta watch-head">fraud — loads with the first scan</p>`;
    return;
  }
  const fraud = state.fraud;
  const flagged = fraud.signals.filter((signal) => signal.band !== "clear");
  const bands = fraud.bands || {};
  const bandLine =
    bands.hold_suggested != null
      ? ` · ${bands.hold_suggested} hold / ${bands.review} review / ${bands.clear} clear`
      : ` · ${fraud.count} flagged of ${fraud.signals.length} events`;
  fraudBox.innerHTML =
    `<p class="meta watch-head">fraud <span class="hint">(experimental lane)</span> — published rules${bandLine} · mission ${escapeHtml(fraud.mission ?? "—")} · suggestions only, the operator decides</p>` +
    flagged
      .map(
        (signal) => `
    <div class="watch-row ${signal.band === "hold_suggested" ? "critical" : ""}" title="${escapeHtml(signal.reasons.join(" · "))}">
      <div class="watch-top">
        <span><span class="watch-glyph" aria-hidden="true">▣</span><span class="watch-strong">${escapeHtml(signal.id)}</span> · ${fmtNumber(signal.amount)} USD</span>
        <span class="watch-tag">score ${signal.score} · ${escapeHtml(signal.band === "hold_suggested" ? "hold suggested" : signal.band)}</span>
      </div>
      <p class="watch-detail">${
        signal.rule_hits && signal.rule_hits.length
          ? escapeHtml(signal.rule_hits.map((hit) => `${hit.rule.replace("_", " ")} +${hit.points}`).join(" · "))
          : escapeHtml(signal.reasons.join(" · "))
      }</p>
    </div>`
      )
      .join("");
}

function renderSummary() {
  const pending = state.actions.filter((a) => a.state === "proposed").length;
  rollFigure(document.getElementById("sum-signals"), state.anomalies.length, (v) =>
    String(Math.round(v))
  );
  rollFigure(document.getElementById("sum-pending"), pending, (v) =>
    String(Math.round(v))
  );
  if (state.costs) {
    const currency = escapeHtml(state.costs.currency);
    rollFigure(
      document.getElementById("sum-total"),
      state.costs.total_cost,
      (v) => `${fmtNumber(v)} <small>${currency}</small>`
    );
    document.getElementById("sum-total-sub").textContent =
      `${state.costs.period.start} → ${state.costs.period.end}`;
  }
  if (state.analytics) {
    const currency = escapeHtml(state.costs ? state.costs.currency : "USD");
    rollFigure(
      document.getElementById("sum-value"),
      state.analytics.quality.approved_estimated_monthly_savings,
      (v) => `${fmtNumber(v)} <small>${currency} / mo</small>`
    );
  }
}

function renderAnomalies(report) {
  // scannable facts, not a sentence: each figure is its own chip
  const chips = [
    `${chipStrong(report.records_analyzed)} records`,
    `threshold ${chipStrong(report.threshold.toFixed(2))}`,
    `${chipStrong(report.anomaly_count)} cost`,
    state.security ? `${chipStrong(state.security.signal_count)} security` : null,
    state.fraud ? `${chipStrong(state.fraud.count)} fraud` : null,
    typeof report.reflex_ms === "number"
      ? `reflex ${chipStrong(report.reflex_ms.toFixed(1))} ms`
      : null,
    serviceFilter.value ? `service ${escapeHtml(serviceFilter.value)}` : null,
  ].filter(Boolean);
  document.getElementById("anomaly-meta").innerHTML = chips.map(statChip).join("");

  anomalyList.innerHTML = "";
  if (report.anomalies.length === 0) {
    anomalyList.innerHTML = `<p class="all-quiet">All quiet.</p>`;
    return new Set();
  }

  report.anomalies.forEach((anomaly, index) => {
    const entry = document.createElement("article");
    entry.className = `entry ${anomaly.severity}`;
    entry.innerHTML = `
      <span class="sq" aria-hidden="true"></span>
      <div>
        <p class="service">${escapeHtml(anomaly.service)}</p>
        <p class="date">${escapeHtml(anomaly.date)}${daysAgo(anomaly.date) ? ` · ${daysAgo(anomaly.date)}` : ""}</p>
        <p class="figures">${fmtNumber(anomaly.cost)} <span class="dim">vs baseline ${fmtNumber(anomaly.service_mean)}</span></p>
        ${anomaly.service_mean > 0
          ? `<p class="ratio-note">${(anomaly.cost / anomaly.service_mean).toFixed(1)}× the usual daily spend</p>`
          : ""}
      </div>
      <div class="entry-rail">
        <p class="z">${anomaly.z_score.toFixed(2)}</p>
        <p class="sev-word">${escapeHtml(anomaly.severity)}</p>
        <button class="row-action" type="button" data-investigate="${index}" aria-label="investigate ${escapeHtml(anomaly.service)} anomaly of ${escapeHtml(anomaly.date)}">investigate →</button>
      </div>`;
    anomalyList.appendChild(entry);
  });
  return new Set(report.anomalies.map((a) => a.service));
}

function renderCosts(report, flaggedServices) {
  document.getElementById("cost-meta").innerHTML =
    statChip(`${escapeHtml(report.period.start)} → ${escapeHtml(report.period.end)}`) +
    statChip(`${chipStrong(report.services.length)} services`);

  document.getElementById("total-cost").innerHTML =
    `${fmtNumber(report.total_cost)} <small>${escapeHtml(report.currency)}</small>`;

  costBars.innerHTML = "";
  const ordered =
    state.sortMode === "az"
      ? [...report.services].sort((a, b) => a.service.localeCompare(b.service))
      : [...report.services].sort((a, b) => b.total_cost - a.total_cost);
  const biggestSpend = Math.max(...report.services.map((s) => s.total_cost));
  ordered.forEach((service, index) => {
    const flagged = flaggedServices.has(service.service);
    const share = (service.share_of_total * 100).toFixed(1);
    const row = document.createElement("div");
    row.className = `cost-row${service.total_cost === biggestSpend ? " top-spender" : ""}`;
    row.innerHTML = `
      <div class="cost-line">
        <span class="idx">${String(index + 1).padStart(2, "0")}</span>
        <button class="service service-btn" type="button" data-filter-service="${escapeHtml(service.service)}"
          aria-pressed="${String(serviceFilter.value === service.service)}"
          title="focus the signal panels on ${escapeHtml(service.service)} — click again to clear">${escapeHtml(service.service)}${
          flagged
            ? '<span class="phantom-sq" aria-hidden="true"></span><span class="phantom-note">phantom traced</span>'
            : ""
        }</button>
        <span class="amount">${fmtNumber(service.total_cost)} <small>${escapeHtml(report.currency)}</small> <span class="share">· ${share}%</span></span>
        <span class="tape-chip" data-tape-chip="${escapeHtml(service.service)}" hidden></span>
      </div>
      <div class="bar"><div class="bar-fill" style="width:0%"></div></div>`;
    costBars.appendChild(row);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        row.querySelector(".bar-fill").style.width = `${share}%`;
      })
    );
  });
}

function populateServiceFilter() {
  if (!state.costs || serviceFilter.options.length > 1) return;
  for (const service of state.costs.services) {
    const option = document.createElement("option");
    option.value = service.service;
    option.textContent = service.service;
    serviceFilter.appendChild(option);
  }
  // apply a ?service= permalink once the options exist (one-shot)
  if (pendingServiceFilter) {
    const match = [...serviceFilter.options].some((o) => o.value === pendingServiceFilter);
    if (match) serviceFilter.value = pendingServiceFilter;
    pendingServiceFilter = null;
  }
}

/* ======================================================================
   08 · INVESTIGATION — signal rail, evidence pack, agent verbs
   ====================================================================== */

let sparkDrawn = ""; // which signal's evidence is currently on the sparkline

function renderInvestigation() {
  signalRail.innerHTML = "";
  if (state.anomalies.length === 0) {
    signalRail.innerHTML = `<p class="meta">no open signal at this sensitivity</p>`;
    invDetail.innerHTML = `<p class="all-quiet">Nothing to investigate — lower the sensitivity, or <button class="row-action" type="button" data-run-pulse>run Pulse →</button>.</p>`;
    return;
  }
  if (state.selectedIndex >= state.anomalies.length) state.selectedIndex = 0;

  state.anomalies.forEach((anomaly, index) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `signal-option row-action ${index === state.selectedIndex ? "is-selected" : ""}`;
    if (index === state.selectedIndex) option.setAttribute("aria-current", "true");
    option.dataset.selectSignal = String(index);
    option.innerHTML = `
      <span class="service">${escapeHtml(anomaly.service)}</span>
      <span class="sig-sub">${escapeHtml(anomaly.date)} · z ${anomaly.z_score.toFixed(2)} · ${escapeHtml(anomaly.severity)}</span>`;
    signalRail.appendChild(option);
  });

  const anomaly = state.anomalies[state.selectedIndex];
  const detail = detailFor(anomaly.service);
  const currency = state.costs ? state.costs.currency : "USD";
  const deviation = anomaly.cost - anomaly.service_mean;
  const action = actionForEvent(anomaly.id);
  const analysis = anomaly.id != null ? state.analyses.get(anomaly.id) : undefined;
  const analystTag = analysis
    ? analysis.source === "fallback"
      ? " — Analyst agent (fallback)"
      : `${analysis.from_cache ? " — Analyst agent · cached" : " — Analyst agent"}`
    : "";

  invDetail.innerHTML = `
    <header class="inv-head">
      <div>
        <p class="microcap inv-kicker">signal ${String(state.selectedIndex + 1).padStart(3, "0")} · ${escapeHtml(anomaly.severity)}${analysis ? ` · triage ${escapeHtml(analysis.triage)}` : ""}</p>
        <p class="inv-title">${escapeHtml(anomaly.service)} <em>cost anomaly</em></p>
        <p class="inv-asset">${escapeHtml(detail.asset)} · observed ${escapeHtml(anomaly.date)}</p>
      </div>
      <div class="confidence">
        <p class="conf-fig">${analysis ? Math.round(analysis.confidence.score * 100) : detail.confidence}<small>%</small></p>
        <p class="microcap">agent confidence${analysis && analysis.source === "fallback" ? " (fallback)" : ""}</p>
      </div>
    </header>

    <div class="evidence-row">
      <div class="evidence"><p class="microcap">Observed spend</p><p class="ev-fig">${fmtNumber(anomaly.cost)} <small>${escapeHtml(currency)}</small></p></div>
      <div class="evidence"><p class="microcap">Baseline</p><p class="ev-fig">${fmtNumber(anomaly.service_mean)}</p></div>
      <div class="evidence ${anomaly.severity === "critical" ? "critical" : ""}"><p class="microcap">Deviation — z ${anomaly.z_score.toFixed(2)}</p><p class="ev-fig">${deviation >= 0 ? "+" : "−"}${fmtNumber(Math.abs(deviation))}</p></div>
    </div>

    <div class="spark-block" id="spark-block" hidden>
      <p class="microcap">Fourteen-day evidence <span class="hint">— daily spend, ${escapeHtml(anomaly.service)}</span></p>
      <svg class="spark-svg" id="spark-svg" viewBox="0 0 460 64" preserveAspectRatio="none" role="img" aria-label="Daily spend for ${escapeHtml(anomaly.service)} with the anomaly day marked"></svg>
      <p class="meta" id="spark-stats"></p>
    </div>

    <div class="inv-columns">
      <div class="inv-block">
        <p class="microcap">What happened${escapeHtml(analystTag)}</p>
        <p class="body">${escapeHtml(analysis ? analysis.summary : detail.reason)}</p>
        ${analysis && analysis.evidence_ids.length
          ? `<p class="meta">cited evidence ${escapeHtml(analysis.evidence_ids.join(" · "))} — rows of the fourteen-day series</p>`
          : ""}
      </div>
      <div class="inv-block">
        <p class="microcap">${analysis ? "Probable cause" : "Security context"}</p>
        <p class="body">${escapeHtml(analysis ? analysis.probable_cause : detail.security)}</p>
        ${analysis ? `<p class="meta">${escapeHtml(analysis.confidence.rationale)}</p>` : ""}
      </div>
      ${renderRecommendationBlock(anomaly, action, analysis)}
    </div>

    <div class="inv-actions">
      <button class="row-action" type="button" data-request-evidence ${
        (anomaly.id != null && state.analystBusy.has(anomaly.id)) || state.readonly ? "disabled" : ""
      }${state.readonly ? ' title="read-only demo — the agent verbs are disabled; the analysis on this card is the one the watch already ran"' : ""}>${
        anomaly.id != null && state.analystBusy.has(anomaly.id)
          ? "analyst working…"
          : analysis ? "re-run analyst →" : "run analyst agent →"
      }</button>
      ${analysis && !action
        ? `<button class="row-action" type="button" data-request-recommend ${
            state.recommendBusy.has(anomaly.id) || state.readonly ? "disabled" : ""
          }${state.readonly ? ' title="read-only demo — filing a recommendation is a write"' : ""}>${
            state.recommendBusy.has(anomaly.id) ? "recommender working…" : "file recommendation →"
          }</button>`
        : ""}
      ${action ? `<a class="row-action" href="#sec-decisions">decide in the inbox ↓</a>` : ""}
      ${state.readonly ? `<p class="meta">read-only demo — the agents run on the deployment's own schedule; their output is already on this card</p>` : ""}
    </div>`;

  const series = state.daily?.services.find(
    (s) => s.service.toLowerCase() === String(anomaly.service).toLowerCase()
  );
  if (series && series.values.length) {
    const block = document.getElementById("spark-block");
    block.hidden = false;
    const anomalyIndex = state.daily.dates.indexOf(anomaly.date);
    const mean = series.values.reduce((sum, v) => sum + v, 0) / series.values.length;
    const sigma = Math.sqrt(
      series.values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / series.values.length
    );
    const spark = document.getElementById("spark-svg");
    // Cited evidence rows become rings on the chart — mapped by DATE (the
    // analyst reports cited_dates), so the ring lands on the exact day the
    // citation names regardless of how the series is shaped.
    const cited = analysis && analysis.cited_dates
      ? analysis.cited_dates
          .map((date) => state.daily.dates.indexOf(date))
          .filter((index) => index >= 0)
      : [];
    const drawn = drawSeries(spark, series.values, {
      spikes: [
        ...cited.map((index) => ({ index, severity: "cited" })),
        ...(anomalyIndex >= 0
          ? [{ index: anomalyIndex, severity: anomaly.severity }]
          : []),
      ],
      band: { mean, sigma },
    });
    // The evidence draws itself in when a DIFFERENT signal is picked (or the
    // analyst's citations land) — the fourteen days arrive as a story and
    // the cited days ring last. The ten-second scan repaints this panel too;
    // re-animating on every one of those would be a tic, not a signal.
    const sparkShape = `${anomaly.service}·${anomaly.date}·${cited.join(",")}`;
    if (sparkShape !== sparkDrawn) revealSeries("spark-reveal", drawn);
    sparkDrawn = sparkShape;
    spark.setAttribute(
      "aria-label",
      `Daily spend for ${anomaly.service}: mean ${fmtNumber(mean)} with a one-sigma band; the anomaly day is marked.`
    );
    document.getElementById("spark-stats").innerHTML =
      `<span class="spark-legend"><span>min ${fmtNumber(Math.min(...series.values))}</span>` +
      `<span>mean ${fmtNumber(mean)}</span>` +
      `<span>max ${fmtNumber(Math.max(...series.values))}</span>` +
      `<span>band ±σ · anomaly day marked${cited.length ? " · cited days ringed" : ""}</span></span>`;
  }
}

/* Shared fragments for section III and the inbox — one source for the
   skeptic fold and the preferred-stance saving, instead of three copies. */
function preferredMonthlySaving(detail) {
  const savings = detail.savings || {};
  return detail.preferred === "BOLD" ? savings.bold_monthly : savings.cautious_monthly;
}

function transcriptFold(detail) {
  if (!detail.transcript) return "";
  const transcript = detail.transcript;
  const reviewers = transcript.reviewers;
  if (reviewers && reviewers.length) {
    // panel transcript: one row per reviewer — persona, the model that
    // seat actually ran, its stance (or abstention) and its argument
    const rows = reviewers
      .map((reviewer) => {
        const stance = reviewer.stance
          ? `${reviewer.stance}${reviewer.agreed ? "" : " — dissent"}`
          : "abstained";
        return `<p class="meta">${escapeHtml(
          `${reviewer.persona} · ${reviewer.model} — ${stance} · ${reviewer.argument || ""}`
        )}</p>`;
      })
      .join("");
    return buildFold(
      `review panel convened — ${transcript.agreed ? "consensus" : "stance revised"}`,
      `<p class="meta">trigger — ${escapeHtml(transcript.trigger || "")}</p>` +
        rows +
        `<p class="meta">${
          transcript.agreed
            ? `the majority backed the ${escapeHtml(transcript.original_preferred || "draft")} stance`
            : `the majority revised the stance ${escapeHtml(transcript.original_preferred || "")} → ${escapeHtml(transcript.final_preferred || "")}`
        }</p>`
    );
  }
  return buildFold(
    `skeptic reviewed this — ${transcript.agreed ? "consensus" : "stance revised"}`,
    `<p class="meta">trigger — ${escapeHtml(transcript.trigger || "")}</p>` +
      `<p class="body">${escapeHtml(transcript.skeptic_rationale || "")}</p>` +
      `<p class="meta">${
        transcript.agreed
          ? `agreed with the ${escapeHtml(transcript.original_preferred || "draft")} stance`
          : `revised the stance ${escapeHtml(transcript.original_preferred || "")} → ${escapeHtml(transcript.final_preferred || "")}`
      }</p>`
  );
}

function numericCheckLine(detail) {
  const check = detail.numeric_check;
  if (!check) return "";
  return check.status === "ok"
    ? `<p class="meta">narrative figures verified ±5% against the computed savings</p>`
    : `<p class="meta">figure check — ${check.figures.length} narrative figure(s) unverified; the computed numbers are authoritative</p>`;
}

/* Orchestration trace: the chain as it actually ran — hop, source, timing.
   Persisted with the action, so the fold replays honestly after reloads. */
function traceFold(detail) {
  const trace = detail.trace;
  if (!trace || !trace.length) return "";
  const label = (entry) => {
    if (entry.step === "memory")
      return `memory — ${entry.entries} prior verdict${entry.entries === 1 ? "" : "s"} recalled`;
    if (entry.step === "panel")
      return (
        `panel — ${entry.answered}/${entry.reviewers} reviewers answered · ` +
        `${entry.revised ? "stance revised" : "consensus"}` +
        (typeof entry.duration_ms === "number" ? ` · ${entry.duration_ms.toFixed(0)} ms` : "")
      );
    const bits = [entry.step, entry.source === "fallback" ? "rule-based fallback" : entry.source];
    if (entry.from_cache) bits.push("cached");
    if (entry.reflected) bits.push("reflection pass");
    if (entry.step === "skeptic") bits.push(entry.revised ? "stance revised" : "consensus");
    if (typeof entry.duration_ms === "number") bits.push(`${entry.duration_ms.toFixed(0)} ms`);
    return bits.join(" · ");
  };
  return buildFold(
    `agent chain — ${trace.length} hop${trace.length === 1 ? "" : "s"}, traced`,
    trace.map((entry) => `<p class="meta">${escapeHtml(label(entry))}</p>`).join("")
  );
}

function memoryFold(detail) {
  const memory = detail.memory;
  if (!memory || !memory.count) return "";
  return buildFold(
    `decision memory — ${memory.count} prior verdict${memory.count === 1 ? "" : "s"} shaped this proposal`,
    memory.entries.map((line) => `<p class="meta">${escapeHtml(line)}</p>`).join("")
  );
}

function renderRecommendationBlock(anomaly, action, analysis) {
  if (action) {
    const detail = action.detail || {};
    const preferred = (detail.options || []).find((o) => o.stance === detail.preferred);
    const saving = preferredMonthlySaving(detail);
    return `
      <div class="inv-block recommendation" style="grid-column: 1 / -1;">
        <p class="microcap">Recommended action — Recommender agent${detail.source === "fallback" ? " (fallback)" : ""}</p>
        <p class="rec-title">${escapeHtml(action.title)}</p>
        <p class="rec-facts">${preferred ? `stance ${escapeHtml(detail.preferred)} · est. saving ${fmtNumber(saving ?? 0)} / month · risk ${escapeHtml(preferred.risk)} · rollback ${escapeHtml(preferred.rollback)}` : `stance ${escapeHtml(detail.preferred || "—")}`}</p>
        ${detail.escalation_reason ? `<p class="meta">debate-lite: ${escapeHtml(detail.escalation_reason)}</p>` : ""}
        ${transcriptFold(detail)}
        ${memoryFold(detail)}
        ${traceFold(detail)}
        ${numericCheckLine(detail)}
        <p class="meta">filed to the decision inbox — state ${escapeHtml(action.state)}</p>
      </div>`;
  }
  if (analysis) {
    return `
      <div class="inv-block recommendation" style="grid-column: 1 / -1;">
        <p class="microcap">Recommended action</p>
        <p class="body">Triage complete — file the recommendation to get two options (cautious / bold) with computed savings into the decision inbox.</p>
      </div>`;
  }
  const demo = detailFor(anomaly.service);
  return `
      <div class="inv-block recommendation" style="grid-column: 1 / -1;">
        <p class="microcap">Recommended action — demo narrative</p>
        <p class="rec-title">${escapeHtml(demo.proposal)}</p>
        <p class="rec-facts">saving ${escapeHtml(demo.savings)} · risk ${escapeHtml(demo.risk)} · rollback ${escapeHtml(demo.rollback)}</p>
      </div>`;
}

async function runAnalyst() {
  const anomaly = state.anomalies[state.selectedIndex];
  // The busy set is the single source of truth: re-renders keep the button
  // disabled, and a second click (or re-rendered twin) cannot double-fire.
  if (!anomaly || anomaly.id == null || state.analystBusy.has(anomaly.id)) return;
  state.analystBusy.add(anomaly.id);
  renderInvestigation();
  try {
    const response = await fetch(`/anomalies/${anomaly.id}/analyze`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const analysis = await response.json();
    state.analyses.set(anomaly.id, analysis);
    auditNote(
      `Analyst agent triaged the ${anomaly.service} signal — ${analysis.triage}`,
      `Confidence ${analysis.confidence.score.toFixed(2)}` +
        `${analysis.reflected ? " · reflection pass applied" : ""}` +
        `${analysis.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}` +
        `${analysis.from_cache ? " · served from cache" : ""}.`
    );
  } catch (error) {
    auditNote("Analyst agent request failed", `${error.message} — the panel keeps its previous narrative.`);
  } finally {
    state.analystBusy.delete(anomaly.id);
    // analyzing mutates exactly what section VI aggregates (analyzed count,
    // triage mix, confidence, ledger) — refresh it like the other verbs do
    await loadIntelligence();
    renderInvestigation();
    renderAudit();
    renderIntelligence();
  }
}

async function fileRecommendation() {
  const anomaly = state.anomalies[state.selectedIndex];
  if (!anomaly || anomaly.id == null || state.recommendBusy.has(anomaly.id)) return;
  state.recommendBusy.add(anomaly.id);
  renderInvestigation();
  try {
    const response = await fetch(`/anomalies/${anomaly.id}/recommend`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const recommendation = await response.json();
    auditNote(
      `Recommender filed a ${recommendation.preferred} proposal for ${anomaly.service}`,
      `Category ${recommendation.category} · est. saving ${preferredMonthlySaving(recommendation)} / month` +
        `${recommendation.escalation_reason ? " · debate-lite: " + recommendation.escalation_reason : ""}` +
        `${recommendation.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}.`
    );
  } catch (error) {
    auditNote("Recommender request failed", `${error.message} — no proposal was filed.`);
  } finally {
    state.recommendBusy.delete(anomaly.id);
    await refreshDecisionSurfaces();
  }
}

/* ======================================================================
   09 · DECISION DESK — HITL inbox and the reflex/conscious split
   ====================================================================== */

function actionForEvent(eventId) {
  if (eventId == null) return undefined;
  // the newest non-rejected action mirrors the backend's reuse lane
  return [...state.actions]
    .reverse()
    .find((action) => action.event_id === eventId && action.state !== "rejected");
}

let actionsSequence = 0; // last-writer-wins: a stale /actions response must never overwrite a newer one

async function loadActions() {
  const sequence = ++actionsSequence;
  try {
    const report = await fetchJson("/actions");
    if (sequence !== actionsSequence) return; // superseded by a newer reload
    state.actions = report.actions;
    // growth feel: a card this session has not seen enters with a bloom;
    // the first load seeds silently so opening the page never strobes
    state.freshActionIds = state.knownActionIds
      ? new Set(
          report.actions
            .filter((action) => !state.knownActionIds.has(action.id))
            .map((action) => action.id)
        )
      : new Set();
    state.knownActionIds = new Set(report.actions.map((action) => action.id));
    // Decision-moment numbers: the what-if projection for every card still
    // awaiting a verdict (best-effort — a missing projection hides the line).
    const proposed = report.actions.filter((action) => action.state === "proposed");
    const projections = await Promise.all(
      proposed.map((action) =>
        fetchJson(`/analytics/whatif?action_id=${action.id}`).catch(() => null)
      )
    );
    if (sequence !== actionsSequence) return;
    state.whatif = new Map();
    projections.forEach((projection) => {
      if (projection) state.whatif.set(projection.action_id, projection);
    });
  } catch {
    if (sequence !== actionsSequence) return;
    state.actions = []; // the inbox degrades to its empty state
  }
}

function actionStatusLine(action) {
  if (action.state === "proposed") return "awaiting the hand — approve, or reject with a reason";
  if (action.state === "approved") return `approved · ${action.decided_by || "operator"} — ready for simulated execution`;
  if (action.state === "executed") return "executed — SIMULATION";
  if (action.decided_by === "system:timeout") return "expired unanswered — reopen ↺ restarts the clock";
  return `rejected · ${action.decided_by || "operator"} — reopen ↺ returns it to the inbox`;
}

/* The card wears its own map — filed → decided → executed — so the desk
   explains where each proposal stands without a manual. */
function lifecycleSteps(action) {
  const rejected = action.state === "rejected";
  const expired = rejected && action.decided_by === "system:timeout";
  const decideLabel =
    action.state === "approved" || action.state === "executed"
      ? "approved"
      : rejected
        ? expired ? "expired" : "rejected"
        : "decide";
  const steps = [
    { label: "filed", cls: "done" },
    {
      label: decideLabel,
      cls: action.state === "proposed" ? "current" : rejected ? "halt" : "done",
    },
    {
      label: action.state === "executed" ? "executed" : "execute",
      cls:
        action.state === "executed"
          ? "done"
          : action.state === "approved"
            ? "current"
            : "idle",
    },
  ];
  return `<p class="dec-steps" aria-hidden="true">${steps
    .map((step) => `<span class="step ${step.cls}">${step.label}</span>`)
    .join('<span class="step-joint">→</span>')}</p>`;
}

/* Append-only trail from the server — the card's own timeline fold. */
function historyFold(action) {
  const trail = action.history || [];
  if (!trail.length) return "";
  const rows = trail
    .map(
      (entry) =>
        `<p class="meta">${escapeHtml(entry.at)} · <strong>${escapeHtml(entry.transition)}</strong>${
          entry.actor ? ` · ${escapeHtml(entry.actor)}` : ""
        }${entry.note ? ` — “${escapeHtml(entry.note)}”` : ""}</p>`
    )
    .join("");
  return buildFold(
    `timeline — ${trail.length} step${trail.length === 1 ? "" : "s"}`,
    rows
  );
}

/* Reflex/conscious split — the filed cards' honest ledger: which sailed
   through with no review call, which escalated and why, what the debate
   changed. Pure client-side aggregation over the already-fetched cards —
   measured from the cards themselves, not narrated; legacy cards without
   an escalation record make no claim either way. */
function renderDecisionSplit() {
  const host = document.getElementById("decision-split");
  if (!host) return;
  const details = state.actions.map((action) => action.detail || {});
  if (!details.length) {
    host.textContent = "";
    return;
  }
  let ruleLane = 0;
  let reflex = 0;
  let escalated = 0;
  let lowConfidence = 0;
  let disagreement = 0;
  let repeated = 0;
  let overruled = 0;
  for (const detail of details) {
    if (detail.kind === "fraud_hold" || detail.kind === "budget_risk") {
      ruleLane += 1;
      continue;
    }
    if (!("escalation_reason" in detail)) continue;
    const reason = detail.escalation_reason;
    if (reason == null) {
      reflex += 1;
      continue;
    }
    escalated += 1;
    if (reason.startsWith("low confidence")) lowConfidence += 1;
    else if (reason.startsWith("analyst-recommender disagreement")) disagreement += 1;
    else if (reason.startsWith("repeated reflex")) repeated += 1;
    if (detail.transcript && detail.transcript.agreed === false) overruled += 1;
  }
  const chips = [];
  if (reflex)
    chips.push(statChip(`${chipStrong(reflex)} sailed through — no skeptic call`));
  if (escalated) {
    const why = [
      lowConfidence ? `${lowConfidence}× low confidence` : "",
      disagreement ? `${disagreement}× disagreement` : "",
      repeated ? `${repeated}× repeated reflex` : "",
    ]
      .filter(Boolean)
      .join(", ");
    chips.push(statChip(`${chipStrong(escalated)} escalated${why ? ` — ${why}` : ""}`));
  }
  if (overruled)
    chips.push(statChip(`${chipStrong(overruled)} overruled by review`));
  if (ruleLane)
    chips.push(statChip(`${chipStrong(ruleLane)} rule-lane — no LLM`));
  host.innerHTML = chips.length ? `these cards, measured — ${chips.join(" ")}` : "";
}

function renderDecisions() {
  renderDecisionSplit();
  const pending = state.actions.filter((a) => a.state === "proposed").length;
  document.getElementById("decision-meta").textContent = pending
    ? `${pending} proposal${pending === 1 ? "" : "s"} awaiting an accountable hand — nothing executes automatically, execution is always simulated`
    : "a proposed action stays inert until an operator accepts or rejects it — file one from an investigated signal";

  decisionList.innerHTML = "";
  if (state.actions.length === 0) {
    decisionList.innerHTML = `<p class="all-quiet">No filed proposal — investigate a signal, or <button class="row-action" type="button" data-run-pulse>run Pulse →</button> to sweep the whole estate.</p>`;
    return;
  }

  state.actions.forEach((action) => {
    const detail = action.detail || {};
    const cardKind = detail.kind; // fraud_hold | budget_risk | (cost card)
    const anomaly = detail.anomaly || {};
    const analysisReport = detail.analysis || {};
    const confidence = detail.confidence || {};
    const preferred = (detail.options || []).find((o) => o.stance === detail.preferred);
    const saving = preferredMonthlySaving(detail);
    const busy = state.hitlBusy.has(action.id);
    const whatif =
      action.state === "proposed" && !cardKind ? state.whatif.get(action.id) : null;
    const severity =
      cardKind === "fraud_hold"
        ? (detail.fraud?.score ?? 0) >= 90 ? "critical" : "warning"
        : cardKind === "budget_risk"
          ? "critical"
          : anomaly.severity || "warning";
    const resolved = action.state === "rejected" || action.state === "executed";
    const card = document.createElement("article");
    card.className = `decision ${severity} ${resolved ? "resolved" : ""} ${action.state} ${
      state.freshActionIds.has(action.id) ? "fresh" : ""
    }`;
    // card body per lane: cost cards carry the full agent evidence pack;
    // fraud and budget cards carry their deterministic arithmetic instead
    let bodyHtml;
    if (cardKind === "fraud_hold") {
      const fraud = detail.fraud || {};
      bodyHtml = `
        <p class="dec-title">${escapeHtml(fraud.service || "payments")} — ${escapeHtml(action.title)}</p>
        <p class="dec-copy">${escapeHtml(fraud.date || "—")} · amount ${fmtNumber(fraud.amount ?? 0)} USD · published rule score ${fraud.score ?? "—"} — ${escapeHtml(fraud.band === "hold_suggested" ? "hold suggested" : fraud.band || "")}</p>
        <p class="dec-facts">${(fraud.rule_hits || []).map((hit) => `<span>${escapeHtml(hit.rule.replace("_", " "))} +${hit.points}</span>`).join("")}</p>
        ${(fraud.reasons || []).length ? `<p class="meta">${escapeHtml(fraud.reasons.join(" · "))}</p>` : ""}
        <p class="meta">${escapeHtml(detail.note || "")}</p>`;
    } else if (cardKind === "budget_risk") {
      const forecast = detail.forecast || {};
      bodyHtml = `
        <p class="dec-title">monthly budget — ${escapeHtml(action.title)}</p>
        <p class="dec-copy">projected ${fmtNumber(forecast.projected_month_total ?? 0)} vs budget ${fmtNumber(forecast.monthly_budget ?? 0)} — overage ${fmtNumber(detail.overage ?? 0)} for ${escapeHtml(forecast.month || "the month")}</p>
        <p class="dec-facts">${(detail.options || []).map((option) => `<span>${escapeHtml(option.stance)} — ${escapeHtml(option.title)}</span>`).join("")}</p>
        <p class="meta">${escapeHtml(detail.note || "")}</p>`;
    } else {
      bodyHtml = `
        <p class="dec-title">${escapeHtml(anomaly.service || "service")} — ${escapeHtml(action.title)}</p>
        <p class="dec-copy">observed ${escapeHtml(anomaly.date || "—")} · z ${anomaly.z_score != null ? Number(anomaly.z_score).toFixed(2) : "—"} · triage ${escapeHtml(analysisReport.triage || "—")} — ${escapeHtml(analysisReport.summary || "no analyst summary recorded")}</p>
        <p class="dec-facts"><span>stance ${escapeHtml(detail.preferred || "—")}</span><span>risk ${escapeHtml(preferred ? preferred.risk : "—")}</span><span>est. saving ${fmtNumber(saving ?? 0)} / month</span><span>confidence ${confidence.score != null ? Math.round(confidence.score * 100) : "—"}%</span></p>
        ${preferred ? `<p class="meta">rollback ${escapeHtml(preferred.rollback)}</p>` : ""}
        ${detail.escalation_reason ? `<p class="meta">debate-lite: ${escapeHtml(detail.escalation_reason)}</p>` : ""}
        ${transcriptFold(detail)}
        ${memoryFold(detail)}
        ${traceFold(detail)}
        ${numericCheckLine(detail)}
        ${whatif ? `<p class="meta">if approved — month projection ${fmtNumber(whatif.current_monthly_projection)} → ${fmtNumber(whatif.with_action_monthly_projection)} (−${fmtNumber(whatif.monthly_saving_if_executed)}/mo, simulated)</p>` : ""}`;
    }
    card.innerHTML = `
      <span class="sq" aria-hidden="true"></span>
      <div>${bodyHtml}${historyFold(action)}
      </div>
      <div class="dec-rail">
        <span class="chip ${action.decided_by === "system:timeout" ? "expired" : action.state}">${
          action.state === "executed"
            ? "executed — simulation"
            : action.decided_by === "system:timeout"
              ? "expired"
              : escapeHtml(action.state)
        }</span>
        ${lifecycleSteps(action)}
        <p class="dec-status">${escapeHtml(actionStatusLine(action))}</p>
        ${action.expires_in_hours != null ? `<p class="meta">${action.expires_in_hours >= 48 ? `expires in ~${Math.round(action.expires_in_hours / 24)}d` : `expires in ~${Math.max(0, Math.round(action.expires_in_hours))}h`}</p>` : ""}
        ${action.event_id != null ? `<button class="row-action" type="button" data-view-signal="${action.event_id}" aria-label="jump to the ${escapeHtml(anomaly.service || "")} signal in investigation">view signal ↑</button>` : ""}
        ${action.state === "proposed" && !busy && !state.readonly ? `
          <input type="text" class="rationale-input" placeholder="rationale — required to reject" maxlength="500" data-rationale-for="${action.id}" aria-label="rationale for the ${escapeHtml(anomaly.service || "")} decision" />
          <button class="row-action" type="button" data-hitl="reject" data-action-id="${action.id}" aria-label="reject the ${escapeHtml(anomaly.service || "")} proposal">reject ×</button>
          <button class="row-action" type="button" data-hitl="approve" data-action-id="${action.id}" aria-label="approve the ${escapeHtml(anomaly.service || "")} proposal for execution">approve →</button>` : ""}
        ${action.state === "approved" && !busy && !state.readonly ? `
          <button class="row-action" type="button" data-hitl="execute" data-action-id="${action.id}" aria-label="run the simulated execution of the ${escapeHtml(anomaly.service || "")} action">execute — simulation →</button>` : ""}
        ${action.state === "rejected" && !busy && !state.readonly ? `
          <input type="text" class="rationale-input" placeholder="why reopen? — lands on the timeline" maxlength="500" data-rationale-for="${action.id}" aria-label="reason for reopening the ${escapeHtml(anomaly.service || "")} proposal" />
          <button class="row-action" type="button" data-hitl="reopen" data-action-id="${action.id}" aria-label="reopen the rejected ${escapeHtml(anomaly.service || "")} proposal">reopen ↺</button>` : ""}
        ${state.readonly && (action.state === "proposed" || action.state === "approved") ? `<p class="meta">read-only demo — decisions disabled</p>` : ""}
        ${busy ? `<p class="meta">recording…</p>` : ""}
      </div>`;
    decisionList.appendChild(card);
  });
}

async function decideAction(actionId, verb) {
  if (state.hitlBusy.has(actionId)) return;
  // capture the rationale BEFORE the busy re-render replaces the input
  const rationaleInput = document.querySelector(`[data-rationale-for="${actionId}"]`);
  const rationale = rationaleInput?.value.trim() || null;
  if (verb === "reject" && !rationale) {
    // the server answers 422 to a bare "no" — say it at the input instead
    if (rationaleInput) {
      rationaleInput.classList.add("needs-reason");
      rationaleInput.placeholder = "a rejection needs a reason — type one first";
      rationaleInput.focus();
    }
    return;
  }
  const actor = (operatorInput?.value || "").trim() || "operator";
  state.hitlBusy.add(actionId);
  renderDecisions();
  try {
    const response = await fetch(`/actions/${actionId}/${verb}`, {
      method: "POST",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ actor, rationale }),
    });
    if (response.status === 409) {
      // idempotency guard: the state machine already recorded a verdict
      const conflict = await response.json().catch(() => ({}));
      auditNote(
        "Decision already recorded — guard held",
        `${conflict.detail || "the action is no longer decidable"}; the inbox reloads the authoritative state.`
      );
      return;
    }
    if (response.status === 422) {
      const refusal = await response.json().catch(() => ({}));
      auditNote(
        "Decision refused by validation",
        typeof refusal.detail === "string"
          ? refusal.detail
          : "the request was incomplete."
      );
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const record = await response.json();
    const service = record.detail?.anomaly?.service || "the flagged service";
    const titles = {
      approve: `Operator approved the ${service} proposal`,
      reject: `Operator rejected the ${service} proposal`,
      execute: `Simulated execution completed for ${service}`,
      reopen: `Operator reopened the ${service} proposal`,
    };
    const copies = {
      approve: "The action is approved and ready for simulated execution — nothing runs on real infrastructure.",
      reject: "The proposal was closed with no infrastructure action.",
      execute: "SIMULATION only: the state machine recorded the execution; no real resource was touched.",
      reopen: "Back in the inbox with a fresh TTL — the earlier verdict stays on the timeline.",
    };
    auditNote(titles[verb], copies[verb] + (rationale ? ` Rationale: ${rationale}` : ""));
  } catch (error) {
    auditNote("Decision request failed", `${error.message} — the inbox reloads with the authoritative state.`);
  } finally {
    state.hitlBusy.delete(actionId);
    await refreshDecisionSurfaces();
  }
}

/* ======================================================================
   10 · LEDGER & AUDIT — the persisted decision trail
   ====================================================================== */

/* Section V is the persisted decision ledger (it survives restarts), not a
   session scratchpad: seed it from the real operator verdicts on load so a
   fresh visitor sees the actual audit trail, never placeholder copy. Live
   in-session activity still layers on top via state.audit.unshift(). */
async function loadDecisions() {
  try {
    const report = await fetchJson("/decisions");
    const rows = report.decisions || [];
    state.audit = rows.length
      ? rows.map((decision) => ({
          time: (decision.decided_at || "").slice(5, 10) || "decision",
          title: `${decision.verdict === "approved" ? "Approved" : "Rejected"} · ${decision.service}`,
          copy: decision.rationale || "(no rationale recorded)",
        }))
      : [
          {
            time: "ledger",
            title: "No operator decisions recorded yet",
            copy: "Approve or reject a proposal on the Decision desk to start the persisted audit trail.",
          },
        ];
  } catch {
    /* ledger unreachable — keep whatever section V already shows */
  }
}

const AUDIT_VISIBLE_LIMIT = 8;

function renderAudit() {
  const visible = state.auditExpanded ? state.audit : state.audit.slice(0, AUDIT_VISIBLE_LIMIT);
  auditList.innerHTML = visible
    .map(
      (item) => `
    <li class="audit-item">
      <span class="audit-time">${escapeHtml(item.time)}</span>
      <span class="sq" aria-hidden="true"></span>
      <div>
        <p class="audit-title">${escapeHtml(item.title)}</p>
        <p class="audit-copy">${escapeHtml(item.copy)}</p>
      </div>
    </li>`
    )
    .join("");
  if (state.audit.length > AUDIT_VISIBLE_LIMIT) {
    auditList.insertAdjacentHTML(
      "beforeend",
      `<li class="audit-more"><button class="row-action" type="button" data-audit-toggle>${
        state.auditExpanded ? "show recent only ↑" : `show all ${state.audit.length} entries ↓`
      }</button></li>`
    );
  }
}

/* ======================================================================
   11 · INTELLIGENCE & HANDOVER — /analytics aggregates, print brief
   ====================================================================== */

let intelSequence = 0; // last-writer-wins: stale analytics must never overwrite newer

async function loadIntelligence() {
  const sequence = ++intelSequence;
  try {
    const [analytics, trend, aiUsage, forecast, calibration, headline, roi, detection, reflexSuggestions] = await Promise.all([
      fetchJson("/analytics/decisions"),
      fetchJson("/analytics/costs/trend"),
      fetchJson("/analytics/ai"),
      fetchJson("/analytics/costs/forecast"),
      fetchJson("/analytics/calibration").catch(() => null),
      fetchJson("/analytics/headline").catch(() => null),
      fetchJson("/analytics/roi").catch(() => null),
      fetchJson("/metrics/detection").catch(() => null),
      fetchJson("/reflex/suggestions").catch(() => null),
    ]);
    if (sequence !== intelSequence) return;
    state.analytics = analytics;
    state.trend = trend;
    state.aiUsage = aiUsage;
    state.forecast = forecast;
    state.calibration = calibration;
    state.headline = headline;
    state.roi = roi;
    state.detection = detection;
    state.reflexSuggestions = reflexSuggestions;
    state.intelStale = false;
  } catch {
    if (sequence !== intelSequence) return;
    // keep the last successful figures; the render marks the feed stale
    state.intelStale = true;
  }
}

/* Section VI — every figure is persisted arithmetic from /analytics; the
   panel never invents a number, it only typesets what the API computed. */
function renderIntelligence() {
  const funnelBox = document.getElementById("intel-funnel");
  const qualityLine = document.getElementById("intel-quality");
  const savingsFig = document.getElementById("intel-savings");
  const trendLine = document.getElementById("intel-trend");
  const teleBox = document.getElementById("intel-telemetry");
  const metaLine = document.getElementById("intel-meta");

  if (!state.analytics) {
    funnelBox.innerHTML = "";
    teleBox.innerHTML = "";
    metaLine.textContent = state.intelStale
      ? "intelligence feed unreachable — it retries with the next scan or decision"
      : "aggregating… — intelligence loads with the first scan";
    return;
  }
  metaLine.textContent = state.intelStale
    ? "intelligence feed unreachable — showing the last successful aggregates"
    : "aggregates over everything the pipeline has persisted — pure arithmetic, no generation";

  const { funnel, quality, telemetry } = state.analytics;
  const cells = [
    ["signals", funnel.signals],
    ["analyzed", funnel.analyzed],
    ["proposals", funnel.proposals],
    ["pending", funnel.pending],
    ["approved", funnel.approved + funnel.executed],
    ["rejected", funnel.rejected],
    ["executed", funnel.executed],
  ];
  funnelBox.innerHTML =
    `<div class="funnel-row">` +
    cells
      .slice(0, 3)
      .map(([label, value]) => `<div class="funnel-cell"><p class="microcap">${label}</p><p class="funnel-fig">${value}</p></div>`)
      .join("") +
    `</div><div class="funnel-row funnel-row-4">` +
    cells
      .slice(3)
      .map(([label, value]) => `<div class="funnel-cell ${value === 0 ? "quiet" : ""}"><p class="microcap">${label}</p><p class="funnel-fig">${value}</p></div>`)
      .join("") +
    `</div>`;

  const rate = quality.approval_rate;
  const hours = quality.avg_decision_hours;
  qualityLine.textContent =
    `${quality.human_decisions} human decision${quality.human_decisions === 1 ? "" : "s"}` +
    ` · approval rate ${rate == null ? "—" : `${Math.round(rate * 100)}%`}` +
    ` · avg time to decide ${hours == null ? "—" : hours < 1 ? `${Math.round(hours * 60)}m` : `${hours.toFixed(1)}h`}` +
    (funnel.timeout_rejections ? ` · ${funnel.timeout_rejections} expired unanswered` : "");

  const currency = escapeHtml(state.costs ? state.costs.currency : "USD");
  // approved savings is the room's headline number — it counts to its new
  // value when a verdict lands, so the change is something you watch happen
  rollFigure(
    savingsFig,
    quality.approved_estimated_monthly_savings,
    (v) => `${fmtNumber(v)} <small>${currency} / mo</small>`
  );

  if (state.trend) {
    const trend = state.trend;
    const mover = trend.services[0];
    const moverNote =
      mover && mover.change != null
        ? ` — top mover ${mover.service} (${mover.change >= 0 ? "+" : "−"}${fmtNumber(Math.abs(mover.change))})`
        : "";
    // change === null is the backend's "windows are not comparable" flag;
    // change set but change_pct null means the prior window's spend was zero.
    if (trend.change == null) {
      trendLine.textContent =
        `insufficient history for a ${trend.window_days}-day comparison — ` +
        `current window holds ${trend.current_window_days} day${trend.current_window_days === 1 ? "" : "s"}`;
    } else if (trend.change_pct == null) {
      trendLine.textContent =
        `spend ${trend.change >= 0 ? "rose" : "fell"} ${fmtNumber(Math.abs(trend.change))} ` +
        `against a zero-spend prior ${trend.window_days} days` + moverNote;
    } else {
      trendLine.textContent =
        `spend ${trend.change_pct >= 0 ? "rose" : "fell"} ${Math.abs(trend.change_pct).toFixed(1)}% ` +
        `vs the prior ${trend.window_days} days` + moverNote;
    }
  } else {
    trendLine.textContent = "—";
  }

  const triageEntries = Object.entries(telemetry.triage_distribution);
  const sources = Object.entries(telemetry.by_source)
    .map(([source, count]) => `${escapeHtml(source)} ${count}`)
    .join(" · ");
  const quota = state.aiUsage;
  const roiLine = (() => {
    if (!state.roi || !state.roi.rows || !state.roi.rows.length) return "";
    const observed = state.roi.rows.filter((row) => row.status === "observed");
    const estimatedOnly = state.roi.rows.length - observed.length;
    const net = observed.reduce((sum, row) => sum + (row.observed_monthly_delta || 0), 0);
    const observedNote = observed.length
      ? `<span class="tele-fig">${observed.length}</span> observed (net ${fmtNumber(net)}/mo)`
      : "none observed yet";
    const estimatedNote = estimatedOnly
      ? ` · <span class="tele-fig">${estimatedOnly}</span> estimated-only (no post-decision days)`
      : "";
    return `<p class="meta tele-line">realized savings — ${observedNote}${estimatedNote}</p>`;
  })();
  teleBox.innerHTML = `
    <p class="meta tele-line">triage — ${
      triageEntries.length
        ? triageEntries.map(([kind, count]) => `<span class="tele-fig">${escapeHtml(kind)} ×${count}</span>`).join(" · ")
        : "no analyses recorded yet"
    }</p>
    <p class="meta tele-line">avg confidence — <span class="tele-fig">${
      telemetry.avg_confidence == null ? "—" : `${Math.round(telemetry.avg_confidence * 100)}%`
    }</span></p>
    <p class="meta tele-line">ledger — <span class="tele-fig">${telemetry.requests_total}</span> agent calls · <span class="tele-fig">${telemetry.cache_hits}</span> cached · <span class="tele-fig">${telemetry.debates}</span> debate${telemetry.debates === 1 ? "" : "s"}${telemetry.debates_overturned ? ` · <span class="tele-fig">${telemetry.debates_overturned}</span> overturned` : ""}</p>
    ${
      state.calibration && state.calibration.decisions_with_confidence
        ? `<p class="meta tele-line">calibration — ${state.calibration.buckets
            .filter((bucket) => bucket.decisions)
            .map((bucket) => `${escapeHtml(bucket.range)}: ${Math.round((bucket.approval_rate ?? 0) * 100)}% (${bucket.decisions})`)
            .join(" · ")}</p>`
        : ""
    }
    <p class="meta tele-line">${sources ? `sources — ${sources}` : "no agent calls ledgered yet"}</p>
    ${
      quota
        ? `<p class="meta tele-line">ai quota — <span class="tele-fig">${quota.live_calls_today}</span> live call${quota.live_calls_today === 1 ? "" : "s"} today · assumed ${quota.rpd_assumption} RPD (${quota.rpd_used_pct}%)</p>`
        : ""
    }
    ${
      state.detection && state.detection.decided
        ? `<p class="meta tele-line">detector precision — <span class="tele-fig">${state.detection.precision_proxy == null ? "—" : `${Math.round(state.detection.precision_proxy * 100)}%`}</span> proxy · ${state.detection.approved} approved / ${state.detection.rejected} rejected of ${state.detection.decided} decided (rejections as a coarse false-positive proxy)</p>`
        : ""
    }
    ${roiLine}
    ${
      state.reflexSuggestions
        ? `<p class="meta tele-line">reflex suggestions — ${
            state.reflexSuggestions.count
              ? `<span class="tele-fig">${state.reflexSuggestions.count}</span> candidate rule${state.reflexSuggestions.count === 1 ? "" : "s"} for operator review`
              : "none yet — no unanimously-approved pattern"
          }</p>`
        : ""
    }`;

  const forecastLine = document.getElementById("trend-forecast");
  if (state.forecast) {
    const forecast = state.forecast;
    forecastLine.textContent =
      `month-end projection ${fmtNumber(forecast.projected_month_total)} ` +
      `(${forecast.slope_per_day >= 0 ? "+" : "−"}${fmtNumber(Math.abs(forecast.slope_per_day))}/day)` +
      (forecast.projected_over_budget == null
        ? ""
        : forecast.projected_over_budget
          ? ` — over the ${fmtNumber(forecast.monthly_budget)} budget`
          : ` — within the ${fmtNumber(forecast.monthly_budget)} budget`);
  } else {
    forecastLine.textContent = "";
  }
}

/* Shift handover: fetch the brief, typeset it into the print-only block and
   print. Reuses the ledger print stylesheet — one page, ink on paper. */
async function printHandover() {
  const box = document.getElementById("handover-print");
  try {
    const h = await fetchJson("/analytics/handover");
    const pending = h.pending.length
      ? h.pending
          .map(
            (p) =>
              `<li>#${p.action_id} ${escapeHtml(p.service)} — ${escapeHtml(p.title)}` +
              `${p.age_hours != null ? ` (waiting ${p.age_hours}h)` : ""}</li>`
          )
          .join("")
      : "<li>none — the inbox is clear</li>";
    const decisions = h.recent_decisions.length
      ? h.recent_decisions
          .map(
            (d) =>
              `<li>${escapeHtml(d.decided_at)} · ${escapeHtml(d.service)} · ${escapeHtml(d.verdict)}` +
              `${d.rationale ? ` — ${escapeHtml(d.rationale)}` : ""}</li>`
          )
          .join("")
      : "<li>no operator verdicts recorded yet</li>";
    box.innerHTML =
      `<h2>Shift handover — CloudSentinel</h2>` +
      `<p>Produced ${escapeHtml(utcNow())} · ${h.open_signals} open signal(s), ` +
      `${h.critical_signals} critical · ${h.pending_actions} awaiting the hand` +
      `${h.oldest_pending_hours != null ? ` (oldest ${h.oldest_pending_hours}h)` : ""}.</p>` +
      `<p>Approved value: ${fmtNumber(h.approved_monthly_savings)} / month. ` +
      `Forecast: ${escapeHtml(h.forecast_note)}.</p>` +
      `<p><strong>Awaiting decision</strong></p><ul>${pending}</ul>` +
      `<p><strong>Recent decisions</strong></p><ul>${decisions}</ul>`;
    document.body.classList.add("printing-handover");
    const cleanup = () => document.body.classList.remove("printing-handover");
    window.addEventListener("afterprint", cleanup, { once: true });
    window.print();
    setTimeout(cleanup, 1000); // safety net if afterprint never fires
  } catch {
    box.innerHTML = "";
    auditNote(
      "Handover brief unavailable",
      "the analytics feed did not answer — try again after the next scan."
    );
    renderAudit();
  }
}

/* ======================================================================
   11b · MARKET WATCH — standing opportunities against this estate
   ====================================================================== */

/* GET /market/opportunities: published market bands costed against the
   estate's own run rate. Read-only and action-free by design — the operator
   picks these up, the system never files them. Every row carries the
   arithmetic it rests on and the source it came from. */
async function renderMarket() {
  const host = document.getElementById("market-table");
  if (!host) return;
  try {
    const data = await fetchJson("/market/opportunities");
    state.market = data; // the desk rail reads the same payload, no second fetch
    const rows = data.opportunities || [];
    host.textContent = "";
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "no standing opportunity matches this estate";
      host.appendChild(empty);
      return;
    }
    for (const row of rows) {
      const entry = document.createElement("article");
      entry.className = "market-row";
      entry.innerHTML = `
        <div class="market-head">
          <span class="market-id">${escapeHtml(row.id)}</span>
          <span class="market-title">${escapeHtml(row.headline)}</span>
          <span class="market-service">${escapeHtml(row.service)}</span>
        </div>
        <p class="market-band">${fmtNumber(row.monthly_saving_low)} – ${fmtNumber(
          row.monthly_saving_high
        )} <small>${escapeHtml(data.currency)} / mo</small></p>
        <p class="meta market-basis">${escapeHtml(row.basis)}</p>
        <p class="meta market-facts"><span>effort ${escapeHtml(
          row.effort
        )}</span><span>risk ${escapeHtml(row.risk)}</span><span>${escapeHtml(
          row.horizon
        )}</span></p>
        <details class="transcript market-detail">
          <summary>why, and what to watch</summary>
          <p class="body">${escapeHtml(row.rationale)}</p>
          <p class="meta">watch out — ${escapeHtml(row.watch_out)}</p>
          <p class="meta">source: ${escapeHtml(row.source)} · checked ${escapeHtml(
            row.checked
          )}</p>
        </details>`;
      host.appendChild(entry);
    }
    const badge = document.getElementById("market-source");
    if (badge) {
      badge.textContent = `${data.source} · reviewed ${data.reviewed}`;
    }
    const note = document.getElementById("market-note");
    if (note) {
      note.textContent =
        `gross ${fmtNumber(data.gross_monthly_low)} – ${fmtNumber(
          data.gross_monthly_high
        )} ${data.currency}/mo across ${data.opportunity_count} moves — ${data.note}`;
    }
  } catch {
    /* quiet: the panel keeps its intro line rather than claiming a number */
  }
}

/* ======================================================================
   12 · BRAIN ROOM — insights, routines, runbooks, identity
   ====================================================================== */

/* The brain room: history synthesis (GET /insights) plus a HITL-safe
   self-review cycle. Read-only; every proposal is still a human decision. */
async function renderBrain() {
  const setList = (id, items, empty) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = "";
    if (!items.length) {
      listPlaceholder(el, empty);
      return;
    }
    items.forEach((value) => {
      const li = document.createElement("li");
      li.textContent = value;
      el.appendChild(li);
    });
  };
  try {
    const data = await fetchJson("/insights");
    setList("brain-observations", data.observations || [], "no observations yet");
    setList(
      "brain-predictions",
      (data.predictions || []).map((p) => p.statement),
      "not enough history to project a run rate"
    );
    setList(
      "brain-recommendations",
      (data.recommendations || []).map((r) => `${r.focus}: ${r.action}`),
      "nothing to recommend right now"
    );
  } catch {
    /* leave the placeholders; the panel simply stays quiet */
  }
}

/* Routines: rituals suggested from the current state; running one is read-only
   (insights + pending + cost). Saving is explicit now — a suggestion can be
   persisted without running it, and the saved list runs or retires stored
   rows. A save reuses a stored routine of the same name so repeated clicks
   do not clutter the store (the server allows duplicates by design). */
async function renderRoutines() {
  const list = document.getElementById("routine-suggestions");
  if (!list) return;
  try {
    const data = await fetchJson("/routines/suggestions");
    list.textContent = "";
    const suggestions = data.suggestions || [];
    if (!suggestions.length) {
      listPlaceholder(list, "no routine suggestions");
      return;
    }
    suggestions.forEach((suggestion) => {
      listRow(list, `${suggestion.name} — ${suggestion.rationale} `, [
        { label: "run ▸", onClick: () => runRoutine(suggestion) },
        {
          label: "save ★",
          title: "persist this ritual without running it",
          onClick: () => saveRoutine(suggestion),
        },
      ]);
    });
  } catch {
    /* quiet — the panel keeps its placeholder */
  }
}

function routineFailureNote(error) {
  return error?.message === "HTTP 403"
    ? "read-only demo — the routine store cannot be changed here"
    : null;
}

async function ensureRoutine(suggestion) {
  const existing = await fetchJson("/routines");
  const found = (existing.routines || []).find((r) => r.name === suggestion.name);
  if (found) return found;
  const created = await postJson("/routines", {
    name: suggestion.name,
    description: suggestion.rationale || "",
    steps: suggestion.steps,
  });
  if (!created.ok) throw new Error(`HTTP ${created.status}`);
  return created.json();
}

function showRoutineMessage(text) {
  const out = document.getElementById("routine-output");
  if (!out) return;
  out.hidden = false;
  out.textContent = text;
}

async function renderSavedRoutines() {
  const list = document.getElementById("routine-saved");
  if (!list) return;
  try {
    const data = await fetchJson("/routines");
    list.textContent = "";
    const routines = data.routines || [];
    if (!routines.length) {
      listPlaceholder(list, "nothing saved yet — save a suggestion above");
      return;
    }
    routines.forEach((routine) => {
      const description = routine.description ? ` · ${routine.description}` : "";
      listRow(list, `${routine.name} — ${(routine.steps || []).join(" + ")}${description} `, [
        { label: "run ▸", onClick: () => runRoutineById(routine.id) },
        {
          label: "retire ✕",
          title: "delete this saved routine",
          onClick: () => deleteRoutine(routine),
        },
      ]);
    });
  } catch {
    /* quiet — the panel keeps its placeholder */
  }
}

async function saveRoutine(suggestion) {
  try {
    await ensureRoutine(suggestion);
    await renderSavedRoutines();
  } catch (error) {
    showRoutineMessage(routineFailureNote(error) || "saving the routine failed");
  }
}

async function deleteRoutine(routine) {
  try {
    const response = await fetch(`/routines/${routine.id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
    await renderSavedRoutines();
  } catch (error) {
    showRoutineMessage(routineFailureNote(error) || "retiring the routine failed");
  }
}

async function runRoutineById(routineId) {
  showRoutineMessage("running…");
  const out = document.getElementById("routine-output");
  if (!out) return;
  try {
    const response = await fetch(`/routines/${routineId}/run`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    out.textContent = (run.steps || [])
      .map((step) => `${step.step}: ${JSON.stringify(step.summary)}`)
      .join("\n\n");
  } catch (error) {
    out.textContent = routineFailureNote(error) || "routine run failed";
  }
}

async function runRoutine(suggestion) {
  showRoutineMessage("running…");
  try {
    const routine = await ensureRoutine(suggestion);
    renderSavedRoutines(); // the run may have just persisted the routine
    await runRoutineById(routine.id);
  } catch (error) {
    showRoutineMessage(routineFailureNote(error) || "routine run failed");
  }
}

/* Runbook retrieval: curated, keyword-matched remediation playbooks. */
async function searchRunbooks(query) {
  const list = document.getElementById("runbook-results");
  if (!list) return;
  list.textContent = "";
  const trimmed = (query || "").trim();
  if (!trimmed) return;
  try {
    const data = await fetchJson(`/runbooks/match?query=${encodeURIComponent(trimmed)}`);
    const matches = data.matches || [];
    if (!matches.length) {
      listPlaceholder(list, "no matching runbook");
      return;
    }
    matches.forEach((match) => {
      const li = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = match.runbook.title;
      const steps = document.createElement("span");
      steps.className = "meta";
      steps.textContent = ` — ${match.runbook.steps.join(" · ")}`;
      li.appendChild(title);
      li.appendChild(steps);
      list.appendChild(li);
    });
  } catch {
    /* quiet — the panel stays empty */
  }
}

/* Identity: local sign-in so a decision carries a server-derived operator —
   the audit trail stops being free browser text. Token in localStorage;
   decideAction attaches it and the server derives the actor from the session. */
let authToken = null;
try {
  authToken = localStorage.getItem("sentinel-token") || null;
} catch {
  authToken = null;
}

function authHeaders() {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

/* The status line lives in the masthead (visible from every room); the
   sign-in form stays in the brain room with its own #identity-note for
   form feedback. Signed-out copy is a static template with a fixed room
   link, so the no-reload navigation the navbar uses works from here too. */
function setIdentityNote(text) {
  const note = document.getElementById("identity-note");
  if (note) note.textContent = text;
}

async function refreshIdentity() {
  const status = document.getElementById("identity-status");
  const form = document.getElementById("identity-form");
  const logout = document.getElementById("auth-logout");
  if (!status) return;
  const signedOut = (copy, noteCopy) => {
    // the separator rides in its own span so print (which drops the link)
    // never shows a dangling em dash after the copy
    status.innerHTML =
      `${copy}<span class="sep"> — </span><a class="row-action" href="/brain" data-room="brain">sign in</a>`;
    if (form) form.hidden = false;
    if (logout) logout.hidden = true;
    setIdentityNote(
      noteCopy || "sign in and every decision carries a server-derived identity — the state lives in the masthead"
    );
  };
  if (!authToken) {
    state.identity = null;
    signedOut("not signed in — decisions use the operator field");
    renderDeskIdentity();
    return;
  }
  try {
    const me = await (await fetch("/auth/me", { headers: authHeaders() })).json();
    if (!me.username) throw new Error("bad token");
    // username/role are user-derived: textContent only, never innerHTML
    status.textContent = `signed in as ${me.username} (${me.role}) — decisions carry this identity`;
    state.identity = me; // the desk names the operator whose desk it is
    renderDeskIdentity();
    if (form) form.hidden = true;
    if (logout) logout.hidden = false;
    setIdentityNote("signed in — sign out from the masthead");
  } catch {
    authToken = null;
    try {
      localStorage.removeItem("sentinel-token");
    } catch {
      /* storage unavailable */
    }
    signedOut("session expired", "session expired — sign in again");
  }
}

async function authAction(kind) {
  const username = document.getElementById("auth-username")?.value.trim();
  const password = document.getElementById("auth-password")?.value || "";
  if (!username || !password) {
    setIdentityNote("enter a username and password (min 8 chars)");
    return;
  }
  try {
    if (kind === "register") {
      const reg = await postJson("/auth/register", { username, password, role: "approver" });
      if (!reg.ok && reg.status !== 409) {
        // a 403 is the read-only guard, not a bad password — saying otherwise
        // sends the visitor off to fix something that was never wrong
        setIdentityNote(
          reg.status === 403
            ? "read-only demo — accounts cannot be created on the public link"
            : "registration failed (name taken or weak password)"
        );
        return;
      }
    }
    const login = await postJson("/auth/login", { username, password });
    if (!login.ok) {
      setIdentityNote(
        login.status === 403
          ? "read-only demo — sign-in is disabled on the public link"
          : login.status === 429
            ? "too many sign-in attempts — try again shortly"
            : "invalid username or password"
      );
      return;
    }
    authToken = (await login.json()).token;
    try {
      localStorage.setItem("sentinel-token", authToken);
    } catch {
      /* storage unavailable — token lives for this session only */
    }
    await refreshIdentity();
  } catch {
    setIdentityNote("auth request failed");
  }
}

/* ======================================================================
   13 · LIVE AGENT FEED (right rail)
   The agent bus persists every inter-agent event as it happens; this panel
   polls the cursor endpoint (plain polling, no sockets) so a running pulse
   streams its conversation into the rail in near-real time.
   ====================================================================== */

const FEED_POLL_MS = 2500;
const FEED_MAX_ROWS = 80;
const feedState = { lastId: 0, open: false, timer: null, seen: 0 };

function feedEntryHtml(event) {
  const time = (event.at || "").slice(11, 19);
  return `<li class="feed-item agent-${escapeHtml(event.agent)}">
    <span class="feed-time">${escapeHtml(time)}</span>
    <span class="feed-agent">${escapeHtml(event.agent)}</span>
    <span class="feed-msg">${escapeHtml(event.message)}</span>
  </li>`;
}

async function pollFeed() {
  if (document.hidden) return;
  try {
    const report = await fetchJson(`/agents/feed?after=${feedState.lastId}`);
    if (!report.count) return;
    feedState.lastId = report.last_id;
    feedState.seen += report.count;
    feedEmpty.hidden = true;
    feedList.insertAdjacentHTML(
      "beforeend",
      report.events.map(feedEntryHtml).join("")
    );
    while (feedList.children.length > FEED_MAX_ROWS) {
      feedList.removeChild(feedList.firstChild);
    }
    if (feedState.open) feedList.lastElementChild?.scrollIntoView({ block: "nearest" });
    feedToggle.classList.add("has-traffic");
  } catch {
    /* feed unreachable — the panel simply stays quiet until the next poll */
  }
}

/* ======================================================================
   14 · SCAN, PULSE & HEALTH — the app verbs that refresh everything
   ====================================================================== */

function renderAll(report) {
  renderCosts(state.costs, renderAnomalies(report));
  renderDesk();
  renderMissionPosture();
  renderTrend();
  renderSummary();
  renderInvestigation();
  renderDecisions();
  renderAudit();
  renderIntelligence();
  renderWatch();
  renderRadar();
}

/* One refresh for every decision-adjacent surface — the verbs (decide,
   recommend, analyze) all mutate the same aggregates. */
async function refreshDecisionSurfaces() {
  await Promise.all([loadActions(), loadIntelligence()]);
  renderSummary();
  renderInvestigation();
  renderDecisions();
  renderAudit();
  renderIntelligence();
  renderBrain();
  renderRoutines();
  renderSavedRoutines();
}

let scanSequence = 0; // last-writer-wins guard: a stale response must never overwrite a newer one

async function scan() {
  const sequence = ++scanSequence;
  const threshold = parseFloat(thresholdInput.value).toFixed(2);
  thresholdValue.textContent = threshold;
  const skeleton = `<div class="skeleton-row"></div><div class="skeleton-row short"></div><div class="skeleton-row"></div>`;
  if (!state.anomalies.length) anomalyList.innerHTML = skeleton;
  if (!state.costs) costBars.innerHTML = skeleton;
  anomalyList.style.opacity = "0.55";
  costBars.style.opacity = "0.55";
  const anomalyUrl =
    `/anomalies?threshold=${threshold}` +
    (serviceFilter.value ? `&service=${encodeURIComponent(serviceFilter.value)}` : "");
  try {
    const [anomalies, costs, daily, unfiltered] = await Promise.all([
      fetchJson(anomalyUrl),
      fetchJson("/costs/summary"),
      fetchJson("/costs/daily"),
      serviceFilter.value ? fetchJson(`/anomalies?threshold=${threshold}`) : null,
      loadActions(),
      loadIntelligence(),
      loadWatch(),
      loadDecisions(),
    ]);
    if (sequence !== scanSequence) return;
    state.costs = costs;
    state.daily = daily;
    state.anomalies = anomalies.anomalies;
    state.allAnomalies = unfiltered ? unfiltered.anomalies : anomalies.anomalies;
    state.lastScan = anomalies;
    sortAnomalies();
    populateServiceFilter();
    renderAll(anomalies);
    syncUrlParams();
    // the mission dropdown mirrors the server's active mission
    const missionSelect = document.getElementById("mission-select");
    if (missionSelect && anomalies.mission) missionSelect.value = anomalies.mission;
    editionLine.textContent =
      `SYSTEM ONLINE — ${state.env === "render" ? "LIVE ON RENDER — " : ""}` +
      `${state.readonly ? "READ-ONLY DEMO — " : ""}` +
      `LAST SCAN ${utcNow()} — ${dataBadge()} — ` +
      `AI ${state.provider === "gemini" ? "LIVE (GEMINI)" : "FAKE PROVIDER"}`;
    editionLine.classList.remove("down");
  } catch {
    if (sequence !== scanSequence) return;
    editionLine.textContent = `RECONNECTING — ${dataBadge()} — SPRINT III`;
    editionLine.classList.add("down");
    anomalyList.innerHTML = `<p class="error-note">Reconnecting — the panels keep the last successful scan.</p>`;
    document.getElementById("anomaly-meta").textContent = "waiting to reconnect — the panels keep the last successful scan";
    if (!state.costs) document.getElementById("cost-meta").textContent = "reconnecting…";
  } finally {
    if (sequence === scanSequence) {
      anomalyList.style.opacity = "1";
      costBars.style.opacity = "1";
    }
  }
}

let pulseBusy = false;

async function runPulse() {
  /* One click, the whole chain: detect → analyze → recommend. Decisions
     stay in the inbox — pulse files proposals, it never approves them. */
  if (pulseBusy) return;
  pulseBusy = true;
  pulseButton.disabled = true;
  pulseButton.textContent = "pulse running…";
  try {
    // quick-switch rides the pulse: the selected mission flips the active
    // YAML server-side, and every mission-following surface follows
    const missionChoice = document.getElementById("mission-select")?.value;
    const response = await fetch(
      missionChoice ? `/pulse?mission=${encodeURIComponent(missionChoice)}` : "/pulse",
      { method: "POST" }
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const report = await response.json();
    // run ledger: each chain hop lands in section V under the summary
    [...report.chain].reverse().forEach((link) => {
      auditNote(
        `Pulse chain: ${link.service} → ${link.triage} → action #${link.action_id}`,
        `severity ${link.severity} · preferred ${link.preferred} · ${
          link.reused ? "existing proposal reused" : "new proposal filed"
        } — state ${link.action_state}.`
      );
    });
    auditNote(
      `Pulse swept the estate — ${report.signals} cost + ${report.security_signals} security + ${report.fraud_signals ?? 0} fraud signals`,
      `mission ${report.mission ?? "—"} · REFLEX ${report.reflex_ms ?? "—"} ms · ` +
        `${report.analyzed} analyzed · ${report.proposals_filed} filed · ${report.proposals_reused} reused · ` +
        `${(report.fraud_holds_filed ?? 0) + (report.budget_cards_filed ?? 0) ? `${report.fraud_holds_filed ?? 0} fraud hold(s) + ${report.budget_cards_filed ?? 0} budget card(s) filed · ` : ""}` +
        `LLM ${report.llm_calls_used}/${report.llm_budget}${
          report.budget_exhausted ? " — budget exhausted, fallbacks answered" : ""
        }.`
    );
    // the chronicler narrates the run — its briefing tops the ledger
    if (report.briefing) {
      auditNote(
        `Chronicler briefing — ${report.briefing.headline}`,
        `${report.briefing.summary} Watch next: ${report.briefing.watch_next}` +
          `${report.briefing.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}`
      );
    }
    pulseNote.textContent = pulseNoteLine(report, utcNow());
  } catch (error) {
    auditNote("Pulse request failed", `${error.message} — the panels keep their last state.`);
  } finally {
    pulseBusy = false;
    pulseButton.disabled = false;
    pulseButton.textContent = "Pulse →";
    // the ledger is the only pulse feedback channel (no toasts): render it
    // NOW so the entries survive even if the refresh scan below fails
    renderAudit();
    await scan(); // full refresh: signals, inbox, intelligence, watch
  }
}

function pulseNoteLine(report, when) {
  // the briefing headline already narrates the lane counts — no repetition
  const story = report.briefing
    ? report.briefing.headline
    : `${report.signals} cost + ${report.security_signals} security + ${report.fraud_signals ?? 0} fraud signals`;
  return `last pulse ${when} — ${story} · LLM ${report.llm_calls_used}/${report.llm_budget}`;
}

/* Deploy environment drives the LIVE banner: read it at load for a fast
   first paint, then refresh on the scan cadence so the data badge heals if
   the first fetch failed or a lane's source changes. Best-effort — the
   default stays "local". */
function refreshHealth() {
  return fetchJson("/health")
    .then((health) => {
      state.env = health.env || "local";
      state.readonly = Boolean(health.readonly);
      state.provider = health.provider || "fake";
      state.dataSources = health.data_sources || {};
      // The nav pill reads "live" only on the deployed link; on the local/mock
      // demo it says "demo" so the green dot never implies live production data.
      const liveLabel = document.getElementById("nav-live-label");
      if (liveLabel) liveLabel.textContent = state.env === "render" ? "live" : "demo";
      if (state.readonly) {
        pulseButton.disabled = true;
        pulseButton.title = "read-only demo — the pulse chain is disabled";
        // quick-switch rides the pulse, so it must go quiet with it — an
        // active dropdown here would 403 invisibly instead of flipping
        const missionSelect = document.getElementById("mission-select");
        if (missionSelect) {
          missionSelect.disabled = true;
          missionSelect.title =
            "read-only demo — quick-switch rides the pulse, which is disabled";
        }
        // the identity form asks for a password the endpoint cannot accept:
        // #identity-form is a <span> (disabling it is a no-op) and signedOut()
        // unhides it on every refresh, so the four controls are gated here
        for (const id of ["auth-username", "auth-password", "auth-register", "auth-login"]) {
          const control = document.getElementById(id);
          if (control) {
            control.disabled = true;
            control.title = "read-only demo — sign-in is disabled on the public link";
          }
        }
        setIdentityNote(
          "read-only demo — decisions are disabled; sign-in runs on a private instance"
        );
        // the investigation room's agent verbs are writes too — an enabled
        // "run analyst agent →" that 403s reads as a broken agent
        renderInvestigation();
        renderDecisions();
      }
    })
    .catch(() => {
      /* health unreachable — the banner keeps its last known form */
    });
}

/* The watchroom never sleeps: a quiet background re-scan keeps every
   figure current (and rolling) without a hand on the controls. */
const AUTO_SCAN_MS = 60000;

/* A background scan rebuilds the decision cards, so it must never fire
   while the operator is entering a rationale — mid-typing (the input is
   focused) OR typed-but-not-yet-submitted (a box holds text after a blur).
   Either way the value lives only in the DOM until the verdict click reads
   it, so a silent re-render would drop it. */
function operatorIsMidRationale() {
  const active = document.activeElement;
  if (active && active.classList && active.classList.contains("rationale-input")) {
    return true;
  }
  return Array.from(document.querySelectorAll(".rationale-input")).some(
    (input) => input.value.trim() !== ""
  );
}

/* ======================================================================
   15 · TOUR & ROUTING — guided tour, room navigation, permalinks
   ====================================================================== */

/* Guided jury tour (?tour=1): a walk through the rooms — one stop each —
   so a first-time viewer reads the product in the right order. Vanilla
   DOM, no inline handlers, and it respects the same no-reload navigation
   the navbar uses. Stop numbering is derived from the list, so adding a
   room here is the whole change. */
const TOUR_STOPS = [
  { view: "watch", title: "Watch", body: "Cost, security and fraud anomalies surface here through one detection line. The radar sweeps the live signal field; drag sensitivity and a borderline signal appears." },
  { view: "investigate", title: "Investigation", body: "Pick a signal for its 14-day evidence, the Analyst's cited triage and the Recommender's two options — cautious and bold — with savings computed in Python." },
  { view: "decide", title: "Decision desk", body: "Every critical action waits for a human. Approve or reject with a rationale; nothing executes unapproved, and execution is always simulated." },
  { view: "intel", title: "Intelligence", body: "The funnel, approved value, forecast, calibration and the self-FinOps ledger — pure arithmetic over what the pipeline persisted. Print a shift handover from here." },
  { view: "brain", title: "The brain", body: "What the system concludes from its own history: insights, a HITL-safe self-review, saved routines, runbook retrieval and a measured detection backtest. Every suggestion still waits for a human." },
  { view: "all", title: "The whole broadsheet", body: "Open the agent feed (bottom right) and hit Pulse: watch six agents reason in the open, hop by hop, in real time." },
];

function startTour() {
  if (document.getElementById("tour-card")) return;
  let step = 0;
  const card = document.createElement("aside");
  card.id = "tour-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Guided tour");
  document.body.appendChild(card);
  const render = () => {
    const s = TOUR_STOPS[step];
    applyView(s.view);
    window.scrollTo({ top: 0 });
    card.innerHTML =
      `<p class="tour-title microcap">${escapeHtml(`${step + 1} / ${TOUR_STOPS.length} · ${s.title}`)}</p>` +
      `<p class="tour-body">${escapeHtml(s.body)}</p>` +
      `<div class="tour-actions">` +
      `<button class="row-action" type="button" data-tour="skip">skip</button>` +
      `<button class="row-action" type="button" data-tour="next">${step === TOUR_STOPS.length - 1 ? "done ✓" : "next →"}</button>` +
      `</div>`;
  };
  card.addEventListener("click", (event) => {
    const action = event.target.closest("[data-tour]")?.dataset.tour;
    if (!action) return;
    if (action === "skip" || step === TOUR_STOPS.length - 1) {
      card.remove();
      return;
    }
    step += 1;
    render();
  });
  render();
}

/* View navigation (rooms of the broadsheet): hash-tab views over ONE page —
   no routes, no reload — sections toggle, the print view always shows the
   whole broadsheet. */
const VIEW_SECTIONS = {
  watch: ["sec-desk", "sec-anomalies", "sec-costs"],
  investigate: ["sec-investigation"],
  decide: ["sec-decisions", "sec-ledger"],
  intel: ["sec-intelligence", "sec-market"],
  brain: ["sec-brain"],
};
const ALL_SECTIONS = [...new Set(Object.values(VIEW_SECTIONS).flat())];
const VIEW_TITLES = {
  watch: "Watch",
  investigate: "Investigation",
  decide: "Decision Desk",
  intel: "Intelligence",
  brain: "Brain",
  all: "Broadsheet",
};

function viewFromPath(pathname) {
  const view = (pathname || "/").replace(/^\//, "").split("/")[0];
  if (view === "broadsheet") return "all";
  return VIEW_SECTIONS[view] ? view : "watch"; // the home room
}

function applyView(view) {
  const visible = view === "all" ? ALL_SECTIONS : VIEW_SECTIONS[view] || ALL_SECTIONS;
  ALL_SECTIONS.forEach((id) =>
    document.getElementById(id).classList.toggle("view-hidden", !visible.includes(id))
  );
  document.querySelectorAll(".view-tab, .nav-brand").forEach((tab) =>
    tab.setAttribute("aria-pressed", String(tab.dataset.view === view))
  );
  // the flow map lights the room the visitor is standing in
  document.querySelectorAll(".flow-stop").forEach((stop) =>
    stop.setAttribute("aria-current", stop.dataset.view === view ? "page" : "false")
  );
  document.title = `CloudSentinel — ${VIEW_TITLES[view] || "Anomaly Watch"}`;
  const main = document.querySelector("main");
  main.classList.remove("room-enter");
  void main.offsetWidth; // restart the ease-in for the incoming room
  main.classList.add("room-enter");
  // the backtest chart sizes its viewBox to the host's real width — a chart
  // first drawn while its room was hidden (width 0) needs a redraw now.
  // Deferred a tick: applyView also runs at boot, before the chart's
  // module-level state has initialized.
  if (visible.includes("sec-brain")) setTimeout(() => drawBacktestChart(true), 0);
  // Walking into the watch room draws the spend trend in — a chart measured
  // at width 0 behind a hidden section has to be redrawn here anyway, and
  // arriving at a chart is exactly the moment worth animating.
  if (visible.includes("sec-costs")) {
    markTrendForReveal();
    if (state.daily && state.daily.totals.length) setTimeout(renderTrend, 0);
  }
  // A room that just went away must not leave anything running behind it.
  if (!visible.includes("sec-anomalies")) stopMotion("radar");
  else startRadarSweep();
}

/* Shareable deep links: the sensitivity and service filter live in the URL
   (?threshold=&service=), so a link sent to the jury opens on the exact
   scene. The view stays in the path; theme and other params are preserved. */
function syncUrlParams() {
  const params = new URLSearchParams(location.search);
  params.set("threshold", parseFloat(thresholdInput.value).toFixed(2));
  if (serviceFilter.value) params.set("service", serviceFilter.value);
  else params.delete("service");
  history.replaceState({}, "", `${location.pathname}?${params}`);
}

/* ======================================================================
   16 · EVENTS & BOOT
   Every top-level imperative statement, in its original relative
   execution order: hydrations first, then listener registrations,
   intervals, view boot and the first paint + scan.
   ====================================================================== */

/* palette boot: ?theme= wins, then the persisted colophon choice */
const themeParam = new URLSearchParams(location.search).get("theme");
let storedTheme = null;
try {
  storedTheme = localStorage.getItem("sentinel-theme");
} catch {
  /* storage can be unavailable (private mode) — the default carries */
}
applyTheme(
  // vivid is the default: the product is a control surface before it is a
  // broadsheet, and the light card palette is the one that says so on first
  // paint. ?theme= still wins for a review link, and the four editorial
  // palettes are one click away in the colophon — nothing was removed.
  THEMES.includes(themeParam) ? themeParam : THEMES.includes(storedTheme) ? storedTheme : "vivid"
);

/* ---------- motion: the two switches and the tab's own attention ----------
   A backgrounded tab gets nothing: requestAnimationFrame stops on its own,
   but the register is settled explicitly so returning to the page finds
   finished charts and a beam that picks up cleanly rather than one that
   has been "rotating" against a frozen clock. */
document.addEventListener("visibilitychange", () => {
  if (document.hidden) settleAllMotion();
  else syncMotion();
});
/* The operating system can change its mind mid-session (a low-power mode,
   a system setting) — the page follows it without a reload. */
if (REDUCED_MOTION.addEventListener) REDUCED_MOTION.addEventListener("change", syncMotion);

// Redraw the trend chart on resize so its 1:1 viewBox tracks the panel width.
let trendResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(trendResizeTimer);
  trendResizeTimer = setTimeout(() => {
    if (state.daily && state.daily.totals.length) renderTrend();
  }, 150);
});

/* ?tour=1 opens the guided tour once the page settles */
if (new URLSearchParams(location.search).get("tour") === "1") {
  setTimeout(startTour, 400);
}

/* permalink hydrate: ?threshold= applies now; ?service= waits for options */
const initialParams = new URLSearchParams(location.search);
let pendingServiceFilter = initialParams.get("service");
const initialThreshold = parseFloat(initialParams.get("threshold"));
if (Number.isFinite(initialThreshold) && initialThreshold >= 0.5 && initialThreshold <= 4) {
  thresholdInput.value = String(initialThreshold);
  thresholdValue.textContent = initialThreshold.toFixed(2);
}

thresholdInput.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdInput.value).toFixed(2);
});
/* A hand asked for this — so the trend redraws itself rather than
   silently swapping one still picture for another. The ten-second
   background scan deliberately does not do this. */
const refreshTrend = () => {
  markTrendForReveal();
  scan();
};
thresholdInput.addEventListener("change", refreshTrend);
serviceFilter.addEventListener("change", refreshTrend);
rescanButton.addEventListener("click", refreshTrend);
pulseButton.addEventListener("click", () => {
  markTrendForReveal();
  runPulse();
});
// flipping the mission runs the chain under the new YAML right away —
// the switch IS the demo beat, not a silent preference
document.getElementById("mission-select")?.addEventListener("change", runPulse);

document.addEventListener("click", (event) => {
  const themeChoice = event.target.closest("[data-theme-choice]");
  if (themeChoice) {
    applyTheme(themeChoice.dataset.themeChoice);
    try {
      localStorage.setItem("sentinel-theme", themeChoice.dataset.themeChoice);
    } catch {
      /* best effort — the choice still applies for this visit */
    }
    return;
  }

  const anomalySortButton = event.target.closest("[data-anomaly-sort]");
  if (anomalySortButton) {
    state.anomalySort = anomalySortButton.dataset.anomalySort;
    markPressed("[data-anomaly-sort]", "anomalySort", state.anomalySort);
    const selectedId = state.anomalies[state.selectedIndex]?.id;
    sortAnomalies();
    if (selectedId != null) {
      const index = state.anomalies.findIndex((anomaly) => anomaly.id === selectedId);
      if (index >= 0) state.selectedIndex = index;
    }
    if (state.lastScan) renderAnomalies(state.lastScan);
    renderInvestigation();
    return;
  }

  const serviceButton = event.target.closest("[data-filter-service]");
  if (serviceButton) {
    const service = serviceButton.dataset.filterService;
    serviceFilter.value = serviceFilter.value === service ? "" : service;
    scan();
    return;
  }

  const investigate = event.target.closest("[data-investigate]");
  if (investigate) {
    state.selectedIndex = Number(investigate.dataset.investigate);
    renderInvestigation();
    document.getElementById("sec-investigation").scrollIntoView();
    return;
  }

  const selectSignal = event.target.closest("[data-select-signal]");
  if (selectSignal) {
    state.selectedIndex = Number(selectSignal.dataset.selectSignal);
    renderInvestigation();
    return;
  }

  const viewSignal = event.target.closest("[data-view-signal]");
  if (viewSignal) {
    // jump from an inbox card back to its signal in investigation; if the
    // signal is filtered out of the current scan, just scroll to section III
    const eventId = Number(viewSignal.dataset.viewSignal);
    const index = state.anomalies.findIndex((a) => a.id === eventId);
    if (index >= 0) {
      state.selectedIndex = index;
      renderInvestigation();
    }
    document.getElementById("sec-investigation").scrollIntoView();
    return;
  }

  const hitlAction = event.target.closest("[data-hitl]");
  if (hitlAction) {
    decideAction(Number(hitlAction.dataset.actionId), hitlAction.dataset.hitl);
    return;
  }

  const pulseCta = event.target.closest("[data-run-pulse]");
  if (pulseCta) {
    runPulse();
    return;
  }

  const roomLink = event.target.closest("[data-room]");
  if (roomLink) {
    // footer room links ride the same no-reload navigation as the navbar
    event.preventDefault();
    const target = roomLink.getAttribute("href") || "/";
    if (location.pathname !== target) history.pushState({}, "", target);
    applyView(roomLink.dataset.room);
    window.scrollTo({ top: 0 });
    return;
  }

  const copyBrief = event.target.closest("#copy-brief");
  if (copyBrief && state.headline) {
    navigator.clipboard
      .writeText(state.headline.headline)
      .then(() => {
        auditNote("Jury brief copied to the clipboard", state.headline.headline);
        renderAudit();
      })
      .catch(() => {
        /* clipboard can be unavailable — the headline stays visible in VI */
      });
    return;
  }

  const handoverBtn = event.target.closest("#handover-print-btn");
  if (handoverBtn) {
    printHandover();
    return;
  }

  const tourLaunch = event.target.closest("[data-tour-launch]");
  if (tourLaunch) {
    event.preventDefault();
    startTour();
    return;
  }

  const recommendRequest = event.target.closest("[data-request-recommend]");
  if (recommendRequest) {
    fileRecommendation();
    return;
  }

  const sortButton = event.target.closest("[data-sort]");
  if (sortButton) {
    state.sortMode = sortButton.dataset.sort;
    markPressed("[data-sort]", "sort", state.sortMode);
    if (state.costs) renderCosts(state.costs, new Set(state.anomalies.map((a) => a.service)));
    return;
  }

  const evidenceRequest = event.target.closest("[data-request-evidence]");
  if (evidenceRequest) {
    runAnalyst();
    return;
  }

  const auditToggle = event.target.closest("[data-audit-toggle]");
  if (auditToggle) {
    state.auditExpanded = !state.auditExpanded;
    renderAudit();
  }
});

/* keyboard: walk the signal rail with the arrow keys */
signalRail.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  if (!state.anomalies.length) return;
  event.preventDefault();
  const delta = event.key === "ArrowDown" ? 1 : -1;
  state.selectedIndex =
    (state.selectedIndex + delta + state.anomalies.length) % state.anomalies.length;
  renderInvestigation();
  signalRail.querySelector(".signal-option.is-selected")?.focus();
});

refreshHealth();
setInterval(refreshHealth, 60000);

/* Operator identity: recorded with every decision (audit trail), persisted
   like the palette so a team demo keeps each hand attributable. */
try {
  operatorInput.value = localStorage.getItem("sentinel-operator") || "";
} catch {
  /* storage unavailable — the field still works for this visit */
}
operatorInput.addEventListener("change", () => {
  try {
    localStorage.setItem("sentinel-operator", operatorInput.value.trim());
  } catch {
    /* best effort */
  }
});

/* The last pulse survives reloads: hydrate the colophon note (and the
   briefing story) from the persisted run instead of starting silent. */
fetchJson("/pulse/last")
  .then((last) => {
    pulseNote.textContent = pulseNoteLine(last.report, `${last.ran_at} UTC`);
  })
  .catch(() => {
    pulseNote.textContent = "";
  });

/* Print header/date stamp: a printed ledger is an audit artifact, so it
   carries a title and the date it was produced (screen-hidden, print-shown). */
const printStamp = document.getElementById("print-stamp");
if (printStamp) {
  const today = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD
  printStamp.textContent = `CloudSentinel — decision ledger · produced ${today}`;
}

// Real page URLs without reloads: links push history, back/forward replay.
document.getElementById("view-nav").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-view]");
  if (!tab) return;
  event.preventDefault();
  const target = tab.getAttribute("href") || "/";
  if (location.pathname !== target) history.pushState({}, "", target);
  applyView(tab.dataset.view);
  window.scrollTo({ top: 0 });
});
// the flow map is a second door to every room — same push-state grammar
document.getElementById("flow-map")?.addEventListener("click", (event) => {
  const stop = event.target.closest("[data-view]");
  if (!stop) return;
  event.preventDefault();
  const target = stop.getAttribute("href") || "/";
  if (location.pathname !== target) history.pushState({}, "", target);
  applyView(stop.dataset.view);
  window.scrollTo({ top: 0 });
});
window.addEventListener("popstate", () => applyView(viewFromPath(location.pathname)));
applyView(viewFromPath(location.pathname));

setInterval(() => {
  if (!document.hidden && !pulseBusy && !operatorIsMidRationale()) scan();
}, AUTO_SCAN_MS);

feedToggle.addEventListener("click", () => {
  feedState.open = !feedState.open;
  feedBody.hidden = !feedState.open;
  feedToggle.setAttribute("aria-expanded", String(feedState.open));
  document.getElementById("agent-feed").classList.toggle("open", feedState.open);
  try {
    localStorage.setItem("sentinel-feed-open", feedState.open ? "1" : "");
  } catch {
    /* best effort */
  }
  if (feedState.open) {
    pollFeed();
    feedList.lastElementChild?.scrollIntoView({ block: "nearest" });
  }
});

try {
  if (localStorage.getItem("sentinel-feed-open") === "1") feedToggle.click();
} catch {
  /* storage unavailable — the panel starts collapsed */
}
feedState.timer = setInterval(pollFeed, FEED_POLL_MS);
pollFeed();

/* Live tape — the simulated stream, honestly labeled. /stream/metrics 404s
   when SENTINEL_SIM_STREAM is off, so a deployment without the flag never
   even shows the strip. Synthetic figures only — no billing data anywhere. */
const tapeState = { polling: false, failures: 0, shape: "", rows: new Map() };

function tapeSparkPoints(trend) {
  if (!Array.isArray(trend) || trend.length < 2) return "";
  const min = Math.min(...trend);
  const span = Math.max(...trend) - min || 1;
  const step = 54 / (trend.length - 1);
  return trend
    .map((value, i) => `${(i * step).toFixed(1)},${(13 - ((value - min) / span) * 11).toFixed(1)}`)
    .join(" ");
}

/* The tape also feeds the cost ledger a LIVE layer: a run-rate line under
   the hero figure and a per-service delta chip on each row. The historical
   daily figures stay untouched — facts are facts; only the clearly-labeled
   simulated layer moves. */
function feedCostLedgerFromTape(frame) {
  const line = document.getElementById("ledger-live-rate");
  if (line) {
    const total = frame.services.reduce((sum, lane) => sum + lane.rate, 0);
    // on the sim lane the chart's last point is today's projection — name it
    // here rather than printing it over the series
    const daily = state.daily?.totals;
    const today =
      String(state.dataSources?.costs || "").startsWith("sim") && daily?.length
        ? ` · today ${fmtNumber(daily[daily.length - 1])} USD so far`
        : "";
    line.hidden = false;
    line.textContent = `live run-rate ${fmtNumber(total)} USD/hour${today} — simulated stream`;
    if (motionAllowed()) {
      line.classList.remove("tick");
      void line.offsetWidth; // restart the pulse so every frame visibly lands
      line.classList.add("tick");
    } else {
      line.classList.remove("tick");
    }
  }
  frame.services.forEach((lane) => {
    const chip = document.querySelector(`[data-tape-chip="${CSS.escape(lane.service)}"]`);
    if (!chip) return;
    const up = lane.delta_pct >= 0;
    chip.hidden = false;
    chip.classList.toggle("up", up);
    chip.classList.toggle("down", !up);
    chip.textContent = `${up ? "▲" : "▼"}${Math.abs(lane.delta_pct).toFixed(2)}% now`;
  });
}

/* The tape TICKS: the rows are built once for a given set of services and
   then re-valued in place, so a rate travels to its new figure and the
   sparkline slides along. Rebuilding the rows every 2.5 seconds — which is
   what this did — replaced the whole strip mid-blink, and a thing that is
   destroyed and recreated cannot appear to move. Text goes in through
   textContent; the service names are the only external strings here and
   they never reach an innerHTML. */
function buildTapeRows(host, frame) {
  host.replaceChildren();
  tapeState.rows = new Map();
  for (const lane of frame.services) {
    const row = document.createElement("div");
    row.className = "tape-row";

    const service = document.createElement("span");
    service.className = "tape-service";
    service.textContent = lane.service;

    const spark = svgEl("svg", { class: "tape-spark", viewBox: "0 0 54 14", "aria-hidden": "true" });
    const polyline = svgEl("polyline", { points: "" });
    spark.append(polyline);

    const rate = document.createElement("span");
    rate.className = "tape-rate";
    rate.dataset.rollKey = `tape-${lane.service}`;

    const delta = document.createElement("span");
    delta.className = "tape-delta";

    row.append(service, spark, rate, delta);
    host.append(row);
    tapeState.rows.set(lane.service, { row, polyline, rate, delta });
  }
}

function renderTape(frame) {
  const host = document.getElementById("tape-rows");
  if (!host) return;
  const shape = frame.services.map((lane) => lane.service).join("|");
  if (shape !== tapeState.shape || !host.firstChild) {
    tapeState.shape = shape;
    buildTapeRows(host, frame);
  }
  for (const lane of frame.services) {
    const row = tapeState.rows.get(lane.service);
    if (!row) continue;
    const up = lane.delta_pct >= 0;
    row.row.classList.toggle("spiking", Boolean(lane.spiking));
    row.polyline.setAttribute("points", tapeSparkPoints(lane.trend));
    rollFigure(row.rate, lane.rate, (v) => fmtNumber(v));
    row.delta.classList.toggle("up", up);
    row.delta.classList.toggle("down", !up);
    row.delta.textContent = `${up ? "▲" : "▼"}${Math.abs(lane.delta_pct).toFixed(1)}%`;
  }
}

/* Sim source only: today's projected point rides the tape, so the BIG
   trend chart and the cost rows breathe at tape cadence — history stays
   fixed, only today moves. */
async function refreshSimCosts() {
  try {
    const [costs, daily] = await Promise.all([
      fetchJson("/costs/summary"),
      fetchJson("/costs/daily"),
    ]);
    state.costs = costs;
    state.daily = daily;
    renderTrend();
    renderCosts(costs, new Set((state.anomalies || []).map((a) => a.service)));
    if (tapeState.lastFrame) feedCostLedgerFromTape(tapeState.lastFrame);
  } catch {
    /* a missed frame is fine — the next scan repaints anyway */
  }
}

async function pollTape() {
  const tape = document.getElementById("live-tape");
  if (!tape) return;
  try {
    const frame = await fetchJson("/stream/metrics");
    tape.hidden = false;
    tapeState.failures = 0;
    tapeState.everLive = true;
    tapeState.lastFrame = frame;
    tapeState.frames = (tapeState.frames || 0) + 1;
    // the honesty line lives under the rows, not in the heading — one short
    // sentence instead of a three-line label crowding the radar
    document.getElementById("tape-note").textContent =
      `${frame.unit} — synthetic figures, no real billing`;
    renderTape(frame);
    feedCostLedgerFromTape(frame);
    if (
      String(state.dataSources?.costs || "").startsWith("sim") &&
      tapeState.frames % 2 === 0
    ) {
      refreshSimCosts();
    }
    // The next frame is asked for by the animation clock, not by a standing
    // interval: the tape keeps ticking for as long as anyone is looking at
    // it and stops asking the moment the tab goes to the background, which
    // is the difference between a live strip and a leak.
    tapeState.polling = true;
    rafDelay("tape", (frame.interval_seconds || 2.5) * 1000, pollTape);
  } catch {
    tape.hidden = true;
    tapeState.failures += 1;
    // Never live: the flag is off (/stream/metrics 404s) — two tries and
    // the strip stays away for good, exactly as before. Once it HAS been
    // live, a dropped frame is weather, not a verdict: back off and keep
    // asking, because a tape that dies on one bad response is not a tape.
    if (!tapeState.everLive && tapeState.failures >= 2) {
      tapeState.polling = false;
      cancelRafDelay("tape");
      return;
    }
    rafDelay("tape", Math.min(30000, 2500 * 2 ** Math.min(tapeState.failures, 4)), pollTape);
  }
}
pollTape();
rafDelay("tape-retry", 15000, () => {
  // one late retry (slow first boot), then quiet
  if (!tapeState.polling && tapeState.failures < 2) pollTape();
});

document.getElementById("brain-review")?.addEventListener("click", async () => {
  const out = document.getElementById("brain-proposals");
  if (!out) return;
  out.textContent = "";
  try {
    const response = await fetch("/insights/self-review", { method: "POST" });
    const data = await response.json();
    const items = (data.proposals || []).map((p) => `[${p.area}] ${p.proposal}`);
    if (!items.length) {
      listPlaceholder(out, "no proposals — nothing to improve right now");
      return;
    }
    items.forEach((value) => {
      const li = document.createElement("li");
      li.textContent = value;
      out.appendChild(li);
    });
  } catch {
    listPlaceholder(out, "self-review unavailable");
  }
});

if (runbookInput) {
  let runbookTimer;
  runbookInput.addEventListener("input", (event) => {
    clearTimeout(runbookTimer);
    const value = event.target.value;
    runbookTimer = setTimeout(() => searchRunbooks(value), 250);
  });
}

// the sensitivity slider drives the backtest too — the bars move with it
thresholdInput?.addEventListener("change", renderBacktest);

// redraw from cache when the host's box changes: the observer catches the
// brain room becoming visible after first paint happened in another room
// (no window resize fires then), the debounced listener mirrors the trend
// chart's resize pattern. The observer is referenced so it cannot be GC'd.
let backtestResizeObserver = null;
if (typeof ResizeObserver !== "undefined") {
  const backtestHost = document.getElementById("backtest-table");
  if (backtestHost) {
    backtestResizeObserver = new ResizeObserver(() => drawBacktestChart());
    backtestResizeObserver.observe(backtestHost);
  }
}
let backtestResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(backtestResizeTimer);
  backtestResizeTimer = setTimeout(drawBacktestChart, 150);
});

document
  .getElementById("auth-register")
  ?.addEventListener("click", () => authAction("register"));
document
  .getElementById("auth-login")
  ?.addEventListener("click", () => authAction("login"));
document.getElementById("auth-logout")?.addEventListener("click", async () => {
  // Revoke server-side first so the token dies with the session, then clear
  // locally regardless of the outcome (the client must never stay "signed in").
  try {
    await fetch("/auth/logout", { method: "POST", headers: authHeaders() });
  } catch {
    /* offline — the local clear below still signs this browser out */
  }
  authToken = null;
  try {
    localStorage.removeItem("sentinel-token");
  } catch {
    /* storage unavailable */
  }
  refreshIdentity();
});

/* First paint: the ledger seeds and the empty-state panels do not depend on the
   API, so they render even if the very first scan fails. */
renderInvestigation();
renderDecisions();
renderAudit();
renderIntelligence();
renderWatch();
renderBrain();
renderRoutines();
renderSavedRoutines();
renderBacktest();
renderMarket();
refreshIdentity();
scan();

/* ======================================================================
   23 · the desk — the surface area, shown as objects
   ----------------------------------------------------------------------
   Six rooms is a good structure for someone who already knows the product
   and a poor one for someone meeting it: the rooms describe the *flow*,
   and a visitor's first question is what the thing can do at all. Eleven
   endpoints answered that question and appeared nowhere in the interface —
   the ledger's integrity proof, the decision-quality measures, the run
   receipts, the runbook hit rate, the watch's own vitals, the pre-flight
   sweep. They were reachable by URL and invisible by design accident.

   The desk is one screen that reads the estate: what it is holding, what
   it can prove about itself, and what is waiting on a human. Every row is
   fetched from the endpoint it names — a capability that cannot answer
   says so rather than showing a hopeful dash.
   ====================================================================== */

const deskState = { proofs: {}, lane: "all", loaded: false };

/* Each proof names its endpoint, how to reduce the answer to one line, and
   the lane it belongs to. Adding a capability here is one object — which is
   the point: the next endpoint should not be able to hide. */
const DESK_PROOFS = [
  {
    key: "audit",
    label: "Ledger integrity",
    note: "hash-chained decision trail",
    url: "/audit/verify",
    lane: "proof",
    read: (d) =>
      d.ok
        ? `${d.entries ?? d.checked ?? 0} sealed · intact`
        : `broken at #${d.first_break?.entry_id ?? "?"}`,
  },
  {
    key: "quality",
    label: "Decision quality",
    note: "acceptance, latency, calibration",
    url: "/analytics/quality",
    lane: "proof",
    read: (d) =>
      d.acceptance_rate == null
        ? "no verdicts yet"
        : `${Math.round(d.acceptance_rate * 100)}% of ${d.human_decisions} accepted`,
  },
  {
    key: "receipts",
    label: "Run receipts",
    note: "turns · milliseconds · calls",
    url: "/analytics/receipts",
    lane: "proof",
    read: (d) => {
      const runs = d.receipts || [];
      if (!runs.length) return "no runs recorded";
      const turns = runs.reduce((sum, run) => sum + (run.agent_turns || 0), 0);
      return `${d.count ?? runs.length} runs · ${turns} agent turns`;
    },
  },
  {
    key: "runbooks",
    label: "Runbook hit rate",
    note: "did the playbook help?",
    url: "/runbooks/effectiveness",
    lane: "proof",
    read: (d) => {
      const moving = (d.scores || []).filter((row) => row.adjustment);
      return moving.length
        ? `${moving.length} moving the ranking`
        : `${d.decisions_considered || 0} decisions, none past the bar`;
    },
  },
  {
    key: "watch",
    label: "Watch vitals",
    note: "is the sentinel still watching?",
    url: "/ops/health/watch",
    lane: "ops",
    read: (d) =>
      !d.configured
        ? "request-triggered (by choice)"
        : d.degraded
          ? `stale — ${Math.round(d.last_pulse_age_seconds || 0)}s since a beat`
          : "beating",
  },
  {
    key: "preflight",
    label: "Pre-flight",
    note: "the demo runbook as code",
    url: "/ops/preflight",
    lane: "ops",
    read: (d) => {
      const checks = d.checks || [];
      const bad = checks.filter((c) => c.status === "fail").length;
      return d.ok ? `${checks.length} checks clear` : `${bad} failing`;
    },
  },
  {
    key: "backtest",
    label: "Detector backtest",
    note: "scorers against planted truth",
    url: "/metrics/backtest",
    lane: "proof",
    read: (d) => {
      const scorers = new Set((d.rows || []).map((row) => row.mode));
      return scorers.size ? `${scorers.size} scorers compared` : "no ground truth loaded";
    },
  },
  {
    key: "telemetry",
    label: "Self telemetry",
    note: "its own traffic, as a dataset",
    url: "/telemetry/usage",
    lane: "ops",
    read: (d) => {
      const rows = d.daily_costs || d.usage || [];
      return rows.length ? `${rows.length} recorded days` : "nothing recorded yet";
    },
  },
];

async function loadDeskProofs() {
  await Promise.all(
    DESK_PROOFS.map(async (proof) => {
      try {
        const data = await fetchJson(proof.url);
        deskState.proofs[proof.key] = { ok: true, line: proof.read(data) };
      } catch {
        // an endpoint that cannot answer says so — never a hopeful dash
        deskState.proofs[proof.key] = { ok: false, line: "unavailable" };
      }
    })
  );
  deskState.loaded = true;
  renderDeskCapabilities();
}

function renderDeskCapabilities() {
  const host = document.getElementById("desk-capabilities");
  if (!host) return;
  host.innerHTML = DESK_PROOFS.map((proof) => {
    const result = deskState.proofs[proof.key];
    const value = result ? result.line : "reading…";
    const off = !result || !result.ok ? " is-off" : "";
    return `<li class="cs-row">
      <span>
        <a class="cs-row-name" href="${escapeHtml(proof.url)}">${escapeHtml(proof.label)}</a>
        <span class="cs-row-note">${escapeHtml(proof.note)}</span>
      </span>
      <span class="cs-row-value${off}">${escapeHtml(value)}</span>
    </li>`;
  }).join("");
}

/* The desk's three figures are built once and then only ever re-valued, so
   they can count to their new number instead of being thrown away and
   replaced by a different string. Rebuilding the markup on every scan is
   what made them snap. */
const DESK_STATS = [
  { key: "signals", label: "open signals" },
  { key: "pending", label: "awaiting you" },
  { key: "decided", label: "decided" },
];

function buildDeskStats(host) {
  host.replaceChildren(
    ...DESK_STATS.map((stat) => {
      const box = document.createElement("div");
      const figure = document.createElement("p");
      figure.className = "cs-stat-fig";
      figure.dataset.deskStat = stat.key;
      figure.dataset.rollKey = `desk-${stat.key}`;
      figure.dataset.v = "0"; // so the first paint counts up from nothing
      figure.textContent = "0";
      const label = document.createElement("p");
      label.className = "cs-stat-label";
      label.textContent = stat.label;
      box.append(figure, label);
      return box;
    })
  );
}

function renderDeskIdentity() {
  const stats = document.getElementById("desk-stats");
  if (!stats) return;
  const pending = state.actions.filter((a) => a.state === "proposed").length;
  const approved = state.actions.filter((a) => a.state === "approved" || a.state === "executed").length;
  if (!stats.querySelector("[data-desk-stat]")) buildDeskStats(stats);
  const figures = { signals: state.anomalies.length, pending, decided: approved };
  for (const stat of DESK_STATS) {
    rollFigure(stats.querySelector(`[data-desk-stat="${stat.key}"]`), figures[stat.key], (v) =>
      String(Math.round(v))
    );
  }
  const who = document.getElementById("desk-who");
  const sub = document.getElementById("desk-who-sub");
  if (who && sub) {
    const name = state.identity?.username;
    who.textContent = name ? `${name}'s desk` : "the operator's desk";
    sub.textContent = name
      ? `signed in as ${state.identity.role || "operator"} — every verdict carries this identity`
      : "not signed in — decisions carry the operator field";
  }
}

/* The feed: one card per thing the estate is holding, newest concern first.
   Lane badges carry the colour; the chips filter by lane without a refetch. */
const DESK_LANES = [
  { key: "all", label: "Everything" },
  { key: "cost", label: "Cost" },
  { key: "security", label: "Security" },
  { key: "fraud", label: "Fraud" },
  { key: "decision", label: "Decisions" },
  { key: "market", label: "Opportunities" },
];

function renderDeskChips() {
  const host = document.getElementById("desk-chips");
  if (!host) return;
  host.innerHTML = DESK_LANES.map(
    (lane) =>
      `<button class="cs-chip" type="button" data-desk-lane="${lane.key}" aria-pressed="${
        deskState.lane === lane.key
      }">${escapeHtml(lane.label)}</button>`
  ).join("");
}

function deskCard({ lane, badge, title, body, meta, href }) {
  const tag = href ? "a" : "div";
  const attrs = href ? ` href="${escapeHtml(href)}"` : "";
  return `<${tag} class="cs-card"${attrs} data-desk-item="${escapeHtml(lane)}">
    <div class="cs-card-head">
      <p class="cs-card-title">${escapeHtml(title)}</p>
      <span class="cs-badge" data-lane="${escapeHtml(lane)}">${escapeHtml(badge)}</span>
    </div>
    <p class="cs-card-sub">${escapeHtml(body)}</p>
    ${meta ? `<p class="meta">${escapeHtml(meta)}</p>` : ""}
  </${tag}>`;
}

function renderDeskFeed() {
  const host = document.getElementById("desk-feed");
  if (!host) return;
  const cards = [];

  state.anomalies.slice(0, 6).forEach((anomaly) => {
    cards.push(
      deskCard({
        lane: "cost",
        badge: `z ${Number(anomaly.z_score ?? 0).toFixed(1)}`,
        title: `${anomaly.service} — ${anomaly.date}`,
        body: `Spend of ${Number(anomaly.cost ?? 0).toFixed(2)} against a rolling baseline of ${Number(
          anomaly.baseline ?? 0
        ).toFixed(2)}. Deterministic detection, no model involved.`,
        meta: daysAgo(anomaly.date),
      })
    );
  });

  (state.security?.signals || []).slice(0, 3).forEach((signal) => {
    cards.push(
      deskCard({
        lane: "security",
        badge: signal.severity || "watch",
        title: `${signal.service || "estate"} — ${signal.metric || "security signal"}`,
        body: signal.summary || signal.detail || "A security signal through the same detection line as cost.",
        meta: signal.date || "",
      })
    );
  });

  (state.fraud?.signals || []).slice(0, 3).forEach((signal) => {
    cards.push(
      deskCard({
        lane: "fraud",
        badge: signal.risk || "rule score",
        title: signal.id ? `transaction ${signal.id}` : "fraud signal",
        body:
          signal.reason ||
          "Scored by published deterministic rules — arithmetic, never a model, and never the final word.",
        meta: signal.date || "",
      })
    );
  });

  state.actions
    .filter((action) => action.state === "proposed")
    .slice(0, 4)
    .forEach((action) => {
      cards.push(
        deskCard({
          lane: "decision",
          badge: "awaiting a hand",
          title: action.title,
          body: "Proposed and inert until an operator accepts or rejects it. Execution stays simulated by design.",
          meta: action.proposed_at || "",
          href: "/decide",
        })
      );
    });

  (state.market?.opportunities || []).slice(0, 3).forEach((row) => {
    cards.push(
      deskCard({
        lane: "market",
        badge: "opportunity",
        title: row.title || row.service || "standing opportunity",
        body: row.rationale || row.note || "A published market band costed against this estate's own run rate.",
        meta: row.saving ? `≈ ${row.saving}` : "",
      })
    );
  });

  host.innerHTML = cards.length
    ? cards.join("")
    : `<div class="cs-card"><p class="cs-card-sub">Nothing is open. Run a Pulse and the desk fills.</p></div>`;
  applyDeskFilter();
}

function applyDeskFilter() {
  document.querySelectorAll("[data-desk-item]").forEach((card) => {
    const show = deskState.lane === "all" || card.dataset.deskItem === deskState.lane;
    card.classList.toggle("view-hidden", !show);
  });
}

function renderDeskActionable() {
  const host = document.getElementById("desk-actionable");
  if (!host) return;
  const rows = [];
  const pending = state.actions.filter((a) => a.state === "proposed");
  rows.push({
    name: "Decisions waiting",
    note: "nothing runs unapproved",
    value: String(pending.length),
    href: "/decide",
  });
  const suggestions = state.reflexSuggestions?.proposed_rules?.length;
  if (suggestions) {
    rows.push({
      name: "Reflex rules drafted",
      note: "settled decisions, offered as rules",
      value: String(suggestions),
      href: "/brain",
    });
  }
  const contested = state.reflexSuggestions?.contested_signatures?.length;
  if (contested) {
    rows.push({
      name: "Contested signatures",
      note: "the same signal decided both ways",
      value: String(contested),
      href: "/brain",
    });
  }
  rows.push({
    name: "Incident report",
    note: "one decision, its whole trail",
    value: "download",
    href: "/decisions/export",
  });
  host.innerHTML = rows
    .map(
      (row) => `<li class="cs-row">
        <span>
          <a class="cs-row-name" href="${escapeHtml(row.href)}">${escapeHtml(row.name)}</a>
          <span class="cs-row-note">${escapeHtml(row.note)}</span>
        </span>
        <span class="cs-row-value">${escapeHtml(row.value)}</span>
      </li>`
    )
    .join("");
}

function renderDeskMarket() {
  const host = document.getElementById("desk-market");
  if (!host) return;
  const rows = (state.market?.opportunities || []).slice(0, 5);
  host.innerHTML = rows.length
    ? rows
        .map(
          (row) => `<li class="cs-row">
            <span>
              <span class="cs-row-name">${escapeHtml(row.title || row.service || "opportunity")}</span>
              <span class="cs-row-note">${escapeHtml(row.band || row.note || "published band")}</span>
            </span>
            <span class="cs-row-value">${escapeHtml(row.saving || "—")}</span>
          </li>`
        )
        .join("")
    : `<li class="cs-row"><span class="cs-row-note">nothing standing right now</span></li>`;
}

function renderDesk() {
  renderDeskIdentity();
  renderDeskChips();
  renderDeskFeed();
  renderDeskActionable();
  renderDeskMarket();
  renderDeskCapabilities();
}

/* ---------- the mega menu ---------- */
const megaToggle = document.getElementById("mega-toggle");
const megaPanel = document.getElementById("mega-panel");
if (megaToggle && megaPanel) {
  const closeMega = () => {
    megaPanel.hidden = true;
    megaToggle.setAttribute("aria-expanded", "false");
  };
  megaToggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = megaPanel.hidden;
    megaPanel.hidden = !open;
    megaToggle.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (event) => {
    if (!megaPanel.hidden && !megaPanel.contains(event.target)) closeMega();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMega();
  });
}

/* ---------- the desk's own controls ---------- */
document.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-desk-lane]");
  if (chip) {
    deskState.lane = chip.dataset.deskLane;
    renderDeskChips();
    applyDeskFilter();
  }
  if (event.target.closest("#desk-pulse")) document.getElementById("pulse-run")?.click();
  if (event.target.closest("#desk-rescan")) document.getElementById("rescan")?.click();
});

/* ======================================================================
   24 · accessibility — the settings an operating system cannot express
   ----------------------------------------------------------------------
   Text scale, line height, letter spacing, a plain face, highlighted links
   and headings, forced contrast, a reading mask, a larger pointer, motion
   off. Each one is a data attribute on <html> and a CSS variable, so this
   module never writes a style (the CSP forbids inline styles anyway) and
   never loads anything: the accessibility overlays sold as a one-line
   script are third-party code with a view of every keystroke on the page.

   Preferences live in localStorage under one key. If storage is blocked
   the panel still works for the session — it simply forgets, which is a
   better failure than refusing to open.
   ====================================================================== */

const A11Y_KEY = "sentinel-a11y";
const A11Y_SCALE_STEPS = [0.9, 1, 1.15, 1.3, 1.5, 1.75];
const A11Y_LINE_STEPS = [1, 1.15, 1.3, 1.5];
const A11Y_TRACK_STEPS = [0, 0.02, 0.05, 0.1];
const A11Y_TOGGLES = ["font", "mask", "contrast", "links", "headings", "cursor", "motion"];

const a11y = {
  scale: 1,
  line: 1,
  track: 0,
  font: null,
  mask: null,
  contrast: null,
  links: null,
  headings: null,
  cursor: null,
  motion: null,
};

function a11yLoad() {
  try {
    Object.assign(a11y, JSON.parse(localStorage.getItem(A11Y_KEY) || "{}"));
  } catch {
    /* storage unavailable or corrupt — the defaults above stand */
  }
}

function a11ySave() {
  try {
    localStorage.setItem(A11Y_KEY, JSON.stringify(a11y));
  } catch {
    /* best effort: the session keeps the setting, the next one will not */
  }
}

function a11yApply() {
  const root = document.documentElement;
  root.style.setProperty("--a11y-scale", String(a11y.scale));
  root.style.setProperty("--a11y-line", String(a11y.line));
  root.style.setProperty("--a11y-track", `${a11y.track}em`);
  // the attributes only exist when they are doing something, so the
  // selectors stay cheap and the DOM stays readable in devtools
  root.toggleAttribute("data-a11y-scale", a11y.scale !== 1);
  root.toggleAttribute("data-a11y-line", a11y.line !== 1);
  root.toggleAttribute("data-a11y-track", a11y.track !== 0);
  A11Y_TOGGLES.forEach((key) => {
    if (a11y[key]) root.setAttribute(`data-a11y-${key}`, a11y[key]);
    else root.removeAttribute(`data-a11y-${key}`);
  });

  const scaleOut = document.getElementById("a11y-scale-out");
  const lineOut = document.getElementById("a11y-line-out");
  const trackOut = document.getElementById("a11y-track-out");
  if (scaleOut) scaleOut.textContent = `${Math.round(a11y.scale * 100)}%`;
  if (lineOut) lineOut.textContent = `${Math.round(a11y.line * 100)}%`;
  if (trackOut) trackOut.textContent = `${a11y.track.toFixed(2)}em`;

  document.querySelectorAll("[data-a11y-toggle]").forEach((button) => {
    const on = Boolean(a11y[button.dataset.a11yToggle]);
    button.setAttribute("aria-pressed", String(on));
    const state = button.querySelector(".a11y-state");
    if (state) state.textContent = on ? "on" : "off";
  });

  const note = document.getElementById("a11y-motion-note");
  if (note) {
    const system = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    note.textContent = system
      ? "your system already asks for reduced motion — the site honours it either way"
      : "";
  }

  /* The motion switch is not only a stylesheet matter: the radar beam, the
     chart draw-ins and every rolling figure are JavaScript loops, and they
     have to hear this. Flipping it off settles them all to their finished
     frame on the spot; flipping it back on restarts the ambient ones. */
  syncMotion();
}

function a11yStep(kind, direction) {
  const steps = kind === "scale" ? A11Y_SCALE_STEPS : kind === "line" ? A11Y_LINE_STEPS : A11Y_TRACK_STEPS;
  const current = steps.indexOf(a11y[kind]);
  const index = Math.min(steps.length - 1, Math.max(0, (current === -1 ? 1 : current) + direction));
  a11y[kind] = steps[index];
  a11ySave();
  a11yApply();
}

function a11yReset() {
  Object.assign(a11y, { scale: 1, line: 1, track: 0 });
  A11Y_TOGGLES.forEach((key) => {
    a11y[key] = null;
  });
  a11ySave();
  a11yApply();
}

/* The reading mask follows the pointer: two dimmed bands with a lit strip
   between them. Written as custom properties on <html> rather than inline
   styles on the bands, so the CSP's style-src never has to widen. */
function a11yMaskMove(event) {
  if (!a11y.mask) return;
  const strip = 120;
  const top = Math.max(0, event.clientY - strip / 2);
  document.documentElement.style.setProperty("--mask-top", `${top}px`);
  document.documentElement.style.setProperty("--mask-bottom", `${top + strip}px`);
}

const a11yLaunch = document.getElementById("a11y-launch");
const a11yPanel = document.getElementById("a11y-panel");
if (a11yLaunch && a11yPanel) {
  a11yLaunch.addEventListener("click", () => {
    const open = a11yPanel.hidden;
    a11yPanel.hidden = !open;
    a11yLaunch.setAttribute("aria-expanded", String(open));
    if (open) a11yPanel.querySelector("button, [href]")?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !a11yPanel.hidden) {
      a11yPanel.hidden = true;
      a11yLaunch.setAttribute("aria-expanded", "false");
      a11yLaunch.focus();
    }
  });
  a11yPanel.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-a11y-toggle]");
    if (toggle) {
      const key = toggle.dataset.a11yToggle;
      a11y[key] = a11y[key] ? null : toggle.dataset.on;
      a11ySave();
      a11yApply();
      return;
    }
    const step = event.target.closest("[data-a11y-step]");
    if (step) {
      a11yStep(step.dataset.a11yStep, Number(step.dataset.dir));
      return;
    }
    if (event.target.closest("#a11y-reset")) a11yReset();
  });
  document.addEventListener("mousemove", a11yMaskMove, { passive: true });
}

a11yLoad();
a11yApply();

/* The desk's capability rows are fetched once at boot, then again after a
   Pulse — the proofs are cheap reads, but they are not free, so they do not
   ride the ten-second scan. */
loadDeskProofs();

/* ======================================================================
   25 · the mission posture — a control that says what it changed
   ----------------------------------------------------------------------
   Three missions once declared identical detection blocks, so the switch
   moved and nothing followed. They differ now, but a differing config is
   invisible unless the interface reads it back: this line states the
   posture the *server* resolved — detector, baseline window, the lane's
   own threshold — from the scan response rather than from a table in the
   client, so it cannot drift from what actually ran.

   It also names the one thing that confused everyone, including me: the
   sensitivity slider overrides the mission's threshold. Both numbers are
   shown when they disagree, because a control silently outranked by
   another control is how a demo loses an audience's trust.
   ====================================================================== */

const MISSION_LANE = { finops: "cost", security: "security event", fraud: "payment" };
const MISSION_DEFAULT_THRESHOLD = { finops: 2.0, security: 1.75, fraud: 2.75 };

function renderMissionPosture() {
  const host = document.getElementById("mission-posture");
  if (!host) return;
  const report = state.lastScan;
  const select = document.getElementById("mission-select");
  const mission = report?.mission || select?.value || "finops";
  const lane = MISSION_LANE[mission] || "cost";
  const parts = [`mission ${mission} — watches the ${lane} lane`];
  if (report?.detector) parts.push(`scores with ${report.detector}`);
  if (report?.window_days) parts.push(`${report.window_days}-day baseline`);
  const slider = parseFloat(thresholdInput.value);
  const missionDefault = MISSION_DEFAULT_THRESHOLD[mission];
  if (missionDefault != null && Math.abs(slider - missionDefault) > 0.001) {
    parts.push(
      `threshold ${slider.toFixed(2)} from the slider, overriding the mission's ${missionDefault.toFixed(2)}`
    );
  } else if (report?.threshold != null) {
    parts.push(`threshold ${Number(report.threshold).toFixed(2)}`);
  }
  host.textContent = parts.join(" · ");
}
