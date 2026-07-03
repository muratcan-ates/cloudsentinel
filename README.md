<!--
PUSH ÖNCESİ DOLDURULACAKLAR:
1. CLICKUP_LINK token'ını gerçek ClickUp board linkiyle değiştir
2. Daily Scrum SS'leri -> ProjectManagement/Sprint1Documents/ klasörüne ekle ve linkle
3. ClickUp board SS -> ProjectManagement/Sprint1Documents/clickup_board.png
4. Ürün durumu SS (Swagger /docs) -> ProjectManagement/Sprint1Documents/swagger_docs.png
5. 5 Temmuz: Sprint Review + Retrospective bölümlerini doldur
-->

# Team Name

Group 60 – Team CloudSentinel

# Information About the Product

## Team Members

<table align="center">
<tr>
<td align="center">
  <a href="https://github.com/tuanaydin">
    <img src="https://github.com/tuanaydin.png" width="90" alt="Tuana Aydın"/>
    <br/><sub><b>Tuana Aydın</b></sub>
  </a>
  <br/><sub>Product Owner</sub>
</td>
<td align="center">
  <a href="https://github.com/muratcan-ates">
    <img src="https://github.com/muratcan-ates.png" width="90" alt="Muratcan Ateş"/>
    <br/><sub><b>Muratcan Ateş</b></sub>
  </a>
  <br/><sub>Scrum Master</sub>
</td>
<td align="center">
  <a href="https://github.com/caglayurtsvn">
    <img src="https://github.com/caglayurtsvn.png" width="90" alt="Çağla Yurtseven"/>
    <br/><sub><b>Çağla Yurtseven</b></sub>
  </a>
  <br/><sub>Developer</sub>
</td>
<td align="center">
  <a href="https://github.com/mertefekurt">
    <img src="https://github.com/mertefekurt.png" width="90" alt="Mert Kurt"/>
    <br/><sub><b>Mert Kurt</b></sub>
  </a>
  <br/><sub>Developer</sub>
</td>
</tr>
</table>

## Product Name

CloudSentinel

## Product Description

CloudSentinel is an agentic decision-support system that monitors cloud cost and security data, detects anomalies in that data, generates action recommendations for detected anomalies through AI agents, and leaves the final approval of critical actions to a human operator (human-in-the-loop). The backend is being developed with FastAPI + Python; Gemini is planned for the LLM layer. At the MVP stage the system runs on synthetic (mock) data.

## Product Features

- Anomaly detection on cloud cost data
- Monitoring of security data and signals
- AI-agent-generated action recommendations for detected anomalies
- Human-in-the-loop approval flow for critical actions
- REST API (FastAPI) with automatic Swagger documentation
- Multi-agent orchestration for decision making (in upcoming sprints)

## Target Audience

- DevOps / platform engineering teams operating cloud infrastructure
- FinOps specialists managing cloud spending
- Security operations (SecOps) teams
- SMEs and startups that want to keep their cloud costs under control

## What Makes CloudSentinel Different

Cloud providers and observability tools (AWS Cost Anomaly Detection, GCP cost
alerts, Datadog Cloud Cost Management) can already *detect* cost anomalies.
CloudSentinel's differentiator is what happens after detection: AI agents
reason about each anomaly, propose concrete remediation actions with risk
levels, and a human operator gives the final approval — closing the
detect → decide → act loop with human-in-the-loop safety instead of leaving
the operator alone with a raw alert. The planned agent design is documented
in [docs/architecture.md](docs/architecture.md).

## How to Run (Local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn main:app --reload
```

Then open Swagger at `http://127.0.0.1:8000/docs`, or query directly:

```bash
curl "http://127.0.0.1:8000/anomalies"
# → detects the 2 planted spikes in the mock data:
#   compute 2026-06-29 (z=3.61) and database 2026-07-02 (z=3.60)
```

Per-service spending breakdown:

```bash
curl "http://127.0.0.1:8000/costs/summary"
# → total spend, per-service totals and each service's share of overall cost
```

Run the test suite with `.venv/bin/pytest`.

Or run it with Docker:

```bash
docker build -t cloudsentinel .
docker run -p 8000:8000 cloudsentinel
```

## Product Backlog URL

[ClickUp Backlog Board](CLICKUP_LINK) <!-- TODO: gerçek linkle değiştir -->

---

# Sprint 1

- **Sprint Notes**:
  - `FastAPI + Python` was chosen as the backend stack (required by the bootcamp guide).
  - `Gemini` is planned for the LLM layer.
  - `ClickUp` was chosen as the project management tool; `GitHub Projects` was not preferred due to data-loss experiences in previous terms.
  - It was decided that Daily Scrum meetings would be held over `WhatsApp`.
  - The scope of Sprint 1 was limited to a single anomaly-detection endpoint running on synthetic (mock) data; Gemini integration and the multi-agent architecture were deferred to later sprints.
  - Code, commit messages and all project documentation, including this scrum notebook, are kept in `English`.
  - Samet Kargın was unable to participate during Sprint 1; the team continues with four active members and the Sprint 1 stories were distributed accordingly.

- **Expected point completion within the sprint**: 10 points

- **Point Completion Logic**: The total backlog planned for the whole project is 36 points. Since Sprint 1 was shortened due to the late formation of teams, the target for this sprint was set at 10 points. The remaining points are split between Sprint 2 (13 points) and Sprint 3 (13 points).

- **Backlog order and story selections**: The backlog is ordered by the stories that will be tackled first. The estimate for each story is kept below half of the sprint total. Sprint 1 stories: repository skeleton and mock cost data (3 points), anomaly detection logic (4 points), `GET /anomalies` endpoint with Swagger documentation (3 points). Stories are split into tasks on ClickUp and assigned across the four team members.

- **Daily Scrum**: Daily Scrum meetings are held over WhatsApp. <!-- TODO: SS'leri ekle ve linkle: [Sprint 1 Daily Scrum](ProjectManagement/Sprint1Documents/) -->

- **Sprint board update**: Sprint board screenshots: <!-- TODO: ![ClickUp Board](ProjectManagement/Sprint1Documents/clickup_board.png) -->

- **Product Status**: Screenshots: <!-- TODO: ![Swagger UI](ProjectManagement/Sprint1Documents/swagger_docs.png) -->

- **Sprint Review**: <!-- TODO (5 Temmuz): alınan kararlar, çıkan ürünün durumu, bir sonraki sprint'e aktarılan maddeler -->

- **Sprint Review Participants**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt` <!-- katılmayan olursa çıkar -->

- **Sprint Retrospective**: <!-- TODO (5 Temmuz): iyi gidenler / geliştirilecekler / aksiyon maddeleri -->

---

# Sprint 2

---

# Sprint 3
