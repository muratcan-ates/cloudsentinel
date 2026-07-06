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
  costs: null,
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
  report.services.forEach((service, index) => {
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
    const [anomalies, costs] = await Promise.all([fetchJson(anomalyUrl), fetchJson("/costs/summary")]);
    if (sequence !== scanSequence) return;
    state.costs = costs;
    state.anomalies = anomalies.anomalies;
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
