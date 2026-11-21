# Arayüz v2 kullanım notları

Başlatma: `python3 run.py` (veya `.venv/bin/python -m uvicorn backend.app:app --port 8000`), sonra tarayıcıda `/`.
three.js (r128) CDN'den yüklenir; yüklenemezse 3B alan bir uyarı gösterir, diğer paneller çalışmaya devam eder.
Tüm yeni paneller Python backend gerektirir (JS fallback modunda lens/3B/duplex çalışmaz, mesaj verilir).

## Lens Tasarımcısı (sol kenar çubuğu, "Lens Sistemi" altında)
- Terminal A / B seçilir; "A'nın tasarımını B'ye kopyala" ile aynı lens iki uçta kullanılır.
- Dalga boyu (850/1310/1550), lens şekli (yalnızca tasarlanabilir olanlar), malzeme, filtre, kaplama seçilir.
  Seçili dalga boyunda opak olan malzemeler listede "— opak" diye işaretlenir ve seçilemez.
  Malzeme kutusunun altında not, maliyet, n(λ), Abbe sayısı ve geçirgenlik bandı görünür.
- Kaydırıcılar: odak uzaklığı 10–1000 mm, çap 5–300 mm, merkez kalınlığı (otomatik ya da elle).
  Değişiklikler 250 ms bekletilip `/api/lens/design`'e gönderilir.
- Okumalar: n(λ), EFL (ABCD), R1, R2, f/#, NA, t_c/t_e, BFD, lens/filtre/toplam iletim, küresel sapma, Airy yarıçapı,
  çıplak alıcıya göre bağlantı kazancı (dB). Küçük kesit çizimi gerçek Snell kırılmasıyla ışın demetini ve odak noktasını gösterir.
- Eski "Lens Tipi" listesi aynen çalışır (JS motoru), yeni tasarımcıdan bağımsızdır.

## 3B Yayılım sekmesi
- "3B Yayılımı Hesapla": ızgara/atmosfer sol paneldeki ayarlardan alınır (ızgara 40 000 hücreyi aşarsa Y/Z küçültülür).
- Sahne: zemin ızgarası, atmosfer kutusu, Terminal A/B, ışın tüpü + merkez hattı, bozulma bulutu, hotspot halkası ve etiketi.
  Fare ile döndür/yakınlaştır/kaydır.
- Bozulma bulutu: renk mavi → sarı → kırmızı, opaklık skorla orantılı. "Eşik" kaydırıcısı düşük skorlu vokselleri gizler;
  "Bozulma bulutu" kutusu tümünü açıp kapatır.
- "Dikey ölçek abartma": yalnızca ışın tüpü ve merkez hattının doğrultudan sapması büyütülür (log ölçek, otomatik mod var).
  Bulut, kutu ve terminaller gerçek ölçektedir (1 birim = 100 m).
- Lens kesiti: iki köşedeki küçük çerçeveler A ve B lens profilini ve ışınları gösterir. "Lens yatay kesit düzlemi"
  açılınca 3B lensler (LatheGeometry) yatay düzlemde kesilir ve profil konturu, ışın demeti ile odak noktası düzlemde çizilir
  (lens boyutu sahnede gösterim için ölçeklenir).
- Anomali düzenleyici: sis yaması, türbülans cebi, sıcak/soğuk kabarcık; konum (%), uzunluk, genişlik, şiddet. En çok 8.
  Eklemeden sonra yayılımı yeniden hesaplayın; hotspot ve bulut değişir.

## Full-Duplex İletişim sekmesi
- Terminal A ve B: güç, dalga boyu, filtre izolasyonu, gönderilecek metin. Alıcı yarıçapı, o terminalin tasarlanan lensinin çapından alınır.
- "3B yayılımı kullan" açıkken kanal 3B sonuçtan (ve anomalilerden) türetilir ve uzamsal bölgeler hesaplanır.
  "Lens/filtre iletimini ekle" iki terminalin toplam iletiminin ortalamasını sabit kayba ekler (yaklaşık).
- Çıktı: A→B ve B→A kartları (P_rx, BER, throughput, goodput, verimlilik, tekrar, teslim edilen metin ve CRC rozeti),
  animasyonlu slot çizgisi (DATA / RETX / yalnız ACK, piggyback ACK ✓ / NACK ✗, kırmızı çerçeve = CRC hatası),
  segment tablosu (deneme, tekrar, CRC geçmişi ✓/✗), "bölge başına tekrar" ve uzamsal bölge grafikleri.
- Seçili yönün uzamsal bölgeleri 3B sekmede en çok tekrar kırmızı olacak şekilde renkli bantlar olarak çizilir
  (B→A bölgeleri A'dan ölçülen konuma çevrilir).

## ARQ Demo sekmesi
- Tek yönlü: segment boyutu, maksimum tekrar, ACK BER, ileri BER (boşsa kanal modeli), mesafe, güç, dalga boyu.
- Aynı tablo ve grafikler; "3B yayılım kullan" ile uzamsal bölgeler de çıkar.

## Sınırlamalar
- Uzamsal bölge atama heuristiktir (CRC hatasının yeri bilinemez; hata, yerel bozulma skoruna orantılı dağıtılır).
- Lens/filtre, duplex'te alıcı çapı ve ortalama sabit kayıp olarak uygulanır; ışın hattına (verici optiği) girmez.
- Fresnel lensin ışın demeti eşdeğer düz-dışbükey yüzeyle çizilir. Doublet/GRIN tasarlanamaz.
