/* CloudSentinel ledger — fetches /anomalies and /costs/summary and typesets the panels.
   The full agent chain runs live: section III triages with the Analyst and
   files Recommender proposals, section IV is the real HITL inbox
   (approve / reject / simulated execute against /actions), and section V
   keeps the audit trail. Nothing ever executes without an operator decision,
   and execution is simulated by design. */

/* Palette: ?theme=mission|paper|horizon|dawn still wins so review links keep
   working; otherwise the choice persisted from the colophon switch applies.
   The default identity stays horizon — the switch promotes night (mission)
   and paper from hidden preview flags to first-class modes. */
const THEMES = ["horizon", "mission", "paper", "dawn"];

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll("[data-theme-choice]").forEach((button) =>
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme))
  );
}

const themeParam = new URLSearchParams(location.search).get("theme");
let storedTheme = null;
try {
  storedTheme = localStorage.getItem("sentinel-theme");
} catch {
  /* storage can be unavailable (private mode) — the default carries */
}
applyTheme(
  THEMES.includes(themeParam) ? themeParam : THEMES.includes(storedTheme) ? storedTheme : "horizon"
);

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
  env: "local", // deploy environment from /health — drives the LIVE banner
  readonly: false, // SENTINEL_READONLY showcase mode — writes are disabled
  auditExpanded: false, // section V shows the newest entries until asked
  audit: [
    { time: "scan", title: "Cost Agent completed the scheduled scan", copy: "Every monitored service was compared against its historical baseline." },
    { time: "scan", title: "Anomaly policy applied", copy: "Signals at or above the configured z-score threshold entered the review queue." },
    { time: "policy", title: "Human approval boundary enforced", copy: "No recommendation can execute while an operator decision is pending." },
  ],
};

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

const fmtNumber = (value) =>
  value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

/* Living figures: numbers ROLL to their new value instead of snapping —
   the page moves because the data moves. Falls back to a plain set under
   reduced motion or on the very first paint. */
