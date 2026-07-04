<!--
PUSH ÖNCESİ DOLDURULACAKLAR:
1. Daily Scrum SS'leri -> ProjectManagement/Sprint1Documents/ klasörüne ekle ve linkle
2. Miro board SS -> ProjectManagement/Sprint1Documents/miro_board.png
3. Ürün durumu SS (dashboard + Swagger) -> ProjectManagement/Sprint1Documents/
4. 5 Temmuz: Sprint Review + Retrospective bölümlerini doldur
-->

<div align="center">

# ☁️ CloudSentinel

### AI-agent powered cloud cost & security anomaly detection — with a human in the loop

**YZTA Bootcamp 2026 · AI Track · Group 60**

[Product](#information-about-the-product) · [Architecture](docs/architecture.md) · [How to Run](#how-to-run-local) · [Sprint 1](#sprint-1)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Sprint_2-8E75B2?logo=googlegemini&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Last Commit](https://img.shields.io/github/last-commit/muratcan-ates/cloudsentinel?style=flat-square)

</div>

## 📖 Table of Contents

- [Team Name](#team-name)
- [Information About the Product](#information-about-the-product)
  - [Team Members](#team-members)
  - [Product Name](#product-name) · [Product Description](#product-description) · [Product Features](#product-features) · [Target Audience](#target-audience)
  - [What Makes CloudSentinel Different](#what-makes-cloudsentinel-different)
  - [How to Run (Local)](#how-to-run-local)
  - [Built With](#built-with) · [Project Status](#project-status)
  - [Requirements Compliance](#requirements-compliance) · [Scope & Limitations](#scope--limitations-by-design)
  - [Product Backlog URL](#product-backlog-url)
- [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) · [Sprint 3](#sprint-3)

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

Then open the dashboard at `http://127.0.0.1:8000/` (Swagger at `/docs`), or query directly:

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

> On Windows, replace `.venv/bin/` with `.venv\Scripts\` in the commands above.

Or run it with Docker:

```bash
docker build -t cloudsentinel .
docker run -p 8000:8000 cloudsentinel
```

## Built With

| Technology | Purpose |
|---|---|
| **Python 3.12** | Core language (pinned in venv, CI and Docker) |
| **FastAPI + Uvicorn** | REST API and ASGI server |
| **Pydantic v2** | Typed request/response models and validation |
| **pytest + httpx** | Automated test suite (23 tests) |
| **Docker** | Containerized, deployment-ready packaging |
| **Gemini** *(Sprint 2)* | LLM layer for the Analyst and Recommender agents |
| **Miro** | Scrum board and product backlog (official bootcamp template) |

## Project Status

| Deliverable | Description | Status |
|---|---|---|
| Mock cost dataset | 4 services × 14 days of synthetic costs with 2 planted spikes | ✅ [`data/mock_costs.json`](data/mock_costs.json) |
| Anomaly detection API | `GET /anomalies` — per-service z-score with typed responses | ✅ [`main.py`](main.py) |
| Cost summary API | `GET /costs/summary` — per-service spend aggregates and shares | ✅ [`main.py`](main.py) |
| Cyber dashboard | Root-served UI: anomaly feed, cost matrix, live threshold control | ✅ [`static/`](static/) |
| Test suite | 23 pytest cases: detection, aggregation, filtering, validation, dashboard | ✅ [`tests/`](tests/) |
| Continuous integration | Tests run on every push via GitHub Actions | 🔜 Sprint 2 |
| Containerization | `python:3.12-slim` image | ✅ [`Dockerfile`](Dockerfile) |
| Agent & HITL architecture design | Sprint 2–3 technical plan | ✅ [`docs/architecture.md`](docs/architecture.md) |
| Gemini agents (Analyst + Recommender) | LLM-based anomaly analysis and action proposals | 🔜 Sprint 2 |
| Human-in-the-loop approval flow | `proposed → approved/rejected → executed` action lifecycle | 🔜 Sprint 2 |
| Security signals · deployment | Same pipeline extended + live demo | 🔜 Sprint 3 |

## Requirements Compliance

Mapping of the official bootcamp scrum-notebook requirements to their evidence in this repository:

| Requirement | Status | Evidence |
|---|---|---|
| Team name & roles documented | ✅ | [Team Name](#team-name) · [Team Members](#team-members) |
| Product name, description, features, target audience | ✅ | [Information About the Product](#information-about-the-product) |
| Product Backlog board (Miro) | ✅ | [Product Backlog URL](#product-backlog-url) |
| Sprint Notes (never left empty) | ✅ | [Sprint 1](#sprint-1) |
| Point estimates & completion logic | ✅ | [Sprint 1](#sprint-1) |
| Daily Scrum documentation | 🔄 being collected | `ProjectManagement/Sprint1Documents/` |
| Sprint board screenshots | 🔄 after board setup | `ProjectManagement/Sprint1Documents/` |
| Product status screenshots | 🔄 being collected | `ProjectManagement/Sprint1Documents/` |
| Sprint Review & Retrospective | 🗓 due 5 July | [Sprint 1](#sprint-1) |
| Working product increment | ✅ | [`GET /anomalies`](main.py) · [`GET /costs/summary`](main.py) · [tests](tests/) |

## Scope & Limitations (By Design)

These constraints are intentional Sprint 1 decisions, not oversights:

- **Synthetic mock data only** — real cloud-provider connectors are outside the
  competition scope; the detection pipeline is data-source agnostic by design.
- **Read-only endpoints for now** — `/anomalies` and `/costs/summary` only
  observe; the action-proposal and approval endpoints arrive with the
  human-in-the-loop flow in Sprint 2
  (see [docs/architecture.md](docs/architecture.md)).
- **Security signals not ingested yet** — the scope decision (extend the same
  pipeline vs. narrow the product to cost) is on the Sprint 1 review agenda.
- **No live deployment yet** — the app is containerized and deployment-ready;
  the target platform is decided in Sprint 3.

## Product Backlog URL

[Miro Scrum Board — official bootcamp template](https://miro.com/app/board/uXjVH-p0md4=/?share_link_id=656166042252)

---

# Sprint 1

- **Sprint Notes**:
  - `FastAPI + Python` was chosen as the backend stack (required by the bootcamp guide).
  - `Gemini` is planned for the LLM layer.
  - `Miro` (the official bootcamp Scrum template) was chosen as the project management tool; `GitHub Projects` was not preferred due to data-loss experiences in previous terms.
  - It was decided that Daily Scrum meetings would be held over `WhatsApp`.
  - The scope of Sprint 1 was limited to a single anomaly-detection endpoint running on synthetic (mock) data; Gemini integration and the multi-agent architecture were deferred to later sprints.
  - Code, commit messages and all project documentation, including this scrum notebook, are kept in `English`.
  - Samet Kargın was unable to participate during Sprint 1; the team continues with four active members and the Sprint 1 stories were distributed accordingly.

- **Expected point completion within the sprint**: 10 points

- **Point Completion Logic**: The total backlog planned for the whole project is 36 points. Since Sprint 1 was shortened due to the late formation of teams, the target for this sprint was set at 10 points. The remaining points are split between Sprint 2 (13 points) and Sprint 3 (13 points).

- **Backlog order and story selections**: The backlog is ordered by the stories that will be tackled first. The estimate for each story is kept below half of the sprint total. Sprint 1 stories: repository skeleton and mock cost data (3 points), anomaly detection logic (4 points), `GET /anomalies` endpoint with Swagger documentation (3 points). Stories are split into tasks on the Miro board and assigned across the four team members.

- **Daily Scrum**: Daily Scrum meetings are held over WhatsApp. <!-- TODO: SS'leri ekle ve linkle: [Sprint 1 Daily Scrum](ProjectManagement/Sprint1Documents/) -->

- **Sprint board update**: Sprint board screenshots: <!-- TODO: ![Miro Board](ProjectManagement/Sprint1Documents/miro_board.png) -->

- **Product Status**: Screenshots: <!-- TODO: ![Swagger UI](ProjectManagement/Sprint1Documents/swagger_docs.png) -->

- **Sprint Review**: <!-- TODO (5 Temmuz): alınan kararlar, çıkan ürünün durumu, bir sonraki sprint'e aktarılan maddeler -->

- **Sprint Review Participants**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt` <!-- katılmayan olursa çıkar -->

- **Sprint Retrospective**: <!-- TODO (5 Temmuz): iyi gidenler / geliştirilecekler / aksiyon maddeleri -->

---

# Sprint 2

---

# Sprint 3

---

<div align="center"><sub>Built by Team CloudSentinel — YZTA Bootcamp 2026 · AI Track · Group 60</sub></div>
