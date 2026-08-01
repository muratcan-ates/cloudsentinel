# Demo Script — One Signal, Two Paths (3-minute cut)

*The beat-by-beat script for the 3-minute product video. The thesis in one
line: **the machine watches, the human decides.** Everything below runs on
the deterministic provider, so it never blocks on a quota — that
reliability is itself part of the story. Extended beats for the live jury
demo are at the end.*

**Before you start**
- Run the stack: `make setup && make demo` (fake provider, fresh dates,
  reset armed — `POST /ops/demo-reset?seed=1` between takes).
- Open the dashboard (`/`); `?tour=1` gives the six-stop guided walk if
  you want an establishing shot.
- Keep one signal in mind as the through-line — a single cost anomaly that
  the system handles two different ways.

---

## Scene 1 · The hook (0:00–0:20 · 20s)

**Show:** the dashboard masthead, quiet.

**Say (TR):** "Bulut faturası aniden yükseldi — tek bir sinyal. Bunu makine
mi otomatik kapatmalı, yoksa bir insan mı karar vermeli? CloudSentinel
ikisini de yapıyor. **Makine izler, insan karar verir.**"

---

## Scene 2 · The reflex path — fast lane (0:20–1:00 · 40s)

**Show:** hit Pulse, then point at the **`REFLEX X ms`** badge on a
flagged card.

**Say (TR):** "Refleks hattı saf istatistik — z-score / MAD, modele hiç
uğramadan; ayarlar mission YAML'ından geliyor. Rolling baseline anomaliyi
işaretliyor ve gecikmeyi *ölçüyoruz*, iddia etmiyoruz: `REFLEX X ms`
rozeti gerçek ölçüm. Rutin sapma için insan beklemez — hızlı hat halleder."

**Key line:** "Veri sentetik — yarışma kuralı gereği — ama tespit gerçek:
z-score baseline'ı canlı hesaplıyor."

---

## Scene 3 · Escalation — the signal that needs a brain (1:00–1:45 · 45s)

**Show:** a signal that crosses the escalation bar: Analyst triage with
**cited evidence** → the **Recommender**'s two options, cautious and
bold → and on this critical signal, the **review panel** — open the
card's fold: three reviewer rows, one dissent marked on the record.

**Say (TR):** "Aynı sinyal daha ağır olsaydı, refleks yetmez. Bilinçli
döngü devreye giriyor: Analyst kanıtla triyaj yapıyor, Recommender iki
yol sunuyor — **temkinli** ve **cesur** — her biri risk ve rollback ile.
Kritik sinyalde tek şüpheci de yetmez: **üç ayrı hakem** aynı kararı
kendi tüzüğünden tartışıyor, çoğunluk karar veriyor, muhalefet şerhi
kayda geçiyor."

**Key line:** "Model metni yazıyor; sayıyı kod yazıyor ve ±%5 post-check
ile doğruluyor. Halüsinasyon rakamı geçemez."

---

## Scene 4 · The human decides (1:45–2:20 · 35s)

**Show:** the decision inbox. Type a rationale, approve a proposal, point
at the **SIMULATION** label. The masthead now carries the signed-in
identity the decision is recorded under.

**Say (TR):** "Hiçbir şey insan onayı olmadan çalışmaz. Operatör gerekçe
yazıyor, onaylıyor — icra bilinçli olarak simüle: gerçek altyapıya
dokunmuyoruz. Kararın izi append-only ledger'da, kimlik sunucudan geliyor."

**Honesty beat (say it before the jury asks):** "Kalıcı Postgres,
zamanlanmış worker ve gerçek altyapı icrası bilinçli olarak kapsam-dışı —
`Scope & Limitations`'ta açıkça yazdık. Bu, gerçeği başarıyla simüle eden,
iyi mühendislik yapılmış bir prototip."

---

## Scene 5 · The brain — the system reads its own history (2:20–2:50 · 30s)

**Show:** switch to `/brain`. Insights (observations → predictions →
recommendations), click **run self-review** for one proposal, one runbook
search hit ("ec2 cost spike"), then let the **backtest chart** sit for a
few seconds — the MAD bar holding 1.0 where z-score drops.

**Say (TR):** "Sistem hatırlıyor ve kendi geçmişinden sonuç çıkarıyor:
gözlem, tahmin, öneri. Kendi maliyetini de FinOps'luyor. Öğrenme döngüsü
HITL-kutsal: sistem yalnız *öneriyor* — hiçbir kuralı otomatik
uygulamıyor. Ve tespit kalitesini anlatmıyoruz, *ölçüyoruz*: backtest
grafiği, kirli baseline'da MAD'in neden kazandığını gösteriyor."

---

## Scene 6 · Close (2:50–3:00 · 10s)

**Show:** flip the **mission dropdown** to `security` — the same engine
re-reads another YAML live, on camera.

**Say (TR):** "Aynı motor, farklı YAML: mission'ı değiştir, davranış
değişir — cost, security, fraud aynı hatta. Refleks hız için, bilinç
ağırlık için, insan son söz için. CloudSentinel."

---

## Cheat-sheet — what to have open

| Scene | Surface | Endpoint / control |
|---|---|---|
| 2 Reflex | dashboard card | `POST /pulse` (Pulse button) |
| 3 Escalation | dashboard debate/card | escalation reason + confidence on the card |
| 4 Decide | decision inbox | approve with rationale; identity in the masthead |
| 5 Brain | `/brain` | `GET /insights`, `POST /insights/self-review`, `/runbooks/match`, `GET /metrics/backtest` |

**Runs entirely offline on the deterministic provider** — no key, no quota,
no network gamble on stage. If a live Gemini key is configured, the same
path uses the real model; the demo does not depend on it.

---

## Extended beats — live jury demo only (not in the video)

- **Swagger sweep:** open `/docs` (self-hosted Swagger) and show
  `GET /anomalies` raw scored output — the anomaly is statistics, not a
  hard-coded number.
- **Smoke sweep:** `make smoke` in a second shell — 14-step PASS/FAIL
  over the live server.
- **Ledger export:** `GET /decisions/export` — the append-only decision
  ledger as CSV.
- **Learning loop:** `GET /reflex/suggestions` — "bu paterni hep
  onayladın, refleks kuralı yapalım mı?" — proposals only, never applied
  automatically.
- **Routines:** save a suggested ritual in `/brain`, run it from the
  saved list — a suggestion can be saved without running it, and running
  one is read-only.
