# FSO Fizik Simülatörü – Proje Denetim Raporu

Kapsam: `backend/engine.py`, `backend/app.py`, `backend/comsol.py`, `engine.js`, `tests/`. (`backend/optics.py` ve `backend/numerics.py` başka ajanlarca yazıldığı için kapsam dışı.)
Yöntem: kod okuma + doğrulayıcı Python/Node parçacıkları + FastAPI `TestClient` ile gerçek istekler. Test durumu: `python3 -m pytest -q` yeşil (denetim başında 39 test; sonradan eklenen `test_numerics.py` / `test_optics.py` dosyaları bu sayıya dahil değil).
Önem dereceleri: Kritik / Yüksek / Orta / Düşük.

---

## Özet

Motor yapısal olarak düzgün ve Python/JS arasında büyük ölçüde aynı formülleri kullanıyor; vektörel Snell yasası, Rytov formülü ve Beer-Lambert dönüşümü matematiksel olarak doğru. Ancak **atmosferik zayıflama (Kim/Kruse) birim hatası**, **nem düzeltmesinin ~100 kat fazla olması**, **ışın izleyicinin pratikte hiç kırılma üretmemesi**, **lens modelinin difraksiyon sınırını ihlal etmesi** ve **veri iletiminin tamamen deterministik/ikili (0 veya %100) olması** nedeniyle "fiziksel doğruluk" iddiası şu an savunulabilir değil. API tarafında yol geçişi (path traversal) açığı yok, CORS varsayılan kapalı; fakat `/api/send-data` ile **tek istekte 289 MB'lık yanıt / 70 sn CPU** üretmek mümkün (DoS). JS tarafında hiç otomatik test yok; CI yalnızca `node --check` yapıyor.

En önemli bulgular (detay aşağıda):
1. Kritik – Kim/Kruse sis/pus zayıflaması `3.91/V` (km⁻¹) değerini dB/km gibi kullanıyor → 4.34 kat eksik (sis 7.8 dB/km, gerçek ≈34 dB/km). Üstelik buna giden sabit tablo değerleri (haze 4, fog 40) ölü kod.
2. Kritik – Edlén nem düzeltmesi 100 kat fazla (−7.1e-5, doğrusu ≈ −7e-7).
3. Yüksek – Işın izleyici eksen-hizalı ışında normal geliş nedeniyle hiç sapmıyor; `lateralDeviation` her zaman 0, Snell yasası nominal linkte hiç devreye girmiyor.
4. Yüksek – LensSystem sabit tablo: Cassegrain/Galilean için efektif diverjans (5e-5 / 8e-5 rad), 4 mm ışın için difraksiyon sınırının (≈2.5e-4 rad) altında; fiziksel olarak imkânsız.
5. Yüksek – `/api/send-data` her bit için tüm ray path'ini serileştiriyor: 1000 bit → 289 MB, 70 sn.
6. Yüksek – BER/successRate anlamsız: kanal ya %100 ya %0 (42 kombinasyonun tümü {0, 100}).

---

## Fizik Doğruluğu

### F1. Kim/Kruse zayıflama birim hatası ve ölü kod — **Kritik**
- `backend/engine.py:397-410` (sabit dB/km tablosu) ve `engine.py:412-423` (Kim/Kruse): `alpha_dB_km = 3.91 / V * (550/λ)^q`. `3.91/V` **km⁻¹ (Np/km) cinsinden sönüm katsayısıdır**, dB/km için 4.343 ile çarpılmalıdır.
- Doğrulama (çalıştırıldı): fog (V=0.5 km) → kod 7.82 dB/km, doğrusu 3.91/0.5·4.343 = **33.96 dB/km**. haze (V=4 km, 1550 nm) → kod 0.373 dB/km; Kim q=0.16V+0.34 ile doğrusu ≈ **1.54 dB/km**.
- Sonuç: 400-406. satırlardaki haze=4.0 / fog=40.0 dB/km sabitleri `if weather in ["haze","fog"]` bloğunda hemen ezildiği için **ölü kod**; üstelik eski sabitler (fog 40) yeni hesaplanan değerden (7.82) 5 kat farklı, yani sis kaybı sessizce 5 kat azaltılmış. `engine.js:460-489` aynı hatayı birebir taşıyor.
- `q` seçimi (`414-422`): Kruse `q=0.585·V^(1/3)` (V<6 km), 1.3 (6-50 km), 1.6 (>50 km) şeklindedir; kodda V>6 ise 1.3 (50 km üstü eksik) ve `wavelengthNm <= 500` için `q=1.6` dalı Kruse/Kim'de yok (UV için uydurma). Kim modeli (V<1: q=0, 1-6: 0.16V+0.34) ile Kruse (0.585V^1/3) karıştırılmış.
- V (görüş mesafesi) sabit 0.5/4.0 km'ye kodlanmış; API/UI'dan ayarlanamıyor.
- Storm (`424-431`): yorumda "~62 dB/km" yazıyor, hesap 71.1 dB/km veriyor (`3.91/0.1=39.1 + 12 + 20`); JS'te `engine.js:467` 62 yorumlu ama aynı 71.1 çıkıyor (iki sürüm de aynı sayıyı veriyor, yorum yanlış). Yağmur 6 dB/km ve kar 20 dB/km yağış oranından bağımsız sabitler; dalga boyu bağımsız.

