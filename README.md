<!--
PUSH ÖNCESİ DOLDURULACAKLAR:
1. CLICKUP_LINK token'ını gerçek ClickUp board linkiyle değiştir
2. Daily Scrum SS'leri -> ProjectManagement/Sprint1Documents/ klasörüne ekle ve linkle
3. ClickUp board SS -> ProjectManagement/Sprint1Documents/clickup_board.png
4. Ürün durumu SS (Swagger /docs) -> ProjectManagement/Sprint1Documents/swagger_docs.png
5. 5 Temmuz: Sprint Review + Retrospective bölümlerini doldur
-->

# Takım İsmi

Grup 60 – CloudSentinel Takımı

# Ürün İle İlgili Bilgiler

## Takım Elemanları

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

## Ürün İsmi

CloudSentinel

## Ürün Açıklaması

CloudSentinel; bulut maliyet ve güvenlik verilerini izleyen, bu verilerdeki anomalileri tespit eden, tespit edilen anomaliler için yapay zekâ ajanlarıyla aksiyon önerileri üreten ve kritik kararların son onayını operatöre bırakan (human-in-the-loop) agentic bir karar destek sistemidir. Backend FastAPI + Python ile geliştirilmektedir; LLM katmanında Gemini kullanılması planlanmaktadır. MVP aşamasında sistem sentetik (mock) veri üzerinde çalışmaktadır.

## Ürün Özellikleri

- Bulut maliyet verilerinde anomali tespiti
- Güvenlik verilerinin ve sinyallerinin izlenmesi
- Tespit edilen anomaliler için AI ajanlarıyla aksiyon önerisi üretimi
- Kritik aksiyonlarda insan onayı (human-in-the-loop) akışı
- REST API (FastAPI) ve otomatik Swagger dokümantasyonu
- Çoklu ajan orkestrasyonu ile karar mekanizması (ilerleyen sprintlerde)

## Hedef Kitle

- Bulut altyapısı işleten DevOps / platform mühendisliği ekipleri
- Bulut harcamalarını yöneten FinOps uzmanları
- Güvenlik operasyon (SecOps) ekipleri
- Bulut maliyetlerini kontrol altında tutmak isteyen KOBİ'ler ve startup'lar

## Product Backlog URL

[ClickUp Backlog Board](CLICKUP_LINK) <!-- TODO: gerçek linkle değiştir -->

---

# Sprint 1

- **Sprint Notları**:
  - Backend stack'i olarak `FastAPI + Python` kullanılmasına karar verilmiştir (bootcamp kılavuzu gereği).
  - LLM katmanında `Gemini` kullanılması planlanmıştır.
  - Proje yönetim aracı olarak `ClickUp` seçilmiştir; `GitHub Projects`, önceki dönemlerde yaşanan veri kaybı deneyimleri nedeniyle tercih edilmemiştir.
  - Daily Scrum görüşmelerinin `WhatsApp` üzerinden yürütülmesine karar verilmiştir.
  - Sprint 1 kapsamı, sentetik (mock) veri üzerinde çalışan tek bir anomali tespit endpoint'i olarak sınırlandırılmıştır; Gemini entegrasyonu ve çoklu ajan mimarisi sonraki sprintlere bırakılmıştır.
  - Kod, commit mesajları ve teknik dokümantasyon `İngilizce`; Scrum defteri `Türkçe` tutulmaktadır.

- **Sprint içinde tamamlanması tahmin edilen puan**: 10 Puan

- **Puan tamamlama mantığı**: Proje boyunca tamamlanması planlanan toplam backlog puanı 36'dır. Sprint 1, takımların geç kurulması nedeniyle kısaltıldığından bu sprint için hedef 10 puan olarak belirlenmiştir. Kalan puanlar Sprint 2 (13 puan) ve Sprint 3 (13 puan) arasında paylaştırılmıştır.

- **Backlog düzeni ve Story seçimleri**: Backlog, ilk yapılacak story'lere göre sıralanmıştır. Story başına tahmin puanı, sprint toplamının yarısından az tutulmuştur. Sprint 1 story'leri: repo iskeleti ve mock maliyet verisi (3 puan), anomali tespit mantığı (4 puan), `GET /anomalies` endpoint'i ve Swagger dokümantasyonu (3 puan). Story'ler ClickUp üzerinde task'lere bölünmüş ve dört takım üyesine atanmıştır.

- **Daily Scrum**: Daily Scrum görüşmeleri WhatsApp üzerinden yürütülmektedir. <!-- TODO: SS'leri ekle ve linkle: [Sprint 1 Daily Scrum](ProjectManagement/Sprint1Documents/) -->

- **Sprint board update**: Sprint board ekran görüntüleri: <!-- TODO: ![ClickUp Board](ProjectManagement/Sprint1Documents/clickup_board.png) -->

- **Ürün Durumu**: Ekran görüntüleri: <!-- TODO: ![Swagger UI](ProjectManagement/Sprint1Documents/swagger_docs.png) -->

- **Sprint Review**: <!-- TODO (5 Temmuz): alınan kararlar, çıkan ürünün durumu, bir sonraki sprint'e aktarılan maddeler -->

- **Sprint Review Katılımcıları**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt` <!-- katılmayan olursa çıkar -->

- **Sprint Retrospective**: <!-- TODO (5 Temmuz): iyi gidenler / geliştirilecekler / aksiyon maddeleri -->

---

# Sprint 2

---

# Sprint 3
