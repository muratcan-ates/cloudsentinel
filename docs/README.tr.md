# CloudSentinel — Türkçe Kılavuz

> **Makine izler, insan karar verir.**

Bu sayfa jüri için yazıldı. Amacı on dakikada şunları anlatmak: ürün ne,
nasıl çalışıyor, matematiği ne, neyin gerçek neyin simülasyon olduğu ve
nerede durduğu. Kod, commit mesajları ve teknik dokümantasyon İngilizce
tutuluyor — bu sayfa onların Türkçe karşılığı, kısaltılmış tanıtımı değil.

**YZTA Bootcamp 2026 · AI Track · Grup 60 · Takım CloudSentinel**

İngilizce ana doküman: [README.md](../README.md) · mimari gerekçeleri:
[architecture.md](architecture.md) · dürüst sınırlar listesi:
[LIMITATIONS.md](LIMITATIONS.md) · bir sayfalık özet:
[PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md)

---

## 1. Tek cümlelik tez

Bulut maliyeti, güvenlik ve ödeme verisindeki sapmayı deterministik bir
dedektör bulur, yapay zekâ ajanları o sapmayı kanıt göstererek yorumlar ve
iki çıkış yolu önerir, **hiçbir şey bir insan onaylamadan çalışmaz**.

## 2. Neden var

Anomaliyi *bulmak* çözülmüş bir problem: AWS Cost Anomaly Detection, GCP
bütçe alarmları, Datadog Cloud Cost Management hepsi bunu yapıyor. Hepsinin
bittiği yer de aynı: ekrana "harcaman sıçradı" yazar ve operatörü ham bir
alarmla baş başa bırakır. Asıl iş oradan sonra başlıyor — bu ne, gerçek mi,
ne yapmalıyım, yanlışsa nasıl geri alırım, kim karar verdi.

CloudSentinel tam olarak o boşluğun ürünü. Alarmdan sonrasını
kurumsallaştırıyor: kanıtlı triyaj, riski ve geri alma planı yazılı iki
seçenek, itiraz eden bir ajan, gerekçesi kayda geçen bir insan onayı ve
sonradan doğrulanabilir bir defter.

## 3. Ürün beş adımda

### 1. Tespit — deterministik, LLM yok

Her hat (maliyet, güvenlik, ödeme) kendi mission'ının ayarlarıyla taranır.
Tarama saf Python'dur: model yok, rastgelelik yok, aynı veri her zaman aynı
sonucu verir. Tarayıcı kendi gecikmesini ölçer ve raporlar
(`GET /reflex/suggestions`, `POST /pulse`).

### 2. Akıl yürütme — ajanlar konuşur, sayı üretmez

Kalıcılaştırılmış her maliyet anomalisi için **Analist** triyaj yapar
(`REAL` / `SEASONAL` / `DATA_ERROR` / `KNOWN_CHANGE`), gerekçesini kanıt
satırlarına atıf vererek yazar ve kendi güvenini beyan eder. **Önerici**
tam olarak iki seçenek üretir — biri temkinli (geri alınabilir, dar etki
alanı), biri cesur (daha büyük kazanç, daha çok risk) — her birinde risk
seviyesi ve geri alma planı vardır. Güven düşükse, ajanlar birbiriyle
çelişiyorsa ya da kritik bir sinyale cesur cevap verilmişse **Şüpheci**
devreye girer; kritik ve çekişmeli bir kartta ise üç sandalyeli bir
**inceleme paneli** toplanır, çoğunluk karar verir, muhalefet ve çekimser
kalanlar kayda geçer.

Para rakamlarını hiçbir model üretmez. Hepsi Python'da hesaplanır; modelin
anlatısındaki para benzeri sayılar hesaplanan rakamlara karşı **±%5**
kontrolden geçirilir ve tutmayanlar işaretlenerek gösterilir.

### 3. Karar — insan olmadan hiçbir şey ilerlemez

Öneriler karar kutusuna kart olarak düşer. Operatör gerekçesiyle onaylar ya
da reddeder; reddederken gerekçe zorunludur. `executed` geçişi
**simülasyondur** ve detaya `SIMULATION` işareti yazar — hiçbir bulut
kaynağına dokunulmaz.

### 4. Hafıza — verilen kararlar geri besler