### F2. Edlén nem düzeltmesi 100× fazla — **Kritik**
- `engine.py:177-181`: `humidity_correction = -4.15e-8 * e` (e: Pa). Edlén 1966'daki katsayı mmHg cinsindendir (`−f(5.722−0.0457σ²)·1e-8`, f mmHg); Pa'ya çevrildiğinde ≈ `-4.3e-10 · e`. Kodda mmHg katsayısı Pa değerine uygulanmış.
- Doğrulama: 15 °C, 101325 Pa, %100 RH, 1550 nm → Δn = **−7.08e-5**; n−1 (kuru) = 2.73e-4, yani düzeltme n−1'in %26'sı. Gerçek etki ≈ −7e-7 (%0.3). Bu, atmosfer ızgarasındaki gerçek n değişimlerini (~1e-6 mertebesi) bastıran bir hata: nem alanındaki ±%5 rastgele gürültü (`engine.py:311`) tek başına ~1e-6 altı yerine ~4e-6 n gürültüsü üretiyor.
- Aynı kod `engine.js:163`. Kuru hava terimi makul (n=1.000273, 1550 nm, 15 °C). Basınç/sıcaklık ölçeklemesi (`n_tp = (ns−1)·P/P0·T0/T`) Edlén'in tam formülü değil (yoğunluk düzeltmesi yok), düşük etki.

### F3. Işın izleyici: ızgara DDA + parçalı sabit n; ışın kırılmıyor — **Yüksek**
- Şema (`engine.py:531-719`): ODE entegratörü değil; Amanatides-Woo benzeri hücre sınırı geçişi, her sınırda vektörel Snell. Hücre içi n sabit → hücre içi eğrilik yok (ışın hücre içinde düz). Sıcaklık/n gradyanı yalnızca sınır sıçramaları olarak görülüyor. Bu, eikonal denklemin birinci mertebeden kaba bir yaklaşımı; yakınsama mertebesi tanımsız ve hücre boyutuna (100 m) bağımlı.
- Doğrulama: varsayılan link (`+x` yönü, tek eksenli) ile 6 profil (uniform, gradient, turbulent, hotspot, layered, coastal) denendi: **çıkış y,z tam 500.0000 / 500.0000, sapma 0**, `exitDirection` birim vektörü korunmuş. Sebep: ışın yalnızca x-yüzlerine dik geliyor; normal geliş → yön değişmiyor. Gradyan yalnızca y/z yüzlerine çarpan ışınlarda etki ediyor. Dolayısıyla (a) `lateralDeviation` = 0 her zaman, (b) `receiverRadius` kontrolü (`engine.py:759`) hiçbir zaman başarısız olmuyor, (c) "refractionEvents" sayısı (58-59) normal geliş olaylarıdır ve görsel olarak anlamsızdır. Gerçek yatay linkte dikey n gradyanı ışını kıvırır (ışın zaten hücre içinde de eğrilir); bu etki modelde yok.
- Kaynak ve hedef `y=z=gridY*cellSize/2` (`app.py:205-206`, `engine.py:1099`): çift boyutlu ızgarada hücre sınırı/köşesine denk geliyor (500 m = 5·100); `floor` kararlı olsa da ışın pozisyonu sayısal olarak sınırda; küçük bir perturbasyon farklı hücre dizisi üretir.
- Enerji/güç: ışın izleyici güç/Fresnel yansıma kaybı izlemiyor (Δn~1e-6 için ihmal edilebilir ama TIR dalı hiç güç kaybı hesaplamıyor). Birim vektör kayması: her adımda `normalize()` uygulanıyor, ölçülen |len−1| = 0.0; dolayısıyla drift yok ama normalizasyon hatayı gizliyor. Ölçülen Snell değişmezi: `n1 sinθi` vs `n2 sinθt` fark < 1e-16 (örnek).
- `engine.py:714` `t = tMin + eps` (1e-10 m) her adımda yapay ilerleme ekliyor → `totalDistance = 6000.0000000001` (60 adımda birikmiş); Düşük.
- `engine.py:601` `abs(nextN-currentN) > 1e-15` eşiği Snell'i atlıyor; makul. TIR dalı (`604-618`) yansıma sonrası hücreyi ilerletmeden `continue` yapıyor (aynı hücrede dolaşabilir; maxSteps=50000 ile sınırlı); mevcut testlerde hiç tetiklenmiyor (TIR sayısı 0 ölçüldü).
- Yol kaydı (`645`): `pathRecordInterval` ile seyreltiliyor, `maxSteps` bitişinde `exitPosition` son kayıtlı nokta olur (eski olabilir); Düşük.

