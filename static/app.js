/* CloudSentinel ledger — fetches /anomalies and /costs/summary and typesets the panels.
   Sections III–V (investigation, decision inbox, decision ledger) rehearse the
   Sprint II agent & HITL flow on a demo narrative layer: the cost/anomaly data
   is live from the API, the recommendations are simulated until the Sprint II
   endpoints exist, and nothing ever executes. */

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
  decisions: [],
  decisionMemory: new Map(), // id → {status, resolvedAt}; survives re-scans and filters
  audit: [
    { time: "scan", title: "Cost Agent completed the scheduled scan", copy: "Every monitored service was compared against its historical baseline." },
    { time: "scan", title: "Anomaly policy applied", copy: "Signals at or above the configured z-score threshold entered the review queue." },
    { time: "policy", title: "Human approval boundary enforced", copy: "No recommendation can execute while an operator decision is pending." },
  ],
};

/* Demo narrative until the Sprint II Analyst/Recommender endpoints exist. */
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

function decisionId(anomaly) {
  return `${String(anomaly.service).toLowerCase()}-${anomaly.date}`;
}

function reconcileDecisions() {
  state.decisions = state.anomalies.map((anomaly) => {
    const detail = detailFor(anomaly.service);
    const id = decisionId(anomaly);
    const remembered = state.decisionMemory.get(id);
    return {
      id,
      status: remembered ? remembered.status : "pending",
      resolvedAt: remembered ? remembered.resolvedAt : null,
      severity: anomaly.severity,
      service: anomaly.service,
      date: anomaly.date,
      title: detail.proposal,
      asset: detail.asset,
      risk: detail.risk,
      savings: detail.savings,
      confidence: detail.confidence,
    };
  });
}

/* ---------- renderers ---------- */

