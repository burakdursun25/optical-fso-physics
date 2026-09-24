# Optical FSO Physics

Free-space optical (FSO) haberleşme bağlantılarını fiziksel bir model üzerinden inceleyen bir simülasyon projesidir. Proje; atmosferik zayıflama, ışın izleme, kırılma, lens sistemi, pointing loss ve veri iletimi gibi parçaları tek bir çalışma akışında bir araya getirir.

## Neler var?

- Atmosferik kayıplar için Beer-Lambert tabanlı hesaplama
- 3B vektörler, ışın izleme ve Snell kırılma yasası
- Lens ve ortam preset'leri
- Veri iletim simülasyonu ve pointing loss
- Paket bütünlüğü için CRC-16/CCITT-FALSE hesaplama ve doğrulama
- Fizik ve veri doğrulama testleri
- COMSOL entegrasyonu için yardımcı modüller
- Tarayıcı tabanlı arayüz için JavaScript/CSS dosyaları

## CRC doğrulaması

`backend/engine.py` içindeki `crc16_ccitt` fonksiyonu, UTF-8 metin veya byte tabanlı payload üzerinden CRC-16/CCITT-FALSE checksum üretir. `validate_crc` ise alınan checksum'ın payload ile eşleşip eşleşmediğini kontrol eder.

Bu yaklaşım, optik bağlantı üzerinden taşınan bir veri paketinin fiziksel iletim hesabından sonra bozulup bozulmadığını kontrol etmeye yarar. Uygulamadaki sonuç `binascii.crc_hqx(payload, 0xFFFF)` ile uyumlu olacak şekilde test edilir.

## Kurulum

Python bağımlılıklarını sanal ortamda kurun:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Testler

Proje kök dizininden:

```bash
python -m pytest -q
```

Testler görsel arayüze ihtiyaç duymaz ve `tests/` altında merkezi olarak gruplanır:

- `tests/unit/math/`: vektör ve Snell yasası matematiği
- `tests/unit/physics/`: atmosfer, kayıp, lens, CRC ve veri iletimi
- `tests/regression/`: sabit girdili deterministik ray-trace fixture kontrolleri
- `tests/fixtures/`: regresyon için beklenen JSON çıktıları

Makine tarafından okunabilir JUnit XML ve okunabilir Markdown raporu üretmek için:

```bash
python scripts/run_test_report.py
```

Çıktılar `reports/test-results.xml` ve `reports/test_report.md` dosyalarına yazılır.

## Rapor

CRC doğrulama sonuçları ve proje değerlendirmesi `reports/optical_fso_crc_report.md` dosyasında, PDF çıktısı ise aynı klasörde bulunur.

## Dizin yapısı

- `backend/`: Python tabanlı fizik ve iletim motoru
- `backend/tests/`: otomatik testler
- `reports/`: doğrulama raporları
- `app.js`, `renderer.js`, `index.html`, `style.css`: kullanıcı arayüzü
- `comsol.js`, `backend/comsol.py`: COMSOL ile ilgili yardımcı katmanlar