### F4. Vektörel Snell (`engine.py:349-376`) — doğru ✔ (Bilgi)
`t = r·i + (r·cosθi − cosθt)·n` formülü doğru; TIR eşiği, reciprocity, tegetsel korunum ölçüldü (`n sinθ` farkı ≈ 1e-16). Yön yeniden normalize edildiği için sayısal sağlam.

### F5. Rytov / sintilasyon — **Orta**
- `engine.py:468-486`: `σ²_R = 1.23·Cn²·k^(7/6)·L^(11/6)` düzlem dalga için doğru (Cn²=1e-15, 6 km → 0.53; 1e-13 → 53.2 ölçüldü). Ancak:
  - Yalnızca düzlem dalga; yatay yol için küresel dalga katsayısı (0.5) ve aperture averaging yok. σ²_R>1'de (güçlü rejim) kayma/doyma (σ_I² → 1) uygulanmıyor, `σ²_R=53` olduğu gibi rapor ediliyor.
  - `avgCn2` (`engine.py:581, 656`) yol-ortalama; düzensiz Cn² için Rytov integrali `∫Cn²(s)(s/L)^{5/6}(1-s/L)^{5/6}ds` kullanılmalı (ağırlık yok).
- Link bütçesine girişi (`engine.py:511`): `scintPenalty = 5·log10(1+σ²_R)` ad hoc; σ²_R=4.9 → 3.85 dB, σ²_R=5.1 → **10 dB** (ölçüldü): 5'te süreksizlik. Fading marjini (log-normal/gamma-gamma, hedef BER'e göre) değil.
- Cn² kaynağı (`engine.py:186-190, 318`): Hufnagel-Valley'in yer terimi parametresi `Cn2Level` ile besleniyor, `v_rms` = rüzgâr hızı (HV'de 21 m/s yüksek irtifa rms rüzgarı). Link irtifasında (500 m) ölçülen Cn²: weak = 1.87e-16, moderate = 1.91e-16, strong = 5.96e-16 → **weak ile moderate fiilen aynı** (sabit 2.7e-16 terimi baskın); "Cn2Level" etiketleri link irtifasındaki gerçek türbülans seviyesini yansıtmıyor.

### F6. Geometrik kayıp — **Orta**
- `engine.py:447-466`: yakalanan oran `(D_rx/D_beam)²` **düz-tepe (top-hat)** varsayar, ama pointing kaybı Gauss (`exp(-2(offset/w)²)`) → model tutarsız. 6 km, D_beam=12.004 m, D_rx=0.1 m: top-hat 41.59 dB; Gauss (1/e² çapı 12 m) `1−exp(−2a²/w²)=1.39e-4` → 38.6 dB; **3 dB (2×) fark**.
- Işın çapı `d0 + 2θL` doğrusal toplam (kuadratik değil), difraksiyon sınırı yok; `beamDivRad` dalga boyuna ve ışın çapına bağlanmamış serbest parametre (F7).
- `engine.py:465` `math.PI hasattr` hack'i ölü/gereksiz (math.PI yok, her zaman else dalı).

