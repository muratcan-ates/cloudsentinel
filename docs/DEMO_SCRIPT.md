# Demo Script — One Signal, Two Paths (≈5 minutes)

*A beat-by-beat script for the 3-minute product video and the live jury
demo. The thesis in one line: **the machine watches, the human decides.**
Everything below runs on the deterministic provider, so it never blocks on
a quota — that reliability is itself part of the story.*

**Before you start**
- Run the stack: `make setup && make demo` (fake provider, fresh dates).
- Open the dashboard (`/`) and Swagger (`/docs`) in two tabs.
- Optional live smoke in a second shell: `make smoke` (13-step PASS/FAIL sweep).
- Keep one signal in mind as the through-line — a single cost anomaly that
  the system handles two different ways.

---

## Beat 0 · The hook (0:00–0:30)

**Show:** the dashboard masthead, quiet.

**Say (TR):** "Bulut faturası aniden yükseldi — tek bir sinyal. Basit
görünür. Ama arkasında bir soru var: bunu makine mi otomatik kapatmalı,
yoksa bir insan mı karar vermeli? CloudSentinel ikisini de yapıyor:
refleks hızında olanı otomatik, ağır olanı insana taşıyarak. **Makine
izler, insan karar verir.**"

**Why it lands:** you frame the product as a *decision* system, not a
dashboard. The rest of the demo pays this off.

---

## Beat 1 · The reflex path — fast lane (0:30–2:00)

**Show:** trigger a scan (`POST /pulse`, or the dashboard's Pulse button),
then point at the **`REFLEX X ms`** badge on a flagged card.

**Say (TR):** "Refleks hattı saf istatistik — z-score / MAD, modele hiç
uğramadan. Rolling baseline anomaliyi işaretliyor ve gecikmeyi *ölçüyoruz*,
iddia etmiyoruz: kartın üstündeki `REFLEX X ms` rozeti gerçek ölçüm.
Rutin, tekrar eden bir sapma için insan beklemez — hızlı hat halleder."

**Show / prove:** the detector settings come from the mission YAML
(`configs/finops.yaml`), and the anomaly is real statistics over the
synthetic data — not a hard-coded number. (Optionally open `/anomalies`
in Swagger to show the raw scored output.)

**Key line:** "Veri sentetik — yarışma kuralı gereği — ama tespit gerçek:
z-score baseline'ı canlı hesaplıyor."

---

## Beat 2 · Escalation — the signal that needs a brain (2:00–3:30)

**Show:** a signal that crosses the mission's escalation bar and routes
into the conscious loop: Analyst triage with **cited evidence** → the
debate-lite **Skeptic** challenging a weak call → the **Recommender**'s two
options.

**Say (TR):** "Aynı sinyal daha ağır olsaydı, refleks yetmez. Burada
bilinçli döngü devreye giriyor: Analyst kanıtla triyaj yapıyor, Skeptic
zayıf kararı çürütüyor, Recommender iki yol sunuyor — **temkinli** ve
**cesur** — her biri risk ve rollback ile. Bütün para rakamları Python'da
hesaplanıyor, model *asla* sayı uydurmuyor."

**Show / prove:** point at the confidence and the escalation reason on the
card; note the guardrails quietly doing their job — per-pulse call budget,
hard timeout, a ±5% numeric post-check of every narrated figure, and the
stakes-raised debate bar for bold answers to critical signals.

**Key line:** "Model metni yazıyor; sayıyı kod yazıyor ve ±%5 post-check
ile doğruluyor. Halüsinasyon rakamı geçemez."

---

## Beat 3 · The human decides (3:30–4:15)

**Show:** the decision inbox (`/actions`). Type a rationale, then approve
(or reject) a proposal. Point out that execution is a **SIMULATION** by
design.

**Say (TR):** "Hiçbir şey insan onayı olmadan çalışmaz. Operatör gerekçe
yazıyor, onaylıyor — ve icra bilinçli olarak simüle: gerçek altyapıya
dokunmuyoruz, çünkü bu bir bootcamp demosu, prod değil. Kararın izi
append-only ledger'da; `/decisions/export` ile CSV olarak dışa aktarılıyor."

**Honesty beat (say it before the jury asks):** "Kimlik doğrulama,
kalıcı Postgres ve zamanlanmış worker bilinçli olarak kapsam-dışı — bunları
`Scope & Limitations`'ta ve backlog'da açıkça yazdık. Şu an gerçeği başarıyla
simüle eden, iyi mühendislik yapılmış bir prototip."

*Naming your own limits before the jury does turns a "gotcha" into evidence
of engineering maturity.*

---

## Beat 4 · Memory, cost, and the loop (4:15–5:00)

**Show:** two quick panels — the decision memory feeding the next
recommendation, and the self-FinOps view (`/analytics/ai`) showing the
system's own LLM spend.

**Say (TR):** "Sistem hatırlıyor: verdiğin kararlar sonraki önerileri
besliyor. Ve kendi maliyetini izliyor — bir FinOps aracı kendi LLM
harcamasını da FinOps'luyor. Öğrenme döngüsü HITL-kutsal: `/reflex/suggestions`
sadece *öneriyor* — 'bu paterni hep onayladın, refleks kuralı yapalım mı?'
— ama hiçbir kuralı otomatik uygulamıyor. Karar hep insanda."

**Close (TR):** "Aynı motor, farklı YAML: mission dosyasını değiştir,
davranış değişir — cost, security, fraud aynı hatta. Refleks hız için,
bilinç ağırlık için, insan son söz için. CloudSentinel."

---

## Cheat-sheet — what to have open

| Beat | Surface | Endpoint / control |
|---|---|---|
| 1 Reflex | dashboard card + Swagger | `POST /pulse`, `GET /anomalies` |
| 2 Escalation | dashboard debate/card | escalation reason + confidence on the card |
| 3 Decide | decision inbox | `GET /actions`, approve + `GET /decisions/export` |
| 4 Memory/cost | analytics | `GET /analytics/decisions`, `GET /analytics/ai`, `GET /reflex/suggestions` |

**Runs entirely offline on the deterministic provider** — no key, no quota,
no network gamble on stage. If a live Gemini key is configured, the same
path uses the real model; the demo does not depend on it.
