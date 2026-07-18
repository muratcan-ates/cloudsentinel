# ☁️ CloudSentinel — Türkçe Özet

> **Makine izler, insan karar verir.**

*(Bu sayfa, [İngilizce README](../README.md)'nin kısa Türkçe özetidir; scrum
defteri ve teknik dokümantasyon İngilizce tutulur. — YZTA Bootcamp 2026 ·
AI Track · Grup 60)*

## Ürün nedir?

CloudSentinel, bulut operasyonları için **ajan destekli bir karar-destek
sistemidir**: maliyet ve güvenlik verisindeki anomalileri saptar, yapay zekâ
ajanları her sinyali kanıt göstererek yorumlayıp riskli/temkinli iki çözüm
önerisi üretir ve **kritik her kararın onayını insana bırakır**. Onaysız
hiçbir şey çalışmaz; yürütme tasarım gereği simülasyondur.

## Beş vuruşta akış

1. **Saptа** — deterministik dedektör (kayan taban çizgisi, z-skoru/MAD,
   haftalık mevsimsellik) üç hattı puanlar: bulut maliyeti, güvenlik
   olayları, ödeme olayları (yayınlanmış kural skoru).
2. **Yorumla** — Analist ajan kanıt satırlarını atıf göstererek triyaj
   yapar; Önerici temkinli/cesur iki seçenek üretir (risk + geri alma
   planıyla); Şüpheci ajan zayıf çağrıları sorgular. **Para rakamlarını
   asla model üretmez** — hepsi Python'da deterministik hesaplanır.
3. **Karar ver** — öneriler karar kutusunda bekler; operatör gerekçesiyle
   onaylar ya da reddeder.
4. **Hatırla** — verilen kararlar hafızaya yazılır ve sonraki önerileri
   besler.
5. **Hesap ver** — onaylanan tasarruflar, HITL hunisi, trend/tahmin/ROI
   panelleri ve sistemin kendi LLM harcaması tek panelde izlenir.

## Kimin için?

| Rol | Günlük sorusu | CloudSentinel'deki karşılığı |
|---|---|---|
| **FinOps analisti** | "Harcama neden sıçradı, düzeltmeye değer mi?" | Maliyet defteri, anomali işaretli trend eğrisi, hesaplanmış tasarruf rakamları, CSV dışa aktarım |
| **DevOps / platform mühendisi** | "Tam olarak neyi değiştireceğim, nasıl geri alırım?" | Kanıt atıflı triyaj, risk + geri alma planlı iki seçenek, insan onayına kadar simüle kalan yürütme |
| **SecOps operatörü** | "Kim, neye, neden karar verdi — kanıtlayabilir miyim?" | İdempotent karar makinesi, silinmez karar defteri, aynı hattan akan güvenlik sinyalleri |

## Nasıl çalıştırılır?

```bash
make setup && make demo     # sahte sağlayıcı ile, çevrimdışı tam demo
# panel: http://127.0.0.1:8000/  ·  API dokümanı: /docs
make test                   # 387 test, ~7 sn
make smoke                  # canlı zincir üzerinde 13 adımlı kontrol
```

## Durum (18 Temmuz 2026)

- **Sprint 2 kapanışında:** 32 uç nokta, 387 otomatik test, ruff temiz;
  Analist + Önerici + Şüpheci + Vakanüvis ajanları, karar hafızası, canlı
  ajan akış paneli, güvenlik ve sahtecilik hatları, misyon DSL'i ve
  operasyon analitiği yayında.
- **Sprint 3 (20 Temmuz – 2 Ağustos):** canlı Gemini ölçümü, dağıtım
  (Render), canlı veri denemesi, piyasa-takibi öneri tablosu, UX turu ve
  3 dakikalık ürün videosu — tam liste:
  [Sprint 3 Backlog](sprint3_backlog.md).

## Sınırlar (bilinçli kararlar)

Yarışma penceresinde veri sentetiktir, kimlik doğrulama ve kalıcı Postgres
yoktur, yürütme simülasyondur. Bunlar eksik değil, demoyu dürüst tutan
kapsam kararlarıdır; ürünleşme yolu [Sprint 3 Backlog](sprint3_backlog.md)
B bölümünde yazılıdır.