### F7. LensSystem sabit tablo — **Yüksek**
- `engine.py:854-953`: ABCD matrisi yok, Gauss ışını (w0, zR, M²) yok, lens yüzeyi/ince-kalın lens denklemi yok, aberasyon/obskürasyon (Cassegrain ikincil), Fresnel/AR yok; `divergenceReduction` ve `apertureBoost` elle atanmış sabitler.
- Fiziksel ihlal (hesaplandı): `θ_diff = λ/(π·w0) = 1550e-9/(π·2e-3) = 2.47e-4 rad` (4 mm ışın çapı). Tablo: collimating 1.5e-4, galilean **8e-5**, cassegrain **5e-5** rad → hepsi difraksiyon sınırının altında; başlangıç çapı (`beamDiamM`) değişmediği için de ışın genişletici (Galilean/Cassegrain) enerji/etendue korunumunu bozuyor (Tx çapı M kat büyümeli, diverjans M kat küçülmeli).
- `apertureBoost` (alıcı çapını çarpıyor, `929`) ile vericiye ait diverjans azaltımı aynı lens girdisine karıştırılmış: gerçekte iki ayrı optik (Tx kolimatör/genişletici, Rx teleskop/odaklayıcı). `lensGain_dB` için sonuçlar (örn. Cassegrain +37 dB) bu yüzden gerçekçi değil.
- `focal_length_m`, `aperture_m` (`908-909`) saklanıyor ama hiçbir hesapta kullanılmıyor; `app.py:67-68` `lensFocalLength_m/lensAperture_m` alanları API'den kabul ediliyor ama etkisiz ve sınırsız (doğrulama yok).
- Not: `backend/optics.py` bu işi yapmak için yazılıyor; şu an `engine.py`/`app.py` ona bağlı değil.

### F8. DataTransmissionSimulator / BER — **Yüksek**
- `engine.py:744-772`: tamamen deterministik; gürültü (shot/termal), SNR, Q-faktörü, eşik karar yok. `received = bit if channelAvailable else 0`. BER (`796`) = `1 − correct/len` → kanal ya açık (BER 0) ya kapalı (BER = 1.0 tüm bitlerde, sıfırlar dahil). Doğrulama: `ComprehensiveTransmissionTest.run_all()` 42 kombinasyonda `successRate ∈ {0.0, 100.0}`. BER gerçek anlamda (Q = (P1−P0)/(σ1+σ0), BER = ½erfc(Q/√2)) hesaplanmıyor.
- "Sıfırlar her zaman geçer" iddiasının tersi: kanal kapalıyken sıfır bitinde `received=0 == sent` ama `correct=False` (kanal-bağımlı), bu tutarlı bir tercih (testlerle sabitlenmiş: `test_zero_bits_fail_when_link_is_unavailable`). Fakat `received` alanı yanıltıcı: kanal kapalıyken 0 bitini "alınmış 0" olarak raporluyor, gerçek alıcı "sinyal yok"u 0'dan ayıramaz. Ölçüm: storm senaryosunda `sendBit(0)` → `received=0, correct=False`, `signalPower=0.0`.
- `engine.py:770`: `signalPower` bit=1 için **dBm** (−459 dBm ölçüldü), bit=0 için `0.0` (lineer/dBm karışık; 0 dBm = 1 mW). Birim tutarsızlığı; JS'te (`engine.js:832`) aynı.
- `engine.py:824-827`, `841-843`: `random` deseni tohumsuz `random` modülü kullanıyor → API'de tekrarlanamaz sonuç; `seed` parametresi yok. `ascii_hello` (`831-832`) `length` parametresini yok sayıyor (API `numBits=5` + `ascii_hello` → 40 bit döner, doğrulandı). `prbs7` periyodu 127 ✔ (ölçüldü; 64 bir).
- `engine.py:729` `self.beamDivergence`, `728` `receiverRadius` dışında kullanılmayan alan; `_getTransmissionState` önbellek yoksa weather/lens/güç vermeden varsayılan link bütçesi kuruyor (`735-741`) → JS'teki `linkBudgetParams` desteğiyle Python davranışı farklı (bkz. tutarlılık).
- `crc16_ccitt` (`engine.py:7-30`): doğru (CCITT-FALSE), ancak veri iletim simülasyonunda hiçbir yerde kullanılmıyor (yalnızca birim testte); "CRC doğrulama" özelliği pratikte bağlı değil.