Her karar hafızaya yazılır. Önerici, benzer sinyallerde geçmiş kararları
bağlamına alır (en fazla 5 kayıt) ve **bunu kartta açıkça söyler** — kaç
karar dikkate alındığı görünür. Ayrıca her öneri, zincirin gerçekte nasıl
koştuğunu adım adım saklar: hangi ajan, hangi kaynak (canlı model / fake /
kural tabanlı fallback), ölçülmüş süre, şüpheci sonucu, hatırlanan kararlar.

Yerleşmiş imzalardan **reflex kuralı taslakları** çıkarılır
(`GET /reflex/suggestions`). Aynı servis, aynı severity, aynı yön ve aynı
çözüm kategorisi yeterince kez, aynı tercih edilen tavırla ve **hiç
reddedilmeden** onaylanmışsa sistem izleyeceği kuralı taslak olarak yazar:
koşulu, eşiği, gerekçesi ve dayandığı karar numaralarıyla. İçinde tek bir ret
ya da karışık tavır olan bir imza **çekişmeli** sayılır ve hiçbir şey üretmez
— operatörler o konuyu kapatmadıysa makine de kapatmaz; çekişmeli sayısı
gizlenmeden yayımlanır ki "bugün kural yok" ile "anlaşmazlığı sakladık"
birbirinden ayrılabilsin. Hiçbiri hiçbir şeyi etkinleştirmez: makine taslağı
yazar, kuralı insan yürürlüğe koyar.

### 5. Muhasebe — sonuç ölçülür

`/analytics/*` altında HITL hunisi, onaylanan tasarruf, pencere-üstü-pencere
trend, ay sonu tahmini, what-if, ROI, kalibrasyon ve sistemin **kendi LLM
harcamasının** defteri var. Buna ek olarak:

- `GET /analytics/quality` — masa çalışıyor mu: kabul oranı, karara kadar
  geçen sürenin ortalaması ve medyanı (silinmez izden okunur, zaman aşımları
  ve yeniden açılan kartlar rakamı güzelleştiremez), tekrarlama, karar başına
  LLM maliyeti, ortalama ajan güveni ve **en sık tetiklenen belirsizlik
  kaynakları**.
- `GET /analytics/receipts` — bir nabız turunun faturası: kaç ajan turu, kaç
  panel sandalyesi cevap verdi, ölçülmüş reflex/ajan/duvar-saati süreleri,
  harcanan LLM çağrı bütçesi.
- `GET /runbooks/effectiveness` — hangi runbook'un önerisi gerçekten
  onaylanmış: kalıcı kararlardan her seferinde yeniden hesaplanır, saklanmaz,
  en fazla bir sıra kaydırır ve işin içinde model yoktur.

## 4. Odalar

Panel tek sayfa ama odalar gerçek URL — geri/ileri tuşu ve link paylaşımı
çalışır. Beş palet (horizon, night, paper, dawn, vivid), yazı boyu ve satır
aralığı ayarlanabilen bir erişilebilirlik paneli, sıkı CSP.

| Oda | URL | Ne var |
|---|---|---|
| **Watch** | `/watch` | Masa kartları (hattın kendi sağlığı, ne yapılabilir), anomali akışı, maliyet defteri, canlı hassasiyet ayarı |
| **Investigation** | `/investigate` | Kanıt penceresi, baseline, sapma, analist triyajı, zenginleştirme (etki alanı, çerçeve etiketi, doğrulama planı, runbook) |
| **Decision Desk** | `/decide` | Karar kutusu (operatör kimliği + gerekçe) ve denetim defteri |
| **Intelligence** | `/intel` | Operasyon analitiği ve piyasa fırsatları tablosu |
| **Brain** | `/brain` | Insights, öz-inceleme döngüsü, kayıtlı rutinler, runbook araması, dedektör backtest'i, operatör girişi |
| **Broadsheet** | `/broadsheet` | Her odanın tek sayfada hâli — ekran görüntüsü ve baskı için |

Watch odasının başındaki **masa** (the desk) en son eklenen yüzey. Ürünün
kendi hakkındaki altı kanıtını, her birini tek satıra indirerek gösteriyor:
defterin bütünlüğü (`/audit/verify`), karar kalitesi (`/analytics/quality`),
tur faturaları (`/analytics/receipts`), runbook isabet oranı
(`/runbooks/effectiveness`), nöbetin kendi vitalleri (`/ops/health/watch`) ve
uçuş öncesi kontrol (`/ops/preflight`). Hepsi zaten URL'den erişilebiliyordu
ama arayüzde hiçbir yerde görünmüyorlardı; broadsheet her satıra aynı ağırlığı
verdiği için — onu güzel yapan şey tam da buydu — hepsini eşit biçimde
saklıyordu.

