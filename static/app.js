/* CloudSentinel ledger — fetches /anomalies and /costs/summary and typesets the panels.
   The full agent chain runs live: section III triages with the Analyst and
   files Recommender proposals, section IV is the real HITL inbox
   (approve / reject / simulated execute against /actions), and section V
   keeps the audit trail. Nothing ever executes without an operator decision,
   and execution is simulated by design. */

const thresholdInput = document.getElementById("threshold");
const thresholdValue = document.getElementById("threshold-value");
const serviceFilter = document.getElementById("service-filter");
const rescanButton = document.getElementById("rescan");
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
  selectedIndex: 0,
  analyses: new Map(), // event id → Analyst agent report; survives re-renders
  analystBusy: new Set(), // event ids with an analyze request in flight
  recommendBusy: new Set(), // event ids with a recommend request in flight
  hitlBusy: new Set(), // action ids with a decision request in flight
  actions: [], // live HITL actions from GET /actions — feeds section IV
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

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>'"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[ch]));

const utcNow = () =>
  new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

function detailFor(service) {
  return detailsByService[String(service || "").trim().toLowerCase()] || detailsByService.network;
}

let actionsSequence = 0; // last-writer-wins: a stale /actions response must never overwrite a newer one

async function loadActions() {
  const sequence = ++actionsSequence;
  try {
    const report = await fetchJson("/actions");
    if (sequence !== actionsSequence) return; // superseded by a newer reload
    state.actions = report.actions;
  } catch {
    if (sequence !== actionsSequence) return;
    state.actions = []; // the inbox degrades to its empty state
  }
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
  document.getElementById("sum-signals").textContent = String(state.anomalies.length);
  document.getElementById("sum-pending").textContent = String(pending);
  if (state.costs) {
    document.getElementById("sum-total").innerHTML =
      `${fmtNumber(state.costs.total_cost)} <small>${escapeHtml(state.costs.currency)}</small>`;
    document.getElementById("sum-total-sub").textContent =
      `${state.costs.period.start} → ${state.costs.period.end}`;
  }
}