### F9. Diğer fizik notları — **Düşük/Orta**
- `engine.py:263-264`: `"uniform"` profil adı yanıltıcı: yükseklikle lapse rate (−6.5 K/km) uyguluyor → tek tip değil.
- `engine.py:311`: nem hücre başına bağımsız beyaz gürültü (±%5) → fiziksel korelasyon yok; F2 ile birleşince n gürültüsü baskın.
- `engine.py:85`: `R=8.31447` (CODATA 8.314462). Etkisi ihmal edilebilir; `temperature` ve `pressure` Float32 saklanıyor (Edlén girdilerinde ~1e-7 göreli kesme).

---

## Python/JS Tutarlılığı

Node ile `engine.js` yüklenip Python ile sayısal karşılaştırma yapıldı.

- **Aynı çıkan (✔):** `absorptionLoss` (6 hava durumu, 1550 nm) birebir; `linkBudget` (haze) P_rx −34.7533 dBm, toplam kayıp 44.7533 dB, scintPenalty 0.92588 (1e-16 mertebesinde fark). Sabitler, Rytov, geometrik kayıp, lens tablosu aynı.
- **Ortak hatalar:** F1, F2, F5, F6 JS'te de var (`engine.js:163, 460-489, 543, 582`). JS yorumları `alpha_clear … alpha_rain` için "dB/m" yazıyor (`engine.js:66-69`) ama değerler dB/km (Python `engine.py:94-98` yorumu "dB/km" demiyor, sabitler zaten kullanılmıyor) — yanıltıcı yorum.
- **Atmosfer ızgarası bit-düzeyinde aynı değil — Orta:** Python `hashVal` (`engine.py:212-218`) "Perfect JS signed 32-bit replication" diyor, ama JS (`engine.js:218-222`) çarpımı double ile yapıp `2^53`'ü aşan değerde yuvarlıyor; Python int32 sarmalıyor. Ölçüm: 8 örnek girdi için hash değerleri 1e-7 mertebesinde farklı (örn. 0.55670047 vs 0.55670057). Sonuçlar: turbulent profilde n toplamı farkı 1.7e-6, **çıkış y = 620.0596 (JS) vs 620.0650 (Py) → 5.4 mm fark** (6 km); sıcaklık/nem dizileri (float32 sonrası) eşit çıktı. Fixture-tabanlı regresyon yalnızca Python'u kilitliyor.
- **API farkı — Düşük:** JS `DataTransmissionSimulator(…, linkBudgetParams)` ve lens transmittance'ı simülatör içinde uyguluyor (`engine.js:782-805`); Python yapıcısı `trace=`/`linkBudget=` dışarıdan enjekte edilmesini bekliyor ve transmittance cezasını `app.py:234` içinde dışarıda uyguluyor. İki farklı sorumluluk dağılımı → davranışlar sapabilir.
- **JS'te `crc16_ccitt`/`validate_crc` yok** (`grep crc engine.js` boş).
- **Doğrulama eksikliği:** JS için hiçbir otomatik test yok; CI (`.github/workflows/tests.yml`) sadece `node --check` yapıyor. Python↔JS parite testi yok; `app.js:804-823` ve `app.py:/api/test` aynı 15 kontrolü iki kez yazıyor.

---

## API & Güvenlik

