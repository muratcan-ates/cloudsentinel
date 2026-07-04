/* CloudSentinel ledger — fetches /anomalies and /costs/summary and typesets the panels. */

const thresholdInput = document.getElementById("threshold");
const thresholdValue = document.getElementById("threshold-value");
const rescanButton = document.getElementById("rescan");
const editionLine = document.getElementById("chip-system");
const anomalyList = document.getElementById("anomaly-list");
const costBars = document.getElementById("cost-bars");

const fmtNumber = (value) =>
  value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

function renderAnomalies(report) {
  document.getElementById("anomaly-meta").textContent =
    `${report.records_analyzed} records scanned · threshold ${report.threshold.toFixed(2)} · ` +
    `${report.anomaly_count} anomal${report.anomaly_count === 1 ? "y" : "ies"} detected`;

  anomalyList.innerHTML = "";
  if (report.anomalies.length === 0) {
    anomalyList.innerHTML = `<p class="all-quiet">All quiet.</p>`;
    return new Set();
  }

  for (const anomaly of report.anomalies) {
    const entry = document.createElement("article");
    entry.className = `entry ${anomaly.severity}`;
    entry.innerHTML = `
      <span class="sq" aria-hidden="true"></span>
      <div>
        <p class="service">${anomaly.service}</p>
        <p class="date">${anomaly.date}</p>
        <p class="figures">${fmtNumber(anomaly.cost)} <span class="dim">vs mean ${fmtNumber(anomaly.service_mean)}</span></p>
      </div>
      <div class="entry-rail">
        <p class="z">${anomaly.z_score.toFixed(2)}</p>
        <p class="sev-word">${anomaly.severity}</p>
      </div>`;
    anomalyList.appendChild(entry);
  }
  return new Set(report.anomalies.map((a) => a.service));
}

function renderCosts(report, flaggedServices) {
  document.getElementById("cost-meta").textContent =
    `${report.period.start} → ${report.period.end} · ${report.services.length} services`;

  document.getElementById("total-cost").innerHTML =
    `${fmtNumber(report.total_cost)} <small>${report.currency}</small>`;

  costBars.innerHTML = "";
  report.services.forEach((service, index) => {
    const flagged = flaggedServices.has(service.service);
    const share = (service.share_of_total * 100).toFixed(1);
    const row = document.createElement("div");
    row.className = "cost-row";
    row.innerHTML = `
      <div class="cost-line">
        <span class="idx">${String(index + 1).padStart(2, "0")}</span>
        <span class="service">${service.service}${
          flagged
            ? '<span class="phantom-sq" aria-hidden="true"></span><span class="phantom-note">phantom traced</span>'
            : ""
        }</span>
        <span class="amount">${fmtNumber(service.total_cost)} <small>${report.currency}</small> <span class="share">· ${share}%</span></span>
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

async function scan() {
  const threshold = parseFloat(thresholdInput.value).toFixed(2);
  thresholdValue.textContent = threshold;
  anomalyList.style.opacity = "0.35";
  costBars.style.opacity = "0.35";
  try {
    const [anomalies, costs] = await Promise.all([
      fetchJson(`/anomalies?threshold=${threshold}`),
      fetchJson("/costs/summary"),
    ]);
    renderCosts(costs, renderAnomalies(anomalies));
    editionLine.textContent = "SYSTEM ONLINE — MOCK DATA — SPRINT I";
    editionLine.classList.remove("down");
  } catch (error) {
    editionLine.textContent = "LINK LOST — MOCK DATA — SPRINT I";
    editionLine.classList.add("down");
    anomalyList.innerHTML = `<p class="error-note">Signal lost — ${error.message}.</p>`;
  } finally {
    anomalyList.style.opacity = "1";
    costBars.style.opacity = "1";
  }
}

thresholdInput.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdInput.value).toFixed(2);
});
thresholdInput.addEventListener("change", scan);
rescanButton.addEventListener("click", scan);

scan();