function rollFigure(el, value, render) {
  const previous = Number(el.dataset.v);
  el.dataset.v = String(value);
  if (REDUCED_MOTION.matches || Number.isNaN(previous) || previous === value) {
    el.innerHTML = render(value);
    return;
  }
  const started = performance.now();
  const duration = 550;
  const step = (now) => {
    const t = Math.min(1, (now - started) / duration);
    const eased = 1 - (1 - t) ** 3;
    el.innerHTML = render(previous + (value - previous) * eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>'"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[ch]));

const utcNow = () =>
  new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";

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

let actionsSequence = 0; // last-writer-wins: a stale /actions response must never overwrite a newer one

async function loadActions() {
  const sequence = ++actionsSequence;
  try {
    const report = await fetchJson("/actions");
    if (sequence !== actionsSequence) return; // superseded by a newer reload
    state.actions = report.actions;
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

let intelSequence = 0; // last-writer-wins: stale analytics must never overwrite newer

async function loadIntelligence() {
  const sequence = ++intelSequence;
  try {
    const [analytics, trend, aiUsage, forecast, calibration, headline] = await Promise.all([
      fetchJson("/analytics/decisions"),
      fetchJson("/analytics/costs/trend"),
      fetchJson("/analytics/ai"),
      fetchJson("/analytics/costs/forecast"),
      fetchJson("/analytics/calibration").catch(() => null),
      fetchJson("/analytics/headline").catch(() => null),
    ]);
    if (sequence !== intelSequence) return;
    state.analytics = analytics;
    state.trend = trend;
    state.aiUsage = aiUsage;
    state.forecast = forecast;
    state.calibration = calibration;
    state.headline = headline;
    state.intelStale = false;
  } catch {
    if (sequence !== intelSequence) return;
    // keep the last successful figures; the render marks the feed stale
    state.intelStale = true;
  }
}

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
      <p class="watch-row ${signal.severity === "critical" ? "critical" : ""}">
        <span class="watch-glyph" aria-hidden="true">▣</span><span class="watch-strong">${escapeHtml(signal.service)}</span> · ${escapeHtml(signal.date)} ·
        ${fmtNumber(signal.count)} events vs ${fmtNumber(signal.baseline)} baseline · z ${signal.z_score.toFixed(2)} · ${escapeHtml(signal.severity)}${costSpikeDates.has(signal.date) ? ` · <span class="watch-strong">⇄ cost spike same day</span>` : ""}
      </p>`
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
    `<p class="meta watch-head">fraud — published rules${bandLine} · mission ${escapeHtml(fraud.mission ?? "—")} · suggestions only, the operator decides</p>` +
    flagged
      .map(
        (signal) => `
    <p class="watch-row" title="${escapeHtml(signal.reasons.join(" · "))}">
      <span class="watch-glyph" aria-hidden="true">▣</span><span class="watch-strong">${escapeHtml(signal.id)}</span> · ${fmtNumber(signal.amount)} USD ·
      score ${signal.score} — ${escapeHtml(signal.band === "hold_suggested" ? "hold suggested" : signal.band)}${
        signal.rule_hits && signal.rule_hits.length
          ? ` · ${escapeHtml(signal.rule_hits.map((hit) => `${hit.rule.replace("_", " ")} +${hit.points}`).join(" · "))}`
          : ` · ${escapeHtml(signal.reasons.join(" · "))}`
      }
    </p>`
      )
      .join("");
}

function actionForEvent(eventId) {
  if (eventId == null) return undefined;
  // the newest non-rejected action mirrors the backend's reuse lane
  return [...state.actions]
    .reverse()
    .find((action) => action.event_id === eventId && action.state !== "rejected");
}

/* ---------- renderers ---------- */

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
  document.getElementById("anomaly-meta").textContent =
    `${report.records_analyzed} records scanned · threshold ${report.threshold.toFixed(2)} · ` +
    // type counter: cost signals here plus the unified security lane
    `${report.anomaly_count} cost` +
    (state.security ? ` · ${state.security.signal_count} security` : "") +
    ` signal${report.anomaly_count === 1 && (!state.security || state.security.signal_count === 0) ? "" : "s"} detected` +
    // measured, not claimed: the API reports how long the reflex pass took
    (typeof report.reflex_ms === "number" ? ` · REFLEX ${report.reflex_ms.toFixed(1)} ms` : "") +
    (serviceFilter.value ? ` · service ${serviceFilter.value}` : "");

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
  document.getElementById("cost-meta").textContent =
    `${report.period.start} → ${report.period.end} · ${report.services.length} services`;

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

/* ---------- SVG helpers (precise static ink) ---------- */

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

  if (area) {
    const defs = svgEl("defs", {});
    const gradient = svgEl("linearGradient", { id: "trend-fill", x1: 0, y1: 0, x2: 0, y2: 1 });
    const stopHi = svgEl("stop", { offset: "0" });
    const stopLo = svgEl("stop", { offset: "1" });
    stopHi.style.stopColor = "var(--chart-area-hi)";
    stopLo.style.stopColor = "var(--chart-area-lo)";
    gradient.append(stopHi, stopLo);
    defs.append(gradient);
    const floor = height - pad.bottom;
    svg.append(
      defs,
      svgEl("path", { class: "area", d: `${path} L ${points[points.length - 1].x.toFixed(1)},${floor} L ${points[0].x.toFixed(1)},${floor} Z` })
    );
  }

  svg.append(svgEl("path", { class: "line", d: path }));
  for (const spike of spikes) {
    const point = points[spike.index];
    if (point) {
      svg.append(svgEl("circle", { class: `spike ${spike.severity}`, cx: point.x.toFixed(1), cy: point.y.toFixed(1), r: 3.5 }));
    }
  }
  return { points, scale };
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
    return;
  }
  const { dates, totals, currency } = state.daily;
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
  const { points } = drawSeries(svg, totals, { spikes, area: true, axes: { dates } });

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
    // flip the balloon when the probe nears the right edge
    const flip = point.x > viewWidth - 124;
    const bx = flip ? point.x - 116 : point.x + 8;
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
      <button class="row-action" type="button" data-request-evidence ${anomaly.id != null && state.analystBusy.has(anomaly.id) ? "disabled" : ""}>${
        anomaly.id != null && state.analystBusy.has(anomaly.id)
          ? "analyst working…"
          : analysis ? "re-run analyst →" : "run analyst agent →"
      }</button>
      ${analysis && !action
        ? `<button class="row-action" type="button" data-request-recommend ${state.recommendBusy.has(anomaly.id) ? "disabled" : ""}>${
            state.recommendBusy.has(anomaly.id) ? "recommender working…" : "file recommendation →"
          }</button>`
        : ""}
      ${action ? `<a class="row-action" href="#sec-decisions">decide in the inbox ↓</a>` : ""}
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
    drawSeries(spark, series.values, {
      spikes: [
        ...cited.map((index) => ({ index, severity: "cited" })),
        ...(anomalyIndex >= 0
          ? [{ index: anomalyIndex, severity: anomaly.severity }]
          : []),
      ],
      band: { mean, sigma },
    });
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
  return `<details class="transcript"><summary>skeptic reviewed this — ${transcript.agreed ? "consensus" : "stance revised"}</summary>
       <p class="meta">trigger — ${escapeHtml(transcript.trigger || "")}</p>
       <p class="body">${escapeHtml(transcript.skeptic_rationale || "")}</p>
       <p class="meta">${
         transcript.agreed
           ? `agreed with the ${escapeHtml(transcript.original_preferred || "draft")} stance`
           : `revised the stance ${escapeHtml(transcript.original_preferred || "")} → ${escapeHtml(transcript.final_preferred || "")}`
       }</p>
     </details>`;
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
    const bits = [entry.step, entry.source === "fallback" ? "rule-based fallback" : entry.source];
    if (entry.from_cache) bits.push("cached");
    if (entry.reflected) bits.push("reflection pass");
    if (entry.step === "skeptic") bits.push(entry.revised ? "stance revised" : "consensus");
    if (typeof entry.duration_ms === "number") bits.push(`${entry.duration_ms.toFixed(0)} ms`);
    return bits.join(" · ");
  };
  return `<details class="transcript"><summary>agent chain — ${trace.length} hop${trace.length === 1 ? "" : "s"}, traced</summary>${trace
    .map((entry) => `<p class="meta">${escapeHtml(label(entry))}</p>`)
    .join("")}</details>`;
}

function memoryFold(detail) {
  const memory = detail.memory;
  if (!memory || !memory.count) return "";
  return `<details class="transcript"><summary>decision memory — ${memory.count} prior verdict${
    memory.count === 1 ? "" : "s"
  } shaped this proposal</summary>${memory.entries
    .map((line) => `<p class="meta">${escapeHtml(line)}</p>`)
    .join("")}</details>`;
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

function actionStatusLine(action) {
  if (action.state === "proposed") return "awaiting the hand";
  if (action.state === "approved") return `approved · ${action.decided_by || "operator"} — ready for simulated execution`;
  if (action.state === "executed") return "executed — SIMULATION";
  if (action.decided_by === "system:timeout") return "expired — proposal timed out unanswered";
  return `rejected · ${action.decided_by || "operator"}`;
}

function renderDecisions() {
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
    card.className = `decision ${severity} ${resolved ? "resolved" : ""} ${action.state}`;
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
      <div>${bodyHtml}
      </div>
      <div class="dec-rail">
        <span class="chip ${action.decided_by === "system:timeout" ? "expired" : action.state}">${
          action.state === "executed"
            ? "executed — simulation"
            : action.decided_by === "system:timeout"
              ? "expired"
              : escapeHtml(action.state)
        }</span>
        <p class="dec-status">${escapeHtml(actionStatusLine(action))}</p>
        ${action.expires_in_hours != null ? `<p class="meta">${action.expires_in_hours >= 48 ? `expires in ~${Math.round(action.expires_in_hours / 24)}d` : `expires in ~${Math.max(0, Math.round(action.expires_in_hours))}h`}</p>` : ""}
        ${action.event_id != null ? `<button class="row-action" type="button" data-view-signal="${action.event_id}" aria-label="jump to the ${escapeHtml(anomaly.service || "")} signal in investigation">view signal ↑</button>` : ""}
        ${action.state === "proposed" && !busy && !state.readonly ? `
          <input type="text" class="rationale-input" placeholder="rationale — feeds decision memory" maxlength="500" data-rationale-for="${action.id}" aria-label="rationale for the ${escapeHtml(anomaly.service || "")} decision" />
          <button class="row-action" type="button" data-hitl="reject" data-action-id="${action.id}" aria-label="reject the ${escapeHtml(anomaly.service || "")} proposal">reject ×</button>
          <button class="row-action" type="button" data-hitl="approve" data-action-id="${action.id}" aria-label="approve the ${escapeHtml(anomaly.service || "")} proposal for execution">approve →</button>` : ""}
        ${action.state === "approved" && !busy && !state.readonly ? `
          <button class="row-action" type="button" data-hitl="execute" data-action-id="${action.id}" aria-label="run the simulated execution of the ${escapeHtml(anomaly.service || "")} action">execute — simulation →</button>` : ""}
        ${state.readonly && (action.state === "proposed" || action.state === "approved") ? `<p class="meta">read-only demo — decisions disabled</p>` : ""}
        ${busy ? `<p class="meta">recording…</p>` : ""}
      </div>`;
    decisionList.appendChild(card);
  });
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
    ["executed", funnel.executed],
  ];
  funnelBox.innerHTML =
    `<div class="funnel-row">` +
    cells
      .slice(0, 3)
      .map(([label, value]) => `<div class="funnel-cell"><p class="microcap">${label}</p><p class="funnel-fig">${value}</p></div>`)
      .join("") +
    `</div><div class="funnel-row">` +
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

  const currency = state.costs ? state.costs.currency : "USD";
  savingsFig.innerHTML = `${fmtNumber(quality.approved_estimated_monthly_savings)} <small>${escapeHtml(currency)} / mo</small>`;

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

function renderAll(report) {
  renderCosts(state.costs, renderAnomalies(report));
  renderTrend();
  renderSummary();
  renderInvestigation();
  renderDecisions();
  renderAudit();
  renderIntelligence();
  renderWatch();
}

/* ---------- actions ---------- */

async function decideAction(actionId, verb) {
  if (state.hitlBusy.has(actionId)) return;
  // capture the rationale BEFORE the busy re-render replaces the input
  const rationale =
    document.querySelector(`[data-rationale-for="${actionId}"]`)?.value.trim() || null;
  const actor = (operatorInput?.value || "").trim() || "operator";
  state.hitlBusy.add(actionId);
  renderDecisions();
  try {
    const response = await fetch(`/actions/${actionId}/${verb}`, {
      method: "POST",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ actor, rationale }),
    });
    if (response.status === 409) {
      // idempotency guard: the state machine already recorded a verdict
      const conflict = await response.json().catch(() => ({}));
      state.audit.unshift({
        time: utcNow(),
        title: "Decision already recorded — guard held",
        copy: `${conflict.detail || "the action is no longer decidable"}; the inbox reloads the authoritative state.`,
      });
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const record = await response.json();
    const service = record.detail?.anomaly?.service || "the flagged service";
    const titles = {
      approve: `Operator approved the ${service} proposal`,
      reject: `Operator rejected the ${service} proposal`,
      execute: `Simulated execution completed for ${service}`,
    };
    const copies = {
      approve: "The action is approved and ready for simulated execution — nothing runs on real infrastructure.",
      reject: "The proposal was closed with no infrastructure action.",
      execute: "SIMULATION only: the state machine recorded the execution; no real resource was touched.",
    };
    state.audit.unshift({
      time: utcNow(),
      title: titles[verb],
      copy: copies[verb] + (rationale ? ` Rationale: ${rationale}` : ""),
    });
  } catch (error) {
    state.audit.unshift({
      time: utcNow(),
      title: "Decision request failed",
      copy: `${error.message} — the inbox reloads with the authoritative state.`,
    });
  } finally {
    state.hitlBusy.delete(actionId);
    await Promise.all([loadActions(), loadIntelligence()]);
    renderSummary();
    renderInvestigation();
    renderDecisions();
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
    state.audit.unshift({
      time: utcNow(),
      title: `Recommender filed a ${recommendation.preferred} proposal for ${anomaly.service}`,
      copy:
        `Category ${recommendation.category} · est. saving ${preferredMonthlySaving(recommendation)} / month` +
        `${recommendation.escalation_reason ? " · debate-lite: " + recommendation.escalation_reason : ""}` +
        `${recommendation.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}.`,
    });
  } catch (error) {
    state.audit.unshift({
      time: utcNow(),
      title: "Recommender request failed",
      copy: `${error.message} — no proposal was filed.`,
    });
  } finally {
    state.recommendBusy.delete(anomaly.id);
    await Promise.all([loadActions(), loadIntelligence()]);
    renderSummary();
    renderInvestigation();
    renderDecisions();
    renderAudit();
    renderIntelligence();
  }
}

function populateServiceFilter() {
  if (!state.costs || serviceFilter.options.length > 1) return;
  for (const service of state.costs.services) {
    const option = document.createElement("option");
    option.value = service.service;
    option.textContent = service.service;
    serviceFilter.appendChild(option);
  }
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
    editionLine.textContent =
      `SYSTEM ONLINE — ${state.env === "render" ? "LIVE ON RENDER — " : ""}` +
      `${state.readonly ? "READ-ONLY DEMO — " : ""}` +
      `LAST SCAN ${utcNow()} — MOCK DATA`;
    editionLine.classList.remove("down");
  } catch (error) {
    if (sequence !== scanSequence) return;
    editionLine.textContent = "LINK LOST — MOCK DATA — SPRINT II";
    editionLine.classList.add("down");
    anomalyList.innerHTML = `<p class="error-note">Signal lost — ${escapeHtml(error.message)}.</p>`;
    document.getElementById("anomaly-meta").textContent = "scan failed — the panels keep the last successful scan";
    if (!state.costs) document.getElementById("cost-meta").textContent = "signal lost";
  } finally {
    if (sequence === scanSequence) {
      anomalyList.style.opacity = "1";
      costBars.style.opacity = "1";
    }
  }
}