- **Statik dosya yolu — Güvenli ✔:** Sabit beyaz liste (`app.py:244-266`), kullanıcı girdisi yol olarak kullanılmıyor. Denendi: `/../backend/app.py`, `/%2e%2e/backend/app.py`, `/backend/app.py`, `/README.md`, `/..%2fREADME.md` → hepsi 404.
- **CORS ✔ (Düşük risk):** Varsayılan kapalı (`app.py:36-44`, env `FSO_CORS_ORIGINS`), `allow_credentials=False`; evil.com preflight → 405, CORS başlığı yok. `run.py` sadece `127.0.0.1`'e bağlanıyor.
- **Yüksek – Kaynak tüketimi / DoS (`/api/send-data`):** `app.py:303-316` her bit için `serialize_trace(r["trace"])` (aynı trace'in kopyası) üretiyor. Ölçüm: `gridX=600, gridY=4, gridZ=4, cellSize=10, numBits=1000` → **69.7 sn, 288.859 KB (~289 MB) yanıt**; `numBits` üst sınırı 10000 (`app.py:80`) → ≈2.9 GB'a kadar. Çözüm: trace'i yalnızca bir kez döndürün veya bit bazında trace'i kaldırın.
- **Yüksek/Orta – CPU maliyeti (`/api/simulate`, `/api/export/*`, `/api/test-transmission`):** `validate_grid_limits` 1.000.000 hücreye izin veriyor (`app.py:18-26`); `applyThermalProfile` saf Python döngüsü, ölçüm: 20.000 hücre = 2.0 sn (turbulent) → 1M hücre ≈ 100 sn; ayrıca `AtmosphereGrid.__init__` → `initializeDefault` aynı döngüyü ikinci kez koşuyor (`engine.py:124, 137-148`). `/api/test-transmission` limiti **tek ızgara** için uyguluyor ama **7 ortam × ızgara** üretiyor (`engine.py:1084-1096`) → 7M hücreye kadar. Zaman aşımı, eşzamanlılık sınırı, rate limit, kimlik doğrulama yok. Öneri: NumPy vektörizasyonu + hücre sınırını 100-200k'ye çekme + `numBits` limiti.
- **Orta – Doğrulama boşlukları:**
  - `lensFocalLength_m`, `lensAperture_m` (`app.py:67-68`) sınırsız, etkisiz (F7).
  - `environmentPreset` verildiğinde `thermalProfile/weather/Cn2Level/baseTemp/deltaT/windSpeed/humidity` sessizce eziliyor (`app.py:182-190`); kullanıcı çelişkili değer gönderdiğinde uyarı yok (ölçüldü: preset=storm + profile=uniform → 200, weather=storm).
  - `wavelengthNm` `SimParams`'ta `int` (`app.py:59`), `TransmissionTestParams`'ta `float` (`app.py:87`) → 1550.5 bir yerde reddedilir.
  - `ascii_hello` + `numBits` çelişkisi (F8).
  - `SimParams` tohum (seed) açığa çıkarmıyor → API sonuçları `random` desende tekrarlanamaz.
- **Orta – Hata işleme:** Genel `except Exception` → 500 (iyi: ayrıntı sızdırmıyor, `test_unhandled_simulation_errors_do_not_leak_details`). Ancak `/api/test` (`app.py:334-520`) her alt testte `str(e)` döndürüyor (iç hata mesajı sızıntısı, Düşük) ve üretim endpoint'inde "test" koşturuyor.
- **Düşük – `/api/test` içeriği:** T10 "Edlén" adıyla `refractiveIndexSimple` test ediyor (`app.py:450`); T11 yalnızca `σ²>0`; T13 `gain_cas > gain_none` (tablo sabitine bağlı) → anlamlı doğrulama değil; bu yüzden F1/F2 hataları "allPassed" ile maskeleniyor.
- **Düşük – Kod:** kullanılmayan import'lar `StaticFiles`, `List` (`app.py:3, 7`); `import numpy` fonksiyon içinde (`app.py:103`); statik yollar için 5 ayrı tekrarlı handler (`244-266`).

---

## Test Kapsamı

Mevcut: Snell (4 test), Beer-Lambert (yalnızca "clear"), geometrik kayıp + pointing, Edlén tek yönlü monotonluk, Rytov "sonlu ve pozitif", preset'ler, Cn2 yol ortalaması, nem aralığı, paket gönderme (0/1 bitleri), lens "yönsel" testleri, CRC döngüsü, 1 regresyon fixture (800 m gradient), 5 API testi, 1 COMSOL CSV testi.

**Test edilmeyen / zayıf test edilen:**
- **Orta–Yüksek:** `AtmosphericLoss.absorptionLoss` için haze/fog/rain/snow/storm ve dalga boyu ölçeklemesi (F1 bu yüzden yakalanmıyor); `refractiveIndexEdlen` mutlak değeri/nem terimi (F2 yakalanmıyor; testte yalnız "T arttıkça n azalır"); `Cn2HufnagelValley`, `pressureAtAltitude`, `refractiveIndexSimple`.
- **Orta:** `RayTracer`: TIR dalı, origin-out-of-bounds, y/z yönlü ışınlar, `findNextBoundary`, maxSteps; yalnız eksenel ışın + tek fixture (800 m) var. Termal profillerin (`turbulent/hotspot/layered/coastal`) nicel doğruluğu (yalnız "preset grid oluşur").
- **Orta:** `linkBudget` scintPenalty dalları (σ²<5 / ≥5 süreksizliği), `verifySnellLaw`.
- **Orta:** `generateTestData` desenleri (`prbs7`, `burst`, `ascii_hello`, `random` tohumsuzluğu); `ComprehensiveTransmissionTest` sonuç değerleri (yalnız alan/boyut ve sıralama).
- **Orta:** API: `/api/presets`, `/api/test`, 4/5 `/api/export/*` uç noktası (yalnız `csv-grid` test var), `/api/test-transmission`, statik rotalar, CORS açıkken davranış, `environmentPreset` ezme.
- **Yüksek:** JS tarafı (`engine.js`, `app.js`, `renderer.js`, `comsol.js`) — sıfır test; CI yalnızca sözdizimi (`node --check`). Python↔JS parite testi yok (F: Tutarlılık).
- `validate_crc` hiç test edilmiyor; `crc16_ccitt` yalnızca round-trip.
- Fixture regresyonu yalnızca "gradient" profili ve kısa (800 m) mesafede; fizik hataları fixture'a gömülürse "regresyon korumalı" görünür (örn. F2 hatalı n değerleri fixture'da kilitli).