function renderSummary() {
  const pending = state.decisions.filter((d) => d.status === "pending").length;
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
  const decision = state.decisions[state.selectedIndex];
  const pending = decision && decision.status === "pending";

  invDetail.innerHTML = `
    <header class="inv-head">
      <div>
        <p class="microcap inv-kicker">signal ${String(state.selectedIndex + 1).padStart(3, "0")} · ${escapeHtml(anomaly.severity)}</p>
        <p class="inv-title">${escapeHtml(anomaly.service)} <em>cost anomaly</em></p>
        <p class="inv-asset">${escapeHtml(detail.asset)} · observed ${escapeHtml(anomaly.date)}</p>
      </div>
      <div class="confidence">
        <p class="conf-fig">${detail.confidence}<small>%</small></p>
        <p class="microcap">agent confidence</p>
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
        <p class="microcap">What happened</p>
        <p class="body">${escapeHtml(detail.reason)}</p>
      </div>
      <div class="inv-block">
        <p class="microcap">Security context</p>
        <p class="body">${escapeHtml(detail.security)}</p>
      </div>
      <div class="inv-block recommendation" style="grid-column: 1 / -1;">
        <p class="microcap">Recommended action — demo narrative</p>
        <p class="rec-title">${escapeHtml(detail.proposal)}</p>
        <p class="rec-facts">saving ${escapeHtml(detail.savings)} · risk ${escapeHtml(detail.risk)} · rollback ${escapeHtml(detail.rollback)}</p>
      </div>
    </div>

    <div class="inv-actions">
      <button class="row-action" type="button" data-request-evidence>request deeper analysis</button>
      ${pending
        ? `<button class="row-action" type="button" data-decision="reject" data-decision-index="${state.selectedIndex}" aria-label="reject the ${escapeHtml(anomaly.service)} proposal">reject proposal ×</button>
           <button class="row-action" type="button" data-decision="approve" data-decision-index="${state.selectedIndex}" aria-label="approve the ${escapeHtml(anomaly.service)} proposal for execution">approve for execution →</button>`
        : `<span class="dec-status">${decision ? escapeHtml(statusWord(decision)) : ""}</span>`}
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

function statusWord(decision) {
  if (decision.status === "approved") return `approved — demo only${decision.resolvedAt ? " · " + decision.resolvedAt : ""}`;
  if (decision.status === "rejected") return `rejected${decision.resolvedAt ? " · " + decision.resolvedAt : ""}`;
  return "awaiting the hand";
}

function renderDecisions() {
  const pending = state.decisions.filter((d) => d.status === "pending").length;
  document.getElementById("decision-meta").textContent = pending
    ? `${pending} proposal${pending === 1 ? "" : "s"} awaiting an accountable hand — nothing executes automatically`
    : "a proposed action stays inert until an operator accepts or rejects it";

  decisionList.innerHTML = "";
  if (state.decisions.length === 0) {
    decisionList.innerHTML = `<p class="all-quiet">No open proposal — no anomaly on watch.</p>`;
    return;
  }

  state.decisions.forEach((decision, index) => {
    const resolved = decision.status !== "pending";
    const card = document.createElement("article");
    card.className = `decision ${decision.severity} ${resolved ? "resolved" : ""} ${decision.status}`;
    card.innerHTML = `
      <span class="sq" aria-hidden="true"></span>
      <div>
        <p class="dec-title">${escapeHtml(decision.service)} — ${escapeHtml(decision.title)}</p>
        <p class="dec-copy">observed ${escapeHtml(decision.date)} · proposal generated by the demo Recommender narrative.</p>
        <p class="dec-facts"><span>asset ${escapeHtml(decision.asset)}</span><span>risk ${escapeHtml(decision.risk)}</span><span>saving ${escapeHtml(decision.savings)}</span><span>confidence ${decision.confidence}%</span></p>
      </div>
      <div class="dec-rail">
        <p class="dec-status">${escapeHtml(statusWord(decision))}</p>
        ${resolved ? "" : `
          <button class="row-action" type="button" data-decision="reject" data-decision-index="${index}" aria-label="reject the ${escapeHtml(decision.service)} proposal">reject ×</button>
          <button class="row-action" type="button" data-decision="approve" data-decision-index="${index}" aria-label="approve the ${escapeHtml(decision.service)} proposal for execution">approve →</button>`}
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

function updateDecision(index, action) {
  const decision = state.decisions[index];
  if (!decision || decision.status !== "pending") return;
  decision.status = action === "approve" ? "approved" : "rejected";
  decision.resolvedAt = utcNow();
  state.decisionMemory.set(decision.id, { status: decision.status, resolvedAt: decision.resolvedAt });
  state.audit.unshift({
    time: decision.resolvedAt,
    title: action === "approve" ? "Operator approved a proposal (demo)" : "Operator rejected a proposal",
    copy:
      action === "approve"
        ? `The ${decision.service} proposal is recorded as approved in the interface. Execution stays disabled until the Sprint II action endpoint is connected.`
        : `The ${decision.service} proposal was closed with no infrastructure action.`,
  });
  renderSummary();
  renderInvestigation();
  renderDecisions();
  renderAudit();
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
    ]);
    if (sequence !== scanSequence) return;
    state.costs = costs;
    state.daily = daily;
    state.anomalies = anomalies.anomalies;
    state.allAnomalies = unfiltered ? unfiltered.anomalies : anomalies.anomalies;
    populateServiceFilter();
    reconcileDecisions();
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

  const decisionAction = event.target.closest("[data-decision]");
  if (decisionAction) {
    updateDecision(Number(decisionAction.dataset.decisionIndex), decisionAction.dataset.decision);
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
    state.audit.unshift({
      time: utcNow(),
      title: "Operator requested deeper analysis",
      copy: "A new evidence request was recorded. In Sprint II this triggers the Analyst Agent endpoint.",
    });
    renderAudit();
    evidenceRequest.textContent = "recorded in the ledger ↓";
    evidenceRequest.disabled = true;
  }
});

/* First paint: the ledger seeds and the empty-state panels do not depend on the
   API, so they render even if the very first scan fails. */
renderInvestigation();
renderDecisions();
renderAudit();
scan();