/* ---------- events ---------- */

let pulseBusy = false;

async function runPulse() {
  /* One click, the whole chain: detect → analyze → recommend. Decisions
     stay in the inbox — pulse files proposals, it never approves them. */
  if (pulseBusy) return;
  pulseBusy = true;
  pulseButton.disabled = true;
  pulseButton.textContent = "pulse running…";
  try {
    const response = await fetch("/pulse", { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const report = await response.json();
    // run ledger: each chain hop lands in section V under the summary
    [...report.chain].reverse().forEach((link) => {
      state.audit.unshift({
        time: utcNow(),
        title: `Pulse chain: ${link.service} → ${link.triage} → action #${link.action_id}`,
        copy: `severity ${link.severity} · preferred ${link.preferred} · ${
          link.reused ? "existing proposal reused" : "new proposal filed"
        } — state ${link.action_state}.`,
      });
    });
    state.audit.unshift({
      time: utcNow(),
      title: `Pulse swept the estate — ${report.signals} cost + ${report.security_signals} security + ${report.fraud_signals ?? 0} fraud signals`,
      copy:
        `mission ${report.mission ?? "—"} · REFLEX ${report.reflex_ms ?? "—"} ms · ` +
        `${report.analyzed} analyzed · ${report.proposals_filed} filed · ${report.proposals_reused} reused · ` +
        `${(report.fraud_holds_filed ?? 0) + (report.budget_cards_filed ?? 0) ? `${report.fraud_holds_filed ?? 0} fraud hold(s) + ${report.budget_cards_filed ?? 0} budget card(s) filed · ` : ""}` +
        `LLM ${report.llm_calls_used}/${report.llm_budget}${
          report.budget_exhausted ? " — budget exhausted, fallbacks answered" : ""
        }.`,
    });
    // the chronicler narrates the run — its briefing tops the ledger
    if (report.briefing) {
      state.audit.unshift({
        time: utcNow(),
        title: `Chronicler briefing — ${report.briefing.headline}`,
        copy:
          `${report.briefing.summary} Watch next: ${report.briefing.watch_next}` +
          `${report.briefing.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}`,
      });
    }
    pulseNote.textContent = pulseNoteLine(report, utcNow());
  } catch (error) {
    state.audit.unshift({
      time: utcNow(),
      title: "Pulse request failed",
      copy: `${error.message} — the panels keep their last state.`,
    });
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

thresholdInput.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdInput.value).toFixed(2);
});
thresholdInput.addEventListener("change", scan);
serviceFilter.addEventListener("change", scan);
rescanButton.addEventListener("click", scan);
pulseButton.addEventListener("click", runPulse);

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
    document.querySelectorAll("[data-anomaly-sort]").forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.anomalySort === state.anomalySort))
    );
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

  const copyBrief = event.target.closest("#copy-brief");
  if (copyBrief && state.headline) {
    navigator.clipboard
      .writeText(state.headline.headline)
      .then(() => {
        state.audit.unshift({
          time: utcNow(),
          title: "Jury brief copied to the clipboard",
          copy: state.headline.headline,
        });
        renderAudit();
      })
      .catch(() => {
        /* clipboard can be unavailable — the headline stays visible in VI */
      });
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
    document.querySelectorAll("[data-sort]").forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.sort === state.sortMode))
    );
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
    state.audit.unshift({
      time: utcNow(),
      title: `Analyst agent triaged the ${anomaly.service} signal — ${analysis.triage}`,
      copy:
        `Confidence ${analysis.confidence.score.toFixed(2)}` +
        `${analysis.reflected ? " · reflection pass applied" : ""}` +
        `${analysis.source === "fallback" ? " · rule-based fallback (LLM unavailable)" : ""}` +
        `${analysis.from_cache ? " · served from cache" : ""}.`,
    });
  } catch (error) {
    state.audit.unshift({
      time: utcNow(),
      title: "Analyst agent request failed",
      copy: `${error.message} — the panel keeps its previous narrative.`,
    });
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

/* Deploy environment drives the LIVE banner: read it once, then re-render the
   edition line on the next scan. Best-effort — the default stays "local". */
fetchJson("/health")
  .then((health) => {
    state.env = health.env || "local";
    state.readonly = Boolean(health.readonly);
    if (state.readonly) {
      pulseButton.disabled = true;
      pulseButton.title = "read-only demo — the pulse chain is disabled";
      renderDecisions();
    }
  })
  .catch(() => {
    /* health unreachable — the banner stays in its local form */
  });

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

function pulseNoteLine(report, when) {
  // the briefing headline already narrates the lane counts — no repetition
  const story = report.briefing
    ? report.briefing.headline
    : `${report.signals} cost + ${report.security_signals} security + ${report.fraud_signals ?? 0} fraud signals`;
  return `last pulse ${when} — ${story} · LLM ${report.llm_calls_used}/${report.llm_budget}`;
}

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

/* ---------- view navigation (rooms of the broadsheet) ----------
   Hash-tab views over ONE page: no routes, no reload — sections toggle,
   the print view always shows the whole broadsheet. */

const VIEW_SECTIONS = {
  watch: ["sec-anomalies", "sec-costs"],
  investigate: ["sec-investigation"],
  decide: ["sec-decisions", "sec-ledger"],
  intel: ["sec-intelligence"],
};
const ALL_SECTIONS = [...new Set(Object.values(VIEW_SECTIONS).flat())];
const VIEW_TITLES = {
  watch: "Watch",
  investigate: "Investigation",
  decide: "Decision Desk",
  intel: "Intelligence",
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
  document.title = `CloudSentinel — ${VIEW_TITLES[view] || "Anomaly Watch"}`;
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
window.addEventListener("popstate", () => applyView(viewFromPath(location.pathname)));
applyView(viewFromPath(location.pathname));

/* The watchroom never sleeps: a quiet background re-scan keeps every
   figure current (and rolling) without a hand on the controls. */
const AUTO_SCAN_MS = 60000;
setInterval(() => {
  if (!document.hidden && !pulseBusy) scan();
}, AUTO_SCAN_MS);

/* ---------- live agent feed (right rail) ----------
   The agent bus persists every inter-agent event as it happens; this panel
   polls the cursor endpoint (plain polling, no sockets) so a running pulse
   streams its conversation into the rail in near-real time. */

const feedToggle = document.getElementById("feed-toggle");
const feedBody = document.getElementById("feed-body");
const feedList = document.getElementById("feed-list");
const feedEmpty = document.getElementById("feed-empty");
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

/* First paint: the ledger seeds and the empty-state panels do not depend on the
   API, so they render even if the very first scan fails. */
renderInvestigation();
renderDecisions();
renderAudit();
renderIntelligence();
renderWatch();
scan();