---

## Kod Kalitesi

- **Ölü kod:** `engine.py:397-410` haze/fog sabitleri (F1); `engine.py:465` `math.PI` hack'i; `engine.py:94-103` `ATM_CONSTANTS["alpha_*"]` kullanılmıyor (JS aynı); `DataTransmissionSimulator.beamDivergence`; `LensSystem.focal_length_m/aperture_m`; `app.py` kullanılmayan import'lar; `refractiveIndexSimple` yalnız testte/`/api/test` içinde.
- **Tekrar:** Python/JS çift implementasyon (`engine.py` ≈ `engine.js`) → paritenin elle korunması gerekiyor; `app.js:804+` ile `app.py:/api/test` aynı birim kontrolleri; `getRefractiveIndex/getTemperature/getCn2` üç kez aynı clamp (`engine.py:320-336`); statik rota handler'ları; `ComprehensiveTransmissionTest` ile `run_simulation_backend` link bütçesi kurulumu (`engine.py:1119-1133` vs `app.py:221-239`) kopya; `app.py:304-316` serileştirme.
- **Sihirli sayılar:** `0.004` (ışın çapı) 6+ yerde sabit (`app.py:212, 228, 239`, `engine.py:1111, 1126, 1149`, `app.js:218, 264`); `1e-15` varsayılan Cn² (`app.py:229`, `engine.py:740, 1127`); `5.0*log10(1+σ²)` ve `10.0` / `5.0` eşiği (`engine.py:511`); `0.3/1.0` rejim eşikleri; `3.91`, `0.585`, `550`, V=0.5/4.0/0.1; `rain=12`, `snow=20`; `eps=1e-10`; `maxSteps=50000`; LCG sabitleri.
- **Hijyen:** `backend/__pycache__/comsol.cpython-313.pyc` ve `backend/tests/__pycache__/*.pyc` git'te **izleniyor** (`.gitignore` yok sayıyor ama eskiden eklenmiş; `git rm --cached` gerekir); `backend/tests/` yetim dizin. `datetime.utcnow()` (`comsol.py:11, 37`) Python 3.12+'da deprecated. `reports/test-results.xml` .gitignore satırı dosya sonu satırsız. Python 3.9'u CI matrisinde ama `Literal`/`|` kullanımı uyumlu, sorun yok.
- **İsimlendirme:** Python tarafında JS camelCase (`applyThermalProfile`, `worldToGrid`) ve snake_case (`apply_to_beam`) karışık; PEP8 dışı.
- **Performans:** Saf Python üç iç içe döngü (`engine.py:137-148, 256-318`, `comsol.py:23-34`); `__init__` ızgarayı iki kez dolduruyor.

---

## Öncelikli Öneriler (sıralı)