## 5. Ajanlar ve yetkileri

`GET /agents` bu listeyi koddan üretir; aşağıdaki tablo o listenin Türkçesi.

| Ajan | Ne yapar | Arkasında ne var | Ne zaman | Yetkisinin sınırı |
|---|---|---|---|---|
| **reflex** | Mission ayarlarını çözer, taramayı koşar, gecikmesini ölçer | Saf Python | Her tarama ve her nabız | LLM yok; mission YAML'ı önce sert doğrulanır |
| **analyst** | Triyaj, kanıt atıfları, güven beyanı | Gemini / fake / kural tabanlı fallback | Kalıcı her maliyet anomalisi | Kanıt atıfları doğrulanır; kritik sinyalde öz-yansıma; cevaplar cache'lenir, fallback'ler asla |
| **recommender** | Temkinli + cesur iki seçenek, risk ve geri alma; karar hafızasını kullanır ve açıklar | Gemini / fake / fallback | Analiz edilmiş her anomali | Tasarruf Python'da hesaplanır; ±%5 sayısal son kontrol; sinyal başına tek açık kart |
| **skeptic** | Çekişmeli taslağa itiraz eder, tavrı çevirebilir | Gemini / fake | Düşük güven, çelişki veya kritik sinyale cesur cevap | Karar başına en fazla bir çağrı; tutanak saklanır |
| **chronicler** | Turun hesaplanmış gerçeklerini operatör brifingine çevirir | Gemini / fake / fallback | Nabız başına bir kez | Rakam uydurmaz; bütçeden düşer; tam olarak aynı gerçeklerle cache'lenir |
| **operator** | Onaylar, reddeder, (simüle) yürütür | **İnsan** | Karar kutusu | Onaysız hiçbir şey çalışmaz; kararlar idempotenttir; gerekçe kayda geçer |

Kritik ve çekişmeli kartlarda toplanan üç sandalyeli panel şüphecinin
tırmanma basamağıdır: canlıda üç farklı Gemini modeli, çevrimdışında
gerçekten farklı üç deterministik persona koşar. En az iki sandalye cevap
vermeden bir taslak devrilemez; cevap vermeyen sandalye ve azınlıkta kalan
görüş kayda geçer.

**Belirsizlik kaynakları beyan değil, türetimdir.** "Kısa baseline",
"tek günlük kanıt", "hiç kanıt atfı yok", "kirli baseline", "sandalye
çekimser" gibi kodlar ajanın kendi hakkında söylediği şeyler değil, elindeki
kanıt hakkındaki olgulardır — dolayısıyla anlatıyı Gemini de yazsa fake
sağlayıcı da yazsa aynı çıkar. Güven puanı şişirilebilir, bunlar
şişirilemez.

## 6. Tespit matematiği, sade dille

Her servis **kendi geçmişiyle** karşılaştırılır; servisler birbiriyle
kıyaslanmaz.