function renderAnomalies(report) {
  document.getElementById("anomaly-meta").textContent =
    `${report.records_analyzed} records scanned · threshold ${report.threshold.toFixed(2)} · ` +
    `${report.anomaly_count} anomal${report.anomaly_count === 1 ? "y" : "ies"} detected` +
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
        <p class="date">${escapeHtml(anomaly.date)}</p>
        <p class="figures">${fmtNumber(anomaly.cost)} <span class="dim">vs mean ${fmtNumber(anomaly.service_mean)}</span></p>
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
  ordered.forEach((service, index) => {
    const flagged = flaggedServices.has(service.service);
    const share = (service.share_of_total * 100).toFixed(1);
    const row = document.createElement("div");
    row.className = "cost-row";
    row.innerHTML = `
      <div class="cost-line">
        <span class="idx">${String(index + 1).padStart(2, "0")}</span>
        <span class="service">${escapeHtml(service.service)}${
          flagged
            ? '<span class="phantom-sq" aria-hidden="true"></span><span class="phantom-note">phantom traced</span>'
            : ""
        }</span>
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

/* ---------- SVG helpers (static ink — no animation) ---------- */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

function seriesPoints(values, width, height, pad) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
  return values.map((value, i) => ({
    x: pad + i * step,
    y: height - pad - ((value - min) / range) * (height - pad * 2),
  }));
}

function drawSeries(svg, values, { spikes = [], area = false } = {}) {
  svg.replaceChildren();
  const [, , width, height] = svg.getAttribute("viewBox").split(" ").map(Number);
  const pad = 6;
  [0.25, 0.5, 0.75].forEach((ratio) =>
    svg.append(svgEl("line", { class: "grid", x1: 0, x2: width, y1: Math.round(height * ratio), y2: Math.round(height * ratio) }))
  );
  const points = seriesPoints(values, width, height, pad);
  const path = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  if (area) {
    svg.append(svgEl("path", { class: "area", d: `${path} L ${points[points.length - 1].x.toFixed(1)},${height} L ${points[0].x.toFixed(1)},${height} Z` }));
  }
  svg.append(svgEl("path", { class: "line", d: path }));
  for (const spike of spikes) {
    const point = points[spike.index];
    if (point) svg.append(svgEl("circle", { class: `spike ${spike.severity}`, cx: point.x.toFixed(1), cy: point.y.toFixed(1), r: 3 }));
  }
  return points;
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
  const points = drawSeries(svg, totals, { spikes, area: true });

  const peakIndex = totals.indexOf(Math.max(...totals));
  const defaultReadout = `peak ${dates[peakIndex]} — ${fmtNumber(totals[peakIndex])} ${currency}`;
  readout.textContent = defaultReadout;

  const half = Math.floor(totals.length / 2);
  const firstHalf = totals.slice(0, half).reduce((sum, v) => sum + v, 0);
  const secondHalf = totals.slice(half).reduce((sum, v) => sum + v, 0);
  const delta = firstHalf ? ((secondHalf - firstHalf) / firstHalf) * 100 : 0;
  note.textContent = `spend ${delta >= 0 ? "rose" : "fell"} ${Math.abs(delta).toFixed(1)}% versus the first half of the period`;

  const probe = svgEl("line", { class: "probe", x1: 0, x2: 0, y1: 0, y2: 96, visibility: "hidden" });
  svg.append(probe);
  svg.onmousemove = (event) => {
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 460;
    let nearest = 0;
    points.forEach((p, i) => { if (Math.abs(p.x - x) < Math.abs(points[nearest].x - x)) nearest = i; });
    probe.setAttribute("x1", points[nearest].x.toFixed(1));
    probe.setAttribute("x2", points[nearest].x.toFixed(1));
    probe.setAttribute("visibility", "visible");
    readout.textContent = `${dates[nearest]} — ${fmtNumber(totals[nearest])} ${currency}`;
  };
  svg.onmouseleave = () => {
    probe.setAttribute("visibility", "hidden");
    readout.textContent = defaultReadout;
  };
}

function renderInvestigation() {
  signalRail.innerHTML = "";
  if (state.anomalies.length === 0) {
    signalRail.innerHTML = `<p class="meta">no open signal at this sensitivity</p>`;
    invDetail.innerHTML = `<p class="all-quiet">Nothing to investigate.</p>`;
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
      <svg class="spark-svg" id="spark-svg" viewBox="0 0 460 56" preserveAspectRatio="none" role="img" aria-label="Daily spend for ${escapeHtml(anomaly.service)} with the anomaly day marked"></svg>
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
    drawSeries(document.getElementById("spark-svg"), series.values, {
      spikes: anomalyIndex >= 0 ? [{ index: anomalyIndex, severity: anomaly.severity }] : [],
    });
    const mean = series.values.reduce((sum, v) => sum + v, 0) / series.values.length;
    document.getElementById("spark-stats").textContent =
      `min ${fmtNumber(Math.min(...series.values))} · mean ${fmtNumber(mean)} · ` +
      `max ${fmtNumber(Math.max(...series.values))} — anomaly day marked`;
  }
}

function renderRecommendationBlock(anomaly, action, analysis) {
  if (action) {
    const detail = action.detail || {};
    const preferred = (detail.options || []).find((o) => o.stance === detail.preferred);
    const savings = detail.savings || {};
    const saving =
      detail.preferred === "BOLD" ? savings.bold_monthly : savings.cautious_monthly;
    return `
      <div class="inv-block recommendation" style="grid-column: 1 / -1;">
        <p class="microcap">Recommended action — Recommender agent${detail.source === "fallback" ? " (fallback)" : ""}</p>
        <p class="rec-title">${escapeHtml(action.title)}</p>
        <p class="rec-facts">${preferred ? `stance ${escapeHtml(detail.preferred)} · est. saving ${fmtNumber(saving ?? 0)} / month · risk ${escapeHtml(preferred.risk)} · rollback ${escapeHtml(preferred.rollback)}` : `stance ${escapeHtml(detail.preferred || "—")}`}</p>
        ${detail.escalation_reason ? `<p class="meta">debate-lite: ${escapeHtml(detail.escalation_reason)}</p>` : ""}
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
    decisionList.innerHTML = `<p class="all-quiet">No filed proposal — investigate a signal and file a recommendation.</p>`;
    return;
  }

  state.actions.forEach((action) => {
    const detail = action.detail || {};
    const anomaly = detail.anomaly || {};
    const analysisReport = detail.analysis || {};
    const savings = detail.savings || {};
    const confidence = detail.confidence || {};
    const preferred = (detail.options || []).find((o) => o.stance === detail.preferred);
    const saving =
      detail.preferred === "BOLD" ? savings.bold_monthly : savings.cautious_monthly;
    const busy = state.hitlBusy.has(action.id);
    const severity = anomaly.severity || "warning";
    const resolved = action.state === "rejected" || action.state === "executed";
    const card = document.createElement("article");
    card.className = `decision ${severity} ${resolved ? "resolved" : ""} ${action.state}`;
    // evidence pack: anomaly + triage + reasoning + options on ONE card
    card.innerHTML = `
      <span class="sq" aria-hidden="true"></span>
      <div>
        <p class="dec-title">${escapeHtml(anomaly.service || "service")} — ${escapeHtml(action.title)}</p>
        <p class="dec-copy">observed ${escapeHtml(anomaly.date || "—")} · z ${anomaly.z_score != null ? Number(anomaly.z_score).toFixed(2) : "—"} · triage ${escapeHtml(analysisReport.triage || "—")} — ${escapeHtml(analysisReport.summary || "no analyst summary recorded")}</p>
        <p class="dec-facts"><span>stance ${escapeHtml(detail.preferred || "—")}</span><span>risk ${escapeHtml(preferred ? preferred.risk : "—")}</span><span>est. saving ${fmtNumber(saving ?? 0)} / month</span><span>confidence ${confidence.score != null ? Math.round(confidence.score * 100) : "—"}%</span></p>
        ${preferred ? `<p class="meta">rollback ${escapeHtml(preferred.rollback)}</p>` : ""}
        ${detail.escalation_reason ? `<p class="meta">debate-lite: ${escapeHtml(detail.escalation_reason)}${detail.transcript ? ` — skeptic ${detail.transcript.agreed ? "agreed" : "revised the stance"}` : ""}</p>` : ""}
      </div>
      <div class="dec-rail">
        <p class="dec-status">${escapeHtml(actionStatusLine(action))}</p>
        ${action.state === "proposed" && !busy ? `
          <button class="row-action" type="button" data-hitl="reject" data-action-id="${action.id}" aria-label="reject the ${escapeHtml(anomaly.service || "")} proposal">reject ×</button>
          <button class="row-action" type="button" data-hitl="approve" data-action-id="${action.id}" aria-label="approve the ${escapeHtml(anomaly.service || "")} proposal for execution">approve →</button>` : ""}
        ${action.state === "approved" && !busy ? `
          <button class="row-action" type="button" data-hitl="execute" data-action-id="${action.id}" aria-label="run the simulated execution of the ${escapeHtml(anomaly.service || "")} action">execute — simulation →</button>` : ""}
        ${busy ? `<p class="meta">recording…</p>` : ""}
      </div>`;
    decisionList.appendChild(card);
  });
}

function renderAudit() {
  auditList.innerHTML = state.audit
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
}

function renderAll(report) {
  renderCosts(state.costs, renderAnomalies(report));
  renderTrend();
  renderSummary();
  renderInvestigation();
  renderDecisions();
  renderAudit();
}

/* ---------- actions ---------- */

async function decideAction(actionId, verb) {
  if (state.hitlBusy.has(actionId)) return;
  state.hitlBusy.add(actionId);
  renderDecisions();
  try {
    const response = await fetch(`/actions/${actionId}/${verb}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    });
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
    state.audit.unshift({ time: utcNow(), title: titles[verb], copy: copies[verb] });
  } catch (error) {
    state.audit.unshift({
      time: utcNow(),
      title: "Decision request failed",
      copy: `${error.message} — the inbox reloads with the authoritative state.`,
    });
  } finally {
    state.hitlBusy.delete(actionId);
    await loadActions();
    renderSummary();
    renderInvestigation();
    renderDecisions();
    renderAudit();
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
        `Category ${recommendation.category} · est. saving ${recommendation.preferred === "BOLD" ? recommendation.savings.bold_monthly : recommendation.savings.cautious_monthly} / month` +
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
    await loadActions();
    renderSummary();
    renderInvestigation();
    renderDecisions();
    renderAudit();
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
  anomalyList.style.opacity = "0.35";
  costBars.style.opacity = "0.35";
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
    ]);
    if (sequence !== scanSequence) return;
    state.costs = costs;
    state.daily = daily;
    state.anomalies = anomalies.anomalies;
    state.allAnomalies = unfiltered ? unfiltered.anomalies : anomalies.anomalies;
    populateServiceFilter();
    renderAll(anomalies);
    editionLine.textContent = "SYSTEM ONLINE — MOCK DATA — SPRINT II";
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

thresholdInput.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdInput.value).toFixed(2);
});
thresholdInput.addEventListener("change", scan);
serviceFilter.addEventListener("change", scan);
rescanButton.addEventListener("click", scan);

document.addEventListener("click", (event) => {
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

  const hitlAction = event.target.closest("[data-hitl]");
  if (hitlAction) {
    decideAction(Number(hitlAction.dataset.actionId), hitlAction.dataset.hitl);
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
  }
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
    renderInvestigation();
    renderAudit();
  }
}

/* First paint: the ledger seeds and the empty-state panels do not depend on the
   API, so they render even if the very first scan fails. */
renderInvestigation();
renderDecisions();
renderAudit();
scan();
