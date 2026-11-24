# Optical FSO Physics

Free-space optical (FSO) haberleşme bağlantılarını fiziksel bir model üzerinden inceleyen bir simülasyon projesidir. Proje; atmosferik zayıflama, ışın izleme, kırılma, lens sistemi, pointing loss ve veri iletimi gibi parçaları tek bir çalışma akışında bir araya getirir.

## Neler var?

- Atmosferik kayıplar için Beer-Lambert tabanlı hesaplama
- 3B vektörler, ışın izleme ve Snell kırılma yasası
- Lens ve ortam preset'leri
- Veri iletim simülasyonu ve pointing loss
- Paket bütünlüğü için CRC-32 (IEEE 802.3) hesaplama ve doğrulama
- Fizik ve veri doğrulama testleri
- COMSOL entegrasyonu için yardımcı modüller
- Tarayıcı tabanlı arayüz için JavaScript/CSS dosyaları

## CRC doğrulaması

`backend/engine.py` içindeki `crc32` fonksiyonu, UTF-8 metin veya byte tabanlı payload üzerinden CRC-32 (IEEE 802.3 / ISO-HDLC: polinom 0x04C11DB7, yansıtılmış 0xEDB88320, başlangıç 0xFFFFFFFF, giriş/çıkış yansıtmalı, son XOR 0xFFFFFFFF) checksum üretir; tablo tabanlı, açık bir uygulamadır. `validate_crc32` alınan checksum'ın payload ile eşleşip eşleşmediğini kontrol eder. Eski `crc16_ccitt` / `validate_crc` yalnızca geriye dönük uyumluluk için durur; proje artık hiçbir yerde CRC-16 kullanmaz.

Bu yaklaşım, optik bağlantı üzerinden taşınan bir veri paketinin fiziksel iletim hesabından sonra bozulup bozulmadığını kontrol etmeye yarar. Uygulamadaki sonuç `zlib.crc32(payload)` / `binascii.crc32(payload)` ile birebir aynı olacak şekilde test edilir (`"123456789"` için kontrol değeri `0xCBF43926`). CRC alanları arayüzde 8 onaltılık basamakla (ör. `CBF43926`) gösterilir.

## Kurulum

Python bağımlılıklarını sanal ortamda kurun:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Uygulamayı API ve web arayüzüyle başlatın:

```bash
uvicorn backend.app:app --reload
```

Arayüz `http://127.0.0.1:8000` adresinde açılır. Varsayılan yapılandırma aynı-origin isteklerle çalışır ve CORS'u açmaz. Arayüzü farklı bir origin'de barındırıyorsanız API başlamadan önce izin verilen tam origin'leri virgülle ayarlayın:

```bash
export FSO_CORS_ORIGINS="https://fso.example.com"
```

## Testler

Proje kök dizininden:

```bash
python -m pytest -q
```

Testler görsel arayüze ihtiyaç duymaz ve `tests/` altında merkezi olarak gruplanır:

- `tests/unit/math/`: vektör ve Snell yasası matematiği
- `tests/unit/physics/`: atmosfer, kayıp, lens, CRC ve veri iletimi
- `tests/unit/`: API parametre sınırları ve COMSOL export kontrolleri
- `tests/regression/`: sabit girdili deterministik ray-trace fixture kontrolleri
- `tests/fixtures/`: regresyon için beklenen JSON çıktıları

Makine tarafından okunabilir JUnit XML ve okunabilir Markdown raporu üretmek için:

```bash
python scripts/run_test_report.py
```

Çıktılar `reports/test-results.xml` ve `reports/test_report.md` dosyalarına yazılır.

## Bit kontrolü, optik ve sayısal modüller

- `backend/bitcheck.py`: bit düzeyinde giriş/çıkış doğrulaması. Çerçeve: preamble (0xAA 0xAA) + sync (0x2DD4) + 16 bit uzunluk + yük + CRC-32. `compare_bits` BER, 0→1 / 1→0 çevrilmeleri, en uzun hata patlaması ve bayt başına hata haritası verir. Kanal, link bütçesinden (OOK: `BER = 0.5*erfc(sqrt(SNR)/(2*sqrt2))`) türetilen BER ile tohumlu (deterministik) bit çevirir. Kopan kanalda tüm bitler "kayıp" sayılır (sessiz sıfır olarak doğru sayılmaz).
- `backend/optics.py`: ABCD matrisleri, Gauss ışını, lens ön ayarları ve `lens_link_budget`.
- `backend/numerics.py`: integral (trapezoid, Simpson, Romberg, Gauss-Legendre), ODE (Euler, RK4, RK45), türev ve yakınsama analizi.

Yeni API uç noktaları:

- `POST /api/bitcheck`: metin yükü, mesafe, lens ön ayarı ve seed ile uçtan uca bit/CRC raporu
- `GET /api/optics/presets`: lens ön ayarları listesi
- `POST /api/optics/link-gain`: mesafe listesi için lens kazancı / link bütçesi
- `POST /api/numerics/convergence`: izin verilen yöntemlerle (trapezoid, simpson, gauss_legendre, euler, rk4) hata tablosu ve yakınsama mertebesi

## Grafikler

Grafikler için matplotlib gerekir (`pip install -r requirements-dev.txt`):

```bash
python3 scripts/generate_test_plots.py   # reports/figures/*.png dosyalarını üretir
python3 scripts/run_test_report.py       # testleri çalıştırır, grafikleri ve bit kontrol tablosunu test_report.md içine ekler
```

Üretilen şekiller: BER-mesafe, lens kazancı, ışın yarıçapı w(z), sayısal yakınsama (log-log), bit I/O zaman çizelgesi, hata konumu haritası ve test sonuç özeti. matplotlib yoksa rapor yine üretilir, sadece şekiller atlanır.

## Rapor

CRC doğrulama sonuçları ve proje değerlendirmesi `reports/optical_fso_crc_report.md` dosyasında, PDF çıktısı ise aynı klasörde bulunur.

## Dizin yapısı

- `backend/`: Python tabanlı fizik ve iletim motoru
- `tests/`: otomatik Python testleri ve regresyon fixture'ları
- `reports/`: doğrulama raporları
- `app.js`, `renderer.js`, `index.html`, `style.css`: kullanıcı arayüzü
- `comsol.js`, `backend/comsol.py`: COMSOL ile ilgili yardımcı katmanlar
