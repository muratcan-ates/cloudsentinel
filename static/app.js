/* CloudSentinel dashboard — fetches /anomalies and /costs/summary, renders the panels,
   and drifts a field of pixel squares in the background. */

const thresholdInput = document.getElementById("threshold");
const thresholdValue = document.getElementById("threshold-value");
const rescanButton = document.getElementById("rescan");
const systemChip = document.getElementById("chip-system");

const fmtMoney = (value, currency) =>
  `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

function renderAnomalies(report) {
  const list = document.getElementById("anomaly-list");
  const meta = document.getElementById("anomaly-meta");
  meta.textContent =
    `${report.records_analyzed} records scanned · threshold ${report.threshold.toFixed(2)} · ` +
    `${report.anomaly_count} anomal${report.anomaly_count === 1 ? "y" : "ies"} detected`;

  list.innerHTML = "";
  if (report.anomalies.length === 0) {
    list.innerHTML = `<div class="all-clear">ALL CLEAR — NO PHANTOMS IN THE MACHINE</div>`;
    return new Set();
  }

  for (const anomaly of report.anomalies) {
    const card = document.createElement("div");
    card.className = `anomaly-card ${anomaly.severity}`;
    card.innerHTML = `
      <div>
        <div class="anomaly-service">${anomaly.service}</div>
        <div class="anomaly-date">${anomaly.date}</div>
        <div class="anomaly-cost">${anomaly.cost.toFixed(2)} <small>vs mean ${anomaly.service_mean.toFixed(2)}</small></div>
      </div>
      <div class="anomaly-z">z ${anomaly.z_score.toFixed(2)}</div>
      <div class="anomaly-badge">[ ${anomaly.severity.toUpperCase()} ] ${
        anomaly.severity === "critical" ? "OPERATOR REVIEW REQUIRED" : "WATCHLISTED"
      }</div>`;
    list.appendChild(card);
  }
  return new Set(report.anomalies.map((a) => a.service));
}

function renderCosts(report, hauntedServices) {
  const meta = document.getElementById("cost-meta");
  meta.textContent = `${report.period.start} → ${report.period.end} · ${report.services.length} services`;

  document.getElementById("total-cost").textContent = fmtMoney(report.total_cost, report.currency);

  const bars = document.getElementById("cost-bars");
  bars.innerHTML = "";
  for (const service of report.services) {
    const haunted = hauntedServices.has(service.service);
    const row = document.createElement("div");
    row.className = `cost-row${haunted ? " haunted" : ""}`;
    row.innerHTML = `
      <div class="cost-head">
        <span class="cost-name">${service.service}${
          haunted ? '<span class="phantom">⌁ phantom traced</span>' : ""
        }</span>
        <span class="cost-nums"><b>${fmtMoney(service.total_cost, report.currency)}</b> · ${(service.share_of_total * 100).toFixed(1)}%</span>
      </div>
      <div class="bar"><div class="bar-fill" style="width:0%"></div></div>`;
    bars.appendChild(row);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        row.querySelector(".bar-fill").style.width = `${(service.share_of_total * 100).toFixed(1)}%`;
      })
    );
  }
}

async function scan() {
  const threshold = parseFloat(thresholdInput.value).toFixed(2);
  thresholdValue.textContent = threshold;
  try {
    const [anomalies, costs] = await Promise.all([
      fetchJson(`/anomalies?threshold=${threshold}`),
      fetchJson("/costs/summary"),
    ]);
    const haunted = renderAnomalies(anomalies);
    renderCosts(costs, haunted);
    systemChip.textContent = "SYSTEM ONLINE";
    systemChip.classList.remove("alert");
    systemChip.classList.add("online");
  } catch (error) {
    systemChip.textContent = "LINK LOST";
    systemChip.classList.remove("online");
    systemChip.classList.add("alert");
    document.getElementById("anomaly-list").innerHTML =
      `<div class="error-box">SIGNAL LOST — ${error.message}</div>`;
  }
}

thresholdInput.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdInput.value).toFixed(2);
});
thresholdInput.addEventListener("change", scan);
rescanButton.addEventListener("click", scan);

/* ---------- pixel particle field ---------- */

const canvas = document.getElementById("particles");
const ctx = canvas.getContext("2d");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let squares = [];

function resizeCanvas() {
  canvas.width = document.documentElement.clientWidth;
  canvas.height = document.documentElement.clientHeight;
}

function seedSquares() {
  const count = Math.min(70, Math.floor(canvas.width / 18));
  squares = Array.from({ length: count }, () => ({
    x: Math.random() * canvas.width,
    y: Math.random() * canvas.height,
    size: 2 + Math.floor(Math.random() * 6),
    speed: 0.15 + Math.random() * 0.5,
    cyan: Math.random() < 0.12,
    phase: Math.random() * Math.PI * 2,
  }));
}

function drawSquares(time) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const square of squares) {
    const flicker = 0.35 + 0.65 * Math.abs(Math.sin(square.phase + time / 900));
    ctx.fillStyle = square.cyan
      ? `rgba(77, 227, 255, ${0.5 * flicker})`
      : `rgba(57, 255, 20, ${0.45 * flicker})`;
    ctx.fillRect(Math.round(square.x), Math.round(square.y), square.size, square.size);
    square.y -= square.speed;
    if (square.y < -10) {
      square.y = canvas.height + 10;
      square.x = Math.random() * canvas.width;
    }
  }
  if (!reducedMotion) requestAnimationFrame(drawSquares);
}

window.addEventListener("resize", () => {
  resizeCanvas();
  seedSquares();
});

resizeCanvas();
seedSquares();
drawSquares(0);
scan();