1. **Kritik – Kim/Kruse birim ve model düzeltmesi:** `engine.py:412-423` ve `engine.js:477-489`: `alpha_dB_km = 4.343 · (3.91/V) · (λ/550)^-q`; Kim q tablosu (V>50: 1.6, 6-50: 1.3, 1-6: 0.16V+0.34, 0.5-1: V−0.5, <0.5: 0); V'yi API parametresi yap; `engine.py:397-410` ölü atamaları ve `ATM_CONSTANTS["alpha_*"]` temizle; storm yorumunu/değerini hizala. Test ekle (fog V=0.5 → ≈34 dB/km).
2. **Kritik – Edlén nem katsayısı:** `engine.py:177-179` / `engine.js:163`: Pa için `≈ −4.3e-10·e` (veya Ciddor/Edlén tam formülü); regresyon fixture'ını (`tests/fixtures/gradient_trace.json`) yeniden üret; nemin mutlak etkisini test et (15 °C, %100 RH → ≈ −7e-7).
3. **Yüksek – `/api/send-data` yanıt boyutu:** `app.py:303-316` bit başına trace'i kaldır (tek `trace` dön); `numBits` limitini (10000) ve toplam yanıt boyutunu sınırla. Ölçüm: 1000 bit = 289 MB / 70 sn.
4. **Yüksek – CPU DoS sınırları:** `app.py:18-26` hücre limitini düşür (örn. 100-200k) ve `/api/test-transmission` için `7 × hücre` sınırı koy; `applyThermalProfile`'i NumPy ile vektörize et (`engine.py:256-318`); `initializeDefault` çift doldurmayı kaldır (`engine.py:124`); sunucu zaman aşımı/rate-limit.
5. **Yüksek – Işın izleyiciyi gerçek kırılma verecek hale getir:** `engine.py:531-668`: sürekli n(x,y,z) alanı (trilineer enterpolasyon) + `d(n·t)/ds = ∇n` denklemini RK4/RK45 ile (bkz. `backend/numerics.py` ray equation) çöz; ya da ızgara DDA'yı yalnız yeni model ile çapraz doğrula. Kaynak/hedef konumunu hücre sınırından kaçır; eksen-dışı test senaryoları ekle.
6. **Yüksek – Lens modelini optik temelli yap:** `engine.py:854-953` → ABCD + Gauss ışını (w0, zR, M², difraksiyon sınırı θ ≥ λ/(πw0)); Tx ve Rx optiğini ayır; Fresnel/obskürasyon; `backend/optics.py` hazır olunca `engine.py`/`app.py`'yi ona bağla ve `lensFocalLength_m/lensAperture_m` alanlarını gerçekten kullan ya da kaldır.
7. **Yüksek – Veri iletimi/BER gerçekçiliği:** `engine.py:744-810`: SNR/Q → BER = ½erfc(Q/√2), olasılıksal bit hataları (tohumlu RNG), sintilasyon fading örneklemesi; `received` için "sinyal yok" ayrımı; `signalPower` birimini tek tipe getir (`engine.py:770`); `random`/`ascii_hello` için seed/length düzeltmesi (`engine.py:824-832`).
8. **Orta – Geometrik kayıp & sintilasyon:** `engine.py:447-466` Gauss ışını için `1−exp(−2a²/w²)` kullan (3 dB tutarsızlık, F6); `engine.py:511` süreksiz `scintPenalty`yi log-normal/gamma-gamma fading marjinine çevir; Rytov ağırlıklı integral ve doyma; "Cn2Level" etiketlerini link irtifasında ayırt edilebilir kıl (F5).
9. **Orta – Python/JS parite:** Hash'i `Math.imul` ile int32'ye sar (`engine.js:218-222`) veya iki tarafta da farklı bir tamsayı hash'i kullan; `engine.py:213` yorumunu düzelt; Node tabanlı parite testi (bu raporda kullanılan karşılaştırma betiğine benzer) ekle ve CI'a (`tests.yml`) ekle; mümkünse tek kaynaktan türet (WASM/ortak JSON test vektörleri).
10. **Orta – Test boşlukları:** F1/F2 için nicel testler; `RayTracer` TIR / y-z yönü / out-of-bounds; tüm `/api/*` uç noktaları; `generateTestData` desenleri; `/api/test` kendi kendini onaylayan kontrollerini gerçek pytest'e taşı ve üretim API'sinden çıkar (`app.py:334-520`); JS için en az `node:test` ile birim testleri.
11. **Düşük – API doğrulama ve hijyen:** `wavelengthNm` tipini birleştir (`app.py:59` vs `87`); `environmentPreset` ezmesini yanıtta açıkça belirt; kullanılmayan import'lar; `utcnow()` → `datetime.now(datetime.UTC)`; izlenen `.pyc` dosyalarını `git rm --cached`; sihirli sayıları (`0.004`, `1e-15`, V değerleri) adlandırılmış sabitlere taşı.