- **Rolling baseline.** İstatistik, veri setinin en yeni gününden geriye
  doğru gerçek bir takvim penceresinden gelir (finops'ta 28 gün). Aylar
  öncesinin rejimi bugünün baseline'ını zehirleyemez; verisi pencereden önce
  kesilmiş bir servis fosillere karşı skorlanmak yerine "yetersiz veri"
  listesine düşer.
- **Yetersiz geçmiş.** Pencerede en az 7 kayıt yoksa servis hiç skorlanmaz,
  `insufficient_data_services` altında ayrıca raporlanır. İki nokta baseline
  değildir.
- **z-score.** `(o günün maliyeti − pencere ortalaması) / pencere standart
  sapması`. Mutlak değeri threshold'a eşit veya ondan büyükse kayıt sinyal
  olur; `critical_z`'ye eşit veya ondan büyükse `critical`, değilse
  `warning` işaretlenir.
- **MAD.** Ortalama yerine medyan, standart sapma yerine `1.4826 × medyan
  mutlak sapma`. Gerekçesi net: tek bir büyük sıçrama, kendisini ölçeceğiniz
  ortalamayı şişirir — medyan şişmez. Medyan tamamen düzse (MAD sıfır) klasik
  z-score devralır ve kayıt bunu `mad->zscore` olarak etiketler; sessiz bir
  vazgeçiş yok.
- **Residual.** Önce pencereye en küçük karelerle bir trend çizgisi oturur,
  sapma sonra o çizgiye göre ölçülür. Sürekli büyüyen bir servis için
  "ortalamadan ne kadar uzak" yanlış sorudur: ortalama kalıcı olarak geride
  kalır, penceredeki her geç gün sapma gibi görünür ve şişen yayılım asıl
  sapan günü saklar. Doğru soru "gitmekte olduğu yerden ne kadar saptı".
- **Seasonality (opsiyonel).** Pazartesi Pazartesi'yle karşılaştırılır — ama
  yalnızca her gün kovası kendi başına bir baseline olacak kadar doluysa.
  Sayısal şart şu: kendini içeren bir popülasyon standart sapmasıyla, n
  örnekli bir grupta ulaşılabilecek en büyük |z| değeri `sqrt(n−1)`'dir;
  `n − 1 ≤ threshold²` olan bir kova matematiksel olarak hiçbir şeyi
  işaretleyemez, yani tespiti sessizce kapatırdı. Şart sağlanmıyorsa düz
  baseline korunur.
- **Leave-one-out (opsiyonel, varsayılan kapalı).** Ölçülen günü kendi
  baseline'ından çıkarır, böylece tek bir büyük aykırı değer kendisini
  ölçeceği merkezi ve yayılımı şişiremez.
- **Bozuk kayıt.** Maliyeti eksik, sayıya çevrilemeyen veya sonlu olmayan
  (NaN, ±∞) kayıtlar **hiçbir istatistik hesaplanmadan önce** atılır ve
  sayılır. Bu kozmetik değil: NaN her eşik karşılaştırmasında `False` döner,
  yani atılmasaydı işaretleme kontrolünden sessizce geçip geçersiz JSON
  olarak tele çıkardı.
- **Yayınlanan skor kararı veren skordur.** Skor önce yuvarlanır, sonra
  işaretleme ve severity ona göre belirlenir; payload'dan yeniden hesaplayan
  biri her zaman kayıtlı severity ile aynı sonuca varır.
- Her anomali, hangi dedektör ve hangi parametrelerle işaretlendiğini
  (`detector`, `detector_params`) kendi içinde taşır — "bu neden
  işaretlendi" sorusunun kalıcı bir cevabı var.

Sistem **sapmayı** tespit eder, **nedeni** değil. "Bu neden oldu" sorusunun
cevabı Analist'in kanıtlı hipotezidir ve arayüzde hipotez olarak
etiketlenir. Makine öğrenmesi yok; `app/detection.py` içindeki aritmetiğin
ötesinde hiçbir şey öğrenilmiyor.

Dedektörlerin birbirine üstünlüğü iddia edilmiyor: `GET /metrics/backtest`
ekili sentetik ground truth üzerinde her skorlayıcının precision/recall'ünü
yan yana ölçüp gösteriyor.

## 7. Mission DSL ve üç mission'ın farkı

Bir mission, tek bir nöbetin bildirimsel tarifidir: neyi, hangi dedektörle,
hangi eşiklerle, hangi kurumsal niyet altında izleyeceğiz ve ne zaman
tartışmaya tırmanacağız. Dosyalar `configs/<isim>.yaml`.

Yükleyici bilinçli olarak affetmez, çünkü bir config dosyası için hata
fırlatmanın her alternatifi sistemin sonradan söyleyeceği bir yalandır:

- **bilinmeyen anahtar** = hiçbir şey yapmayan bir düğme — dosyada canlı
  görünür, hiçbir tarama onu okumaz;
- **tekrarlı anahtar** = sessizce sonuncusu kazanır, yani operatörün okuduğu
  satır yürürlükteki ayar değildir;
- **gevşek tip dönüşümü** (sayı beklenen yerde `"2.0"`, boolean beklenen
  yerde `"no"`) = bozuk bir şablonu ya da hiç gerçekleşmemiş bir ortam
  değişkeni ikamesini saklar;
- **dedektörün sessizce ezeceği değer** (7 günün altında bir pencere) =
  dosyadaki sayı hiç çalışmaz ve hiçbir yer bunu söylemez;
- **gölgeleyen dosya** (`finops.yml`, `FinOps.yaml`) = düzenlediğiniz dosya
  yüklenen dosya olmaz.

Hepsi yüklemeyi reddeder ve hata mesajı dosyayı, anahtarı ve kabul edilecek
aralığı adıyla söyler — ne düzelteceğini söylemeyen bir ret, uzamış bir
kesintiden başka bir şey değil. YAML `SafeLoader` alt sınıfıyla veri olarak
okunur (Python nesnesi üretemeyecek etiket kümesi), sonra Pydantic'te
`strict` ve `extra="forbid"` ile doğrulanır. Mission adı bir dosya adı
bileşeni olduğu için katı bir slug'a kısıtlanmıştır: `configs/` dışına
çıkan bir yol asla oluşamaz. Sayısal tavanlar (z için 100, pencere için 365
gün) istatistik görüşü değil, yazım hatası yakalayıcıdır — kayan bir ondalık
(2.0 → 2000.0) bir hattı sonsuza dek susturur ve bunu yaparken sapasağlam
görünür.

| | **finops** | **security** | **fraud** |
|---|---|---|---|
| Kaynak | günlük servis maliyeti | günlük güvenlik olayı sayısı | ödeme olayları |
| Dedektör | z-score | **MAD** | z-score (**atıl** — aşağıya bakın) |
| threshold / critical | 2.0 / 3.0 | **1.75 / 2.75** | 2.75 / 3.5 |
| Baseline penceresi | 28 gün | **14 gün** | 21 gün |
| Tartışma eşiği | 0.6 | 0.75 | 0.5 |
| LLM ajanı | var | yok | yok |

Farklar keyfî değil, her biri o hattın fiziğinden geliyor:

- **security daha alçak bir bardan geçer** çünkü bir kimlik bilgisi patlaması
  küçük ve hızlıdır; büyümeden yakalanması gerekir. **MAD kullanılır** çünkü
  patlamanın kendisi, z-score'un onu ölçeceği ortalamayı şişirir. **14 gün**,
  çünkü güvenlik desenleri harcamadan hızlı devreder — bir aylık geçmiş artık
  var olmayan bir sistemi tarif eder.
- **fraud'un detection bloğu şemanın zorunlu kıldığı ama bu hatta çalışmayan
  bir bloktur.** Fraud yalnızca `app/fraud.py`'deki yayınlanmış kural skoruyla
  çalışır; hiçbir istatistiksel tarama fraud olaylarının üzerinden geçmez.
  Kural skoru elle yeniden hesaplanabilir: tutar (tipik tutarın ≥10 katı 40
  puan, ≥3 katı 25, ≥1.5 katı 10), hız (son 10 dakikada ≥5 işlem 25 puan,
  ≥3 işlem 15), coğrafya 20, hesap yaşı 15 — toplam 100'de kapanır, hangi
  kuralın kaç puan verdiği kartta tek tek yazar. 70 ve üzeri "hold", 40 ve
  üzeri "review". Sistem **asla** bir ödemeyi bloklamaz, yalnızca önerir.
- **finops olduğu gibi kaldı**: demonun yürüdüğü hat bu, her ekran görüntüsü
  ve her test bu sayılara sabitlenmiş durumda.

Panelde bir açılır menü aktif mission'ı canlı çevirir
(`POST /pulse?mission=security`, bellek içi override): eşikler, dedektör ve
tartışma barı başka bir YAML'dan yeniden okunur ve mission'ı takip eden bütün
yüzeyler birlikte döner. Tek motor, üç mission.

## 8. Human-in-the-loop yaşam döngüsü

```
proposed ──onay──> approved ──yürüt──> executed (SİMÜLASYON)
   │
   └────ret────> rejected ──yeniden aç──> proposed (TTL sıfırlanır)
```

- **Karar kutusundan geçmeyen hiçbir şey yürütülemez.** `executed` yalnızca
  `approved` bir karttan gelir; başka bir durumdan denenirse 409.
- **Reddederken gerekçe zorunlu** (boş gerekçe 422 döner). Onayda gerekçe
  isteğe bağlı ama verildiğinde karar kaydına yazılır.
- **Kararlar idempotent.** `Idempotency-Key` başlığıyla aynı istek güvenle
  tekrarlanabilir — ağ hatasında çift onay oluşmaz.
- **Operatör kimliği sunucudan türetilir.** Giriş yapmış bir kullanıcının
  kimliği karara sunucu tarafında yazılır; tarayıcıdan gelen serbest metin
  değil.
- **Zaman aşımı istekle tetiklenir.** Zamanlayıcı yok: süresi dolmuş kartlar
  bir sonraki istekte kapatılır, çünkü hedef ortam istekler arasında uyuyor.
- **Alarm bastırma.** Bir kart hâlâ **karara bağlanmamışken** aynı hattaki
  aynı servisin sonraki sinyalleri yeni kart açmaz, o karta sayılı bir tekrar
  olarak katlanır (varsayılan 24 saat). Kapsamı bilinçli olarak dar: yalnızca
  `proposed` bir kart bastırır — insan onayladığı, reddettiği ya da yürüttüğü
  anda o konuşma kapanır ve sonraki sinyal kendi kartını hak eder, çünkü yeni
  bir olguyu karara bağlanmış bir karta katlamak kimsenin yargılamadığı bir
  şeye eski bir hükmü uygulamak olurdu. Bastırma hatlar arasında da geçmez:
  aynı servis için bir fraud hold'u ile bir maliyet kartı iki ayrı konuşmadır.
- **Roller.** `viewer < analyst < approver < admin`. Kendi kaydolan herkes
  daima `viewer` olur — yabancılar bakabilir, karar veremez. Canlı-operasyon
  kipinde (`SENTINEL_REQUIRE_APPROVER=1`) onay/ret/yürüt fiilleri imzalı bir
  `approver` veya `admin` oturumu ister.
- **Denetim defteri kendini kanıtlar.** Her karar ve her yaşam döngüsü geçişi,
  bir öncekinin hash'iyle birlikte mühürlenir. `GET /audit/verify` zinciri
  başından yürür ve **ilk kırık halkayı** adıyla söyler; dört kırılma birbirinden
  ayırt edilir: araya kayıt sokulmuş/silinmiş (`chain_break`), defter satırının
  kendisi düzenlenmiş (`entry_rewritten`), kaynak satır sonradan değiştirilmiş
  (`source_modified`), kaynak satır silinmiş (`source_deleted`). Mühürleme
  yazma anında, çağıranın transaction'ı içinde olur — okurken mühürleyen bir
  zincir yalnızca okumanın kendi içinde tutarlı olduğunu kanıtlardı. Karar
  masasından geçmemiş satırlar (örneğin demo sıfırlamasının ektiği geçmiş
  kararlar) zincirin **dışında** kalır ve `unsealed` olarak raporlanır.

## 9. Para rakamları

`estimated_savings` tek para kaynağıdır ve şunu yapar: anomalinin günlük
fazlasını (`o günün maliyeti − servis baseline'ı`) 30 güne projekte eder,
sonra bir yakalama katsayısıyla çarpar — temkinli seçenekte 0.35, cesur
seçenekte 0.70.

Bu bir **senaryo tahminidir**, öngörü ya da muhasebe rakamı değil: fazlanın
devam edeceğini ve bir kısmının kontrol altına alınacağını varsayar. Bu
varsayım rakamın yanında seyahat eder — tasarruf gösteren her yüzey onu
üreten yöntem cümlesini de gösterir. Rakamlar Python'da hesaplanır, model
tarafından asla üretilmez ve modelin anlatısındaki para benzeri sayılar
hesaplananlara karşı ±%5 kontrolden geçer. Bunların hiçbiri rakamları
finans-sınıfı yapmaz.

## 10. Neyin simüle, neyin gerçek olduğu

| Gerçek | Simülasyon / sentetik |
|---|---|
| Tespit aritmetiği — deterministik, tekrar üretilebilir | Veri: paketlenmiş sentetik fixture'lar (maliyet, güvenlik, ödeme) |
| Karar kaydı, sunucudan türetilen operatör kimliği, silinmez iz, hash zinciri | Altyapı değişikliği: **asla** — `executed` geçişi detaya `SIMULATION` yazar |
| Webhook teslimatı: `SENTINEL_EXECUTE_WEBHOOK_URL` ayarlıysa karara bağlanan olay kaydı gerçekten POST edilir, sonucu denetim detayına yazılır | Yayındaki demo modeli: `SENTINEL_FAKE_LLM=1` — cevaplar deterministik fake sağlayıcıdan gelir |
| Ölçülen süreler, çağrı sayıları, bütçe tüketimi | Canlı bant (`SENTINEL_SIM_STREAM`): rozette ve payload'da `simulated: true` yazan sentetik tik akışı |

İki nokta özellikle jüri için altı çizilmeli:

1. **"Mutasyon simüle, teslimat gerçek"** — cümle tam olarak budur ve mimari
   dokümanı ile arayüz aynı cümleyi kullanır. Onay hiçbir bulutta hiçbir şeyi
   değiştirmez; binadan gerçekten çıkan şey, transaction commit olduktan sonra
   operatörün kendi ayarladığı uca giden olay kaydıdır.
2. **Yayındaki demoda ajan güveni 0.50 okur** — bu ölçülmüş bir inanç değil,
   fake sağlayıcının sabit yer tutucusudur. 0.50 tartışma eşiğinin altında
   kaldığı için tırmanma merdiveni de pratikte her kartta çalışır. Canlı Gemini
   yolu uygulanmış ve faturalandırması kapalı bir projedeki gerçek anahtarla
   uçtan uca doğrulanmıştır; demo onun üzerinde koşmuyor çünkü sıfır kota,
   sıfır maliyet ve kayıt sırasında ortalıkta anahtar olmaması tercih edildi.

## 11. Güvenlik sınırları

- **Sıkı CSP.** `script-src 'self'`; Swagger repoda barındırılıyor, fontlar
  yerel — hiçbir yolda uzak sunucuya izin yok.
- **Dışa çıkışta SSRF koruması** (`app/netguard.py`). Yalnızca `https`;
  loopback, link-local (bulut metadata aralığı), özel, taşıyıcı-NAT,
  multicast, ayrılmış ve belirsiz adresler — ister düz yazılmış ister DNS'ten
  çözülmüş olsun — reddedilir; yönlendirme takip edilmez, çünkü `302 →
  169.254.169.254` cevabı veren halka açık bir host, geçmiş bir adres
  kontrolünün içinden yürüyüp geçerdi. Geliştirici kaçış kapağı ayrı bir
  ortam değişkeninin arkasında.
- **Açılışta konfigürasyon denetimi** (`app/configcheck.py`). Her güvenlik
  özelliği bir ortam değişkeni ve hepsi varsayılan olarak kapalı — dizüstünde
  doğru, dağıtımda yanlış. `SENTINEL_ENV=production` altında her bulgu
  ölümcüldür: uygulama demo duruşuyla gerçek kullanıcıya hizmet vermektense
  açılmayı reddeder. Diğer bütün profillerde davranış değişmez, bulgular
  `[CONFIG]` uyarısı olarak loglanır.
- **Yerel kimlik** (`app/auth.py`). Salt'lı PBKDF2-SHA256, 240.000 tur; dört
  rol; 12 saatte sona eren oturumlar; kullanıcı adı başına hız sınırlama ve
  iptal edilebilir oturumlar.
- **Salt okunur vitrin** (`SENTINEL_READONLY=1`): halka açık linkte her yazma
  gerekçesiyle reddedilir ve arayüz yerine getiremeyeceği fiilleri 403
  verecek butonlar olarak sunmak yerine devre dışı bırakır.
- **Ajan sınırında** güvenilmeyen veri işaretlenerek (prompt spotlighting)
  veriliyor, nabız başına LLM çağrı bütçesi ve sert taşıma zaman aşımı var,
  sağlayıcı erişilemezse her ajan etiketli kural tabanlı bir cevaba düşüyor.
- **İzlenebilirlik.** Her HTTP isteği bir korelasyon kimliği alır, bu kimlik
  `X-Request-ID` başlığında döner ve zincirin her adımının log satırına
  otomatik olarak eklenir; `SENTINEL_LOG_FORMAT=json` bütün akışı satır başına
  bir JSON nesnesine çevirir. `GET /metrics` uygulamanın zaten saydığı
  sayıları Prometheus metin formatında dışa verir.
- **Kendi kendini tarar.** `bandit` kendi kaynağımıza, `pip-audit` yüklediğimiz
  bağımlılıklara CI'da her push'ta koşar.

Olmayanlar da açıkça yazılı: OIDC/SSO yok, MFA yok, parola sıfırlama yok,
çok kiracılı izolasyon yok, harici sızma testi yapılmadı. Detaylı ve dürüst
liste [LIMITATIONS.md](LIMITATIONS.md) ve
[SECURITY.md](../SECURITY.md) dosyalarında.

## 12. Nasıl çalıştırılır

```bash
make setup && make demo     # fake sağlayıcı, tarihler bu haftaya kaydırılmış
# panel: http://127.0.0.1:8000/   ·   API dokümanı: /docs
```

Başka bir kabukta:

```bash
make smoke      # çalışan sunucuya karşı uçtan uca PASS/FAIL taraması
make test       # ruff + bütün test paketi (fake sağlayıcı, kotasız)
make verify     # dokümanlardaki sayaçları ve bağlantıları ölçer
```

Zinciri tek çağrıyla sürmek ve sunucu çıktısındaki etiketli akışı
(`[SIGNAL] / [ANALYST] / [DEBATE] / [RECOMMENDER] / [HITL]`) izlemek için:

```bash
curl -X POST "http://127.0.0.1:8000/pulse"
```

Docker ile: `docker build -t cloudsentinel . && docker run -p 8000:8000 cloudsentinel`

**Ölçülen durum:** API yüzeyi 59 endpoint, test paketi 1317 test topluyor,
ruff temiz. Bu iki sayı tahmin değil: `bash scripts/verify_release.sh` önce
koddan ölçüyor (`pytest --collect-only` ve uygulamanın kendi OpenAPI
çıktısı), sonra bu sayfa dâhil her dokümanda yazan iddiayla karşılaştırıp
tutmayanı hata sayıyor. Aynı script bütün göreli bağlantıları da yürüyor.

## 13. Bilinçli sınırlar

Aşağıdakiler eksik değil, yarışma penceresinde alınmış kapsam kararları —
her biri demoyu dürüst ve tekrar üretilebilir tutuyor:

- veri sentetik; canlı hatlar (kendi telemetrimiz, dışa aktarılmış fatura
  CSV'si, harici JSON feed'ler) uygulanmış ama ortam değişkeniyle kapalı ve
  hiçbiri gerçek bir üretim ortamına karşı çalıştırılmadı;
- depolama geçici disk üzerinde SQLite; yeniden dağıtım geçmişi siler. Hash
  zinciri geçmişin **yeniden yazılmadığını** kanıtlar, geçmişin hayatta
  kalmasını sağlamaz — o Postgres'in işi ve bilinçli olarak alınmadı (ücretsiz
  yönetilen bir instance 30 gün içinde sona erer, yani halka açık link
  yarışmadan kısa süre sonra ölürdü);
- yürütme her zaman simülasyon; fraud hattı deneysel ve makine öğrenmesi
  içermiyor; MITRE ATT&CK ve FinOps Framework etiketleri bir eşleme tablosundan
  geliyor, sınıflandırma motorundan değil;
- yük/dayanıklılık testi yapılmadı, harici pentest yapılmadı, ajan zincirinin
  değerlendirmesi kendi ürettiğimiz, dokuz aileye bölünmüş 288 vakalık bir
  golden set ([EVAL_SCORECARD.md](EVAL_SCORECARD.md)) — kendi hatalı
  pozitifleri konusunda dürüst, ama bağımsız bir benchmark değil.

Tam ve güncel liste: **[LIMITATIONS.md](LIMITATIONS.md)**. Demoda bir şey bu
sayfanın kabul ettiğinden fazlasını yapıyor gibi görünüyorsa, inanılması
gereken o sayfadır.

## 14. Jüri için üç dakikalık tur

1. `make demo` çalıştırın, `http://127.0.0.1:8000/` açın — **watch** odası.
   Hassasiyet kaydırıcısını oynatın: tarama canlı yeniden koşar.
2. Bir sinyalde *investigate →* deyin. Kanıt penceresi, baseline, sapma; sonra
   *run analyst agent →* ile atıflı triyaj.
3. Sağ alttaki **ajan akışı** panelini açın: devralmalar, teslimler, şüpheci
   itirazları, kararlar — zincirin her adımı olurken akıyor.
4. *file recommendation →*, gerekçe yazın, karar kutusunda onaylayın ya da
   reddedin. Altyapı değişikliği her zaman simülasyondur; defter o karta
   dokunan her eli hatırlar.
5. Üstteki mission menüsünü **security**'ye çevirin ve tekrar nabız atın:
   aynı motor, başka bir duruş — daha alçak bar, MAD, iki haftalık pencere.
6. `GET /audit/verify` ile defterin bütünlüğünü kendiniz doğrulayın.

---

<sub>Takım CloudSentinel — YZTA Bootcamp 2026 · AI Track · Grup 60 ·
"Makine izler, insan karar verir."</sub>
