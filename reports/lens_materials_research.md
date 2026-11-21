# FSO Lens Malzemeleri, Lens Tipleri, Filtreler ve Kaplamalar — Araştırma Raporu

Kapsam: 780–1600 nm FSO (serbest uzay optik) bağlantıları için lens malzemeleri, lens geometrileri,
teleskop konfigürasyonları, filtreler ve AR kaplamalar. Veriler `backend/materials.py` içine
yerleştirilmiştir; kırılma indisleri yayımlanmış Sellmeier/tablo verilerinden hesaplanır, **hiçbir katsayı
uydurulmamıştır** (tek istisna: polikarbonat, aşağıda belirtilen Cauchy uyumu).

Not: Kırılma indisleri 20 °C, havada (n≈1) tanımlıdır. Fresnel kaybı yüzey başına ((n−1)/(n+1))².

## 1. Malzemeler

| Malzeme (anahtar) | n@850 | n@1310 | n@1550 | Abbe (Vd) | Geçirgenlik bandı (µm) | dn/dT (×10⁻⁶/K) | Yoğunluk (g/cm³) | Maliyet | FSO uygunluğu |
|---|---|---|---|---|---|---|---|---|---|
| N-BK7 (`n_bk7`) | 1.5098 | 1.5036 | 1.5007 | 64.2 | 0.35–2.0 | +0.85 (@1550, Schott formülünden) | 2.51 | düşük | Mükemmel: 850/1310/1550 için varsayılan seçim |
| UV Ergimiş silika (`fused_silica`) | 1.4525 | 1.4468 | 1.4440 | 67.8 | 0.185–2.1 | — (doğrulanmadı) | 2.20 | orta | Çok iyi: düşük genleşme (0.52×10⁻⁶/K), termal kararlı |
| CaF2 (`caf2`) | 1.4300 | 1.4272 | 1.4260 | 95.0 | 0.13–10 | −10.6 | 3.18 | yüksek | İyi ama pahalı; düşük dağılım, geniş bant |
| BaF2 (`baf2`) | 1.4698 | 1.4669 | 1.4660 | 81.6 | 0.15–12 | −15.2 | 4.89 | yüksek | Gereksiz; nem/darbe duyarlı |
| MgF2, sıradan ışın (`mgf2`) | 1.3746 | 1.3718 | 1.3705 | 106.2 | 0.12–7 | +1.7 (0.4 µm'de) | 3.18 | yüksek | Pencere/kaplama; çift kırıcı, nadiren lens |
| Safir, sıradan ışın (`sapphire`) | 1.7589 | 1.7503 | 1.7462 | 72.3 | 0.17–5.5 | +13.1 (0.546 µm'de) | 3.97 | yüksek | Dış ortam pencereleri (çizilme dayanımı) |
| N-SF11 (`n_sf11`) | 1.7619 | 1.7480 | 1.7434 | 25.7 | 0.40–2.5 | −1.8 (@1550) | 3.224 | orta | Yüksek indeks menisküs/doublet; yüksek dağılım |
| N-SF6 (`n_sf6`) | 1.7815 | 1.7673 | 1.7627 | 25.4 | 0.40–2.5 | −2.7 (@1550) | 3.369 | orta | N-SF11'e alternatif |
| S-LAH64 (`s_lah64`) | 1.7744 | 1.7641 | 1.7599 | 47.4 | 0.38–2.4 | +1.9 (@1550) | 4.30 | yüksek | Akromat çiftlerinde yüksek indeksli eleman |
| ZnSe (`znse`) | 2.5127 | 2.4650 | 2.4556 | — (görünür bölge formül aralığı dışı) | 0.6–21 | +61 (10.6 µm'de) | 5.27 | yüksek | 1550'de geçirgen, ama n=2.46 → AR şart; CO₂/MIR için |
| Germanyum (`germanium`) | OPAK | OPAK | OPAK | — | 1.8–23 | +396 | 5.33 | yüksek | **1550/850/1310 nm için KULLANILAMAZ** (kenar 1.8 µm) |
| Silisyum (`silicon`) | OPAK | 3.5003 | 3.4757 | — | 1.2–15 | +160 | 2.33 | orta | **850 nm'de opak**; 1310/1550'de geçirgen ama n=3.48 → AR şart |
| PMMA (`pmma`) | 1.4859 | 1.4820 | 1.4809 | 59.4 | 0.38–1.6 | — | 1.18 | düşük | 850 nm iyi; 1550'de ~1.2 dB/cm soğurma (kalın lenste kayıp) |
| Polikarbonat (`polycarbonate`) | 1.5756 | 1.5654 | 1.5629 | — | 0.4–1.6 | — | 1.20 | düşük | Ucuz/darbe dayanımlı; 1550'de ~0.6 dB/cm |

Notlar:

- Germanyum için Sellmeier verisi yalnızca 2.5–12 µm'de geçerlidir (Icenogle/Barnes–Piltch); 1550 nm için
  `refractive_index` bilinçli olarak `ValueError` verir. Silisyum için Li (1993) tablosu (1.2–14 µm) lineer
  interpolasyonla kullanılır; 850 nm aralık dışıdır.
- Cam dn/dT değerleri Schott/Ohara termal dispersiyon formülünden (D0, E0, λTK; 20 °C) hesaplanır
  (mutlak, havada). Kristaller için satıcı değerleri tek bir dalga boyunda verilmiştir (tablo notuna bakınız);
  1550 nm değerleri değildir.
- Soğurma (1550 nm): N-BK7 8.1×10⁻³ /cm, N-SF11 6.0×10⁻³ /cm, N-SF6 8.9×10⁻³ /cm, S-LAH64 7.0×10⁻³ /cm
  (Schott/Ohara k tablosundan α = 4πk/λ), ZnSe ≈5×10⁻³ /cm (1.3 µm), PMMA 0.275 /cm, PC 0.142 /cm
  (Zhang 2020, k'dan). `design_lens` iç soğurmayı `exp(−α·d)` ile hesaba katar.
- PMMA: Beadie vd. (2015) formülü (0.42–1.62 µm, doğruluk <2×10⁻⁴) kullanılmıştır. 1550 nm'de Beadie
  1.4809, Zhang (Mitsubishi kalitesi) 1.4771 verir; fark üretici/numune farkıdır (~4×10⁻³). Polimerlerde
  parti-parti n değişimi 10⁻³–10⁻² mertebesinde olabilir.
- Polikarbonat: yayımlanmış bir Sellmeier bulunmadığından Zhang (2020) tablo verisine 0.7–1.65 µm'de Cauchy
  tipi (n² = a₀ + a₁λ⁻² + a₂λ⁻⁴) uyum yapıldı; maksimum hata ≈1.5×10⁻³ (ölçüm saçılması dahil). Modül
  bunu "uyum" olarak etiketler.
- Abbe sayıları cam için Schott/Ohara Vd ile örtüşecek şekilde Sellmeier'den hesaplanır (N-BK7 64.17,
  N-SF11 25.68, S-LAH64 47.37; Corning silika Vd≈67.8).

### 1.1 1550 nm doğrulaması (hesaplanan vs yayımlanmış)

| Malzeme | λ (µm) | Hesaplanan n | Yayımlanmış n | Kaynak |
|---|---|---|---|---|
| N-BK7 | 1.55 | 1.50065 | 1.5007 | Schott veri sayfası |
| Ergimiş silika | 1.55 | 1.44402 | 1.4440 | Corning/Malitson |
| CaF2 | 1.65 | 1.42556 | 1.4256 | Crystran tablosu |
| BaF2 | 1.5295 | 1.46611 | 1.4661 | Crystran tablosu |
| MgF2 (o) | 1.512 | 1.3707 | 1.370 | Crystran tablosu |
| Safir (o) | 1.55 | 1.74618 | 1.7462 | Crystran tablosu |
| ZnSe | 1.40 | 2.46091 | 2.4609 | Crystran tablosu |
| Silisyum | 1.55 | 3.4757 | 3.4757 | Li tablosu |
| Germanyum | 3.0 | 4.0443 | 4.0442 | Li tablosu |
| N-SF11 / N-SF6 / S-LAH64 | 0.5876 | nd uyumlu (<5×10⁻⁴) | 1.78472 / 1.80518 / 1.788001 | Schott / Ohara |
| PMMA | 1.55 | 1.4809 | 1.4771 (Zhang) | farklı numune, bkz. not |
| Polikarbonat | 1.55 | 1.5629 | 1.5633 | Zhang 2020 |

Bu karşılaştırmalar `tests/unit/physics/test_materials.py` içinde otomatik testtir (tolerans 5×10⁻⁴;
MgF2 3 haneli kaynak olduğu için 1.5×10⁻³, polimerler daha geniş).

## 2. Lens tipleri

Yarıçap işaret konvansiyonu: ışık +z yönünde ilerler; R>0 eğrilik merkezi +z tarafında (yüzey kaynağa dışbükey);
R=∞ düz. Şekil faktörü q = (R2+R1)/(R2−R1); verilen f, n, q için
R1 = 2f(n−1)/(q+1), R2 = 2f(n−1)/(q−1) (ince lens). Kalın lensler için `design_lens` merkez kalınlığı ve
eğriliği, `optics.thick_lens_focal_length` hedef f'ye eşit olana dek yineler.

| Tip (`LENS_SHAPES` anahtarı) | q | R1, R2 | Ne zaman kullanılır |
|---|---|---|---|
| Düz-dışbükey, eğri yüz önde (`plano_convex`) | +1 | R1=f(n−1), R2=∞ | Sonsuz eşlenikte kolimasyon/odaklama (≤5:1); eğri yüz uzak nesneye bakar → min. küresel sapma |
| Düz-dışbükey, düz yüz önde | −1 | R1=∞, R2=−f(n−1) | Ters yönlendirme (daha fazla sapma); montaj kısıtı |
| Çift dışbükey (`biconvex`) | 0 | R1=−R2=2f(n−1) | 1:1'e yakın eşlenik (5:1–1:5), röle/görüntüleme |
| Düz-içbükey (`plano_concave`) | −1 | R1=∞, R2=−f(n−1)>0 (f<0) | Galileo genişletici negatif elemanı, ışın açma |
| Çift içbükey (`biconcave`) | 0 | R1=−R2=2f(n−1)<0 | Işın genişletme/odak uzatma |
| Pozitif menisküs | +2 (varsayılan) | R1,R2>0, R2>R1 | Küçük f-sayısı (<2.5), yüksek NA toplama |
| Negatif menisküs | −2 | R1,R2>0, R1>R2 (f<0) | Yüksek açılı ıraksatıcı |
| Asferik (`aspheric`) | −1 + konik | R2=−f(n−1), k=−n² | Paralel ışık için küresel sapmasız odaklama/kolimasyon, tek eleman |
| Top lens (`ball`) | — | R=2(n−1)f/n, d=2R | Fiber/küçük dedektör kuplajı |
| Fresnel (`fresnel`) | −1 eşdeğeri | düz ön yüz, testere dişli arka | Hafif/ucuz geniş açıklıklı alıcı (PMMA) |
| Silindirik (`cylindrical`) | +1 (tek eksen) | kesit PCX | Çizgi odak, ışın şekillendirme |
| Akromatik doublet | — | 3 yarıçap, 2 malzeme | f/2 altında difraksiyon sınırlı odak; çok dalga boylu link (yalnız tanımlayıcı, tasarım fonksiyonu yok) |
| GRIN | — | düz yüzler, n(r) | Fiber ucu kolimatörü (yalnız tanımlayıcı) |

Teleskop konfigürasyonları (`TELESCOPES`, `telescope_parameters`):

| Konfigürasyon | Elemanlar | Aralık / büyütme | FSO kullanımı |
|---|---|---|---|
| Kepler | + f1, + f2 | L=f1+f2, M=−f2/f1 | İç odak (uzamsal filtre konabilir); alıcı ve ışın genişletici |
| Galileo | − f1, + f2 | L=f2−\|f1\|, M=f2/\|f1\| | Kompakt, iç odak yok; verici genişletici |
| Cassegrain | içbükey birincil + dışbükey ikincil | katlanmış yol | Büyük açıklıklı (>10 cm) terminaller |
| Ritchey–Chrétien | çift hiperbolik | koma+küresel giderilmiş | Yüksek performanslı terminal (200 mm RC, 1550 nm, 2.5 Gb/s, 1.1 km) |

Sayısal kaynak notu: 5:1 / 1:5 eşlenik oranları ve f/2.5 menisküs eşiği satıcı uygulama
notlarından gelen kural-ı başparmaktır; standart değildir.

## 3. Filtreler ve kaplamalar

Filtre modelleri (`FILTERS`, `filter_transmittance`) **yaklaşıktır** (süper-Gauss bandpass, lojistik kenar
filtresi); satıcı başlık değerlerine kalibre edilmiştir, ölçülmüş eğri değildir.

| Filtre | Merkez/kenar | FWHM | Tepe T | Bloklama | Kaynak / durum |
|---|---|---|---|---|---|
| `bp_1550_10nm` | 1550 nm | 10 nm | ≥%85 (min.) | OD ≥4 | Edmund 1550 nm sert kaplamalı (doğrulandı) |
| `bp_1550_2nm` | 1550.6 nm | 2 nm | >%90 | OD ≥4 | Edmund çok kavite ultra dar (doğrulandı) |
| `bp_850_10nm` | 850 nm | 10 nm | ≥%85 (min.) | OD ≥4 | Edmund 850 nm sert kaplamalı (doğrulandı) |
| `lp_1400` | cut-on 1400 nm | — | %90 | OD 4 | Genel model (doğrulanmadı) |
| `sp_1000` | cut-off 1000 nm | — | %90 | OD 4 | Genel model (doğrulanmadı) |
| `nd_od1/2/3` | düz | — | 10^−OD | — | T=10^−OD (RP Photonics) |

Güneş arka planı: dar bandpass (FWHM 10 nm veya 2 nm) alıcı önünde bant dışı arka plan gücünü
yaklaşık FWHM/geniş-bant oranında azaltır; kalın filtrede 0° dışı geliş açısı merkez dalga boyunu kısa λ'ya
kaydırır (modelde yok). Kaynak: filtre veri sayfaları.

AR kaplamalar (`COATINGS`, `coating_reflectance`; model yaklaşıktır):

| Anahtar | Yüzey başına R | Bant | Kaynak |
|---|---|---|---|
| `none` | Fresnel (BK7'de %4.0) | — | Fresnel denklemi |
| `v_coat_1550` / `v_coat_850` | <%0.25 @ tasarım λ, parabolik artış | dar | Edmund: laser V-coat R<0.25% @ DWL |
| `mgf2_single` | Ravg ≤%1.75 | 400–700 nm | Edmund (λ/4 MgF2 @ 550 nm) |
| `bbar_nir_600_1050` | Ravg ≤%0.5 | 600–1050 nm | Edmund NIR I |
| `bbar_swir_900_1700` | Ravg ≤%1.0 | 900–1700 nm | Edmund SWIR |
| `bbar_telecom_1200_1600` | Ravg ≤%0.25 | 1200–1600 nm | Edmund Telekom-NIR (arama özeti) |

Not: Standart V-kaplama tasarım dalga boyları arasında 1550 nm listelenmemiştir (266…1064 nm); 1550 V-kaplama
özel üretimdir. Bant içi Ravg ≤%0.25–1.0 değerleri tipik satıcı spesifikasyonudur.

## 4. 1550 nm FSO için öneriler

1. **Varsayılan lens: N-BK7, düz-dışbükey (eğri yüz kaynağa dönük)**, 1050–1700 nm geniş bant AR veya 1550 V-kaplama. En düşük maliyet, n=1.5007, soğurma ~0.008 /cm → 3 mm kalınlıkta <%0.3.
2. **Termal kararlılık / yüksek güç:** UV ergimiş silika (CTE 0.52×10⁻⁶/K); OH soğurma bandı (~1380 nm) FSO 1550'yi etkilemez.
3. **Kısa odak/yüksek NA:** N-SF11 veya S-LAH64 ile menisküs ya da asferik; n yüksek olduğundan eğri yüzeyler yumuşar ama Fresnel kaybı artar (AR zorunlu).
4. **Geniş açıklıklı ucuz alıcı:** PMMA Fresnel/asferik — 850 nm'de uygundur; 1550'de ~1.2 dB/cm soğurma nedeniyle ince (≤2 mm) tutun.
5. **Kaçının:** Ge (1.8 µm altında opak), Si (850 nm opak; 1550'de n=3.48 → %30 Fresnel kaybı/yüz, AR şart), BaF2/ZnSe (maliyet, gereksiz).
6. **Arka plan bastırma:** alıcıda 1550 nm / 10 nm FWHM bandpass (tepe ≥%85) + AR kaplı lens; toplam optik verim ≈ T_lens × T_filtre ≈ 0.99 × 0.85.
7. **Verici:** Galileo (negatif + pozitif) genişletici, alıcı: Kepler veya Cassegrain/RC (büyük çap).
8. **Sıcaklık:** N-BK7 dn/dT≈+0.9×10⁻⁶/K → f değişimi ihmal edilebilir; polimerlerde dn/dT büyük (literatür ~−10⁻⁴/K, bu raporda doğrulanmadı) → odak kayması riski.

## 5. Kaynaklar

1. refractiveindex.info veri tabanı (CC0): https://github.com/polyanskiy/refractiveindex.info-database
2. Schott N-BK7 / N-SF11 / N-SF6 (Zemax kataloğu 2017-01-20b, RII üzerinden): https://refractiveindex.info/download/data/2017/schott_2017-01-20b.agf
3. Ohara S-LAH64 (Zemax kataloğu 2017-11-30): https://refractiveindex.info/download/data/2017/ohara_2017-11-30.agf
4. I. H. Malitson, JOSA 55, 1205 (1965) — ergimiş silika: https://doi.org/10.1364/JOSA.55.001205
5. I. H. Malitson, Appl. Opt. 2, 1103 (1963) — CaF2: https://doi.org/10.1364/AO.2.001103
6. I. H. Malitson, JOSA 54, 628 (1964) — BaF2: https://doi.org/10.1364/JOSA.54.000628
7. M. J. Dodge, Appl. Opt. 23, 1980 (1984) — MgF2: https://doi.org/10.1364/AO.23.001980
8. Malitson & Dodge, JOSA 62, 1405 (1972) — safir: http://www.opticsinfobase.org/josa/abstract.cfm?uri=josa-62-11-1336
9. Connolly vd., SPIE 181, 141 (1979) / Tatian, Appl. Opt. 23, 4477 (1984) — ZnSe: https://doi.org/10.1364/AO.23.004477
10. Icenogle, Platt, Wolfe, Appl. Opt. 15, 2348 (1976) / Barnes & Piltch, JOSA 69, 178 (1979) — Ge: https://doi.org/10.1364/JOSA.69.000178
11. H. H. Li, J. Phys. Chem. Ref. Data 9, 561 — Si ve Ge tabloları: https://doi.org/10.1063/1.555624
12. Beadie vd., Appl. Opt. 54, F139 (2015) — PMMA 0.4–1.6 µm: https://doi.org/10.1364/AO.54.00F139
13. Zhang vd., Appl. Opt. 59, 2337 (2020) — polimer kompleks indisleri (PMMA, PC): https://doi.org/10.1364/AO.383831
14. Crystran CaF2: https://www.crystran.com/optical-materials/calcium-fluoride-caf2
15. Crystran BaF2: https://www.crystran.com/optical-materials/barium-fluoride-baf2
16. Crystran MgF2: https://www.crystran.com/optical-materials/magnesium-fluoride-mgf2
17. Crystran safir: https://www.crystran.com/optical-materials/sapphire-al2o3
18. Crystran ZnSe: https://www.crystran.com/optical-materials/zinc-selenide-znse
19. Crystran Ge: https://www.crystran.com/optical-materials/germanium-ge
20. Crystran Si: https://www.crystran.com/optical-materials/silicon-si
21. Corning HPFS 7980 veri (yoğunluk 2.20, CTE 0.52×10⁻⁶/K, Vd 67.8): https://www.corning.com/media/worldwide/csm/documents/HPFS_Product_Brochure_All_Grades_2025_11.pdf
22. Thorlabs düz-dışbükey lens kataloğu (N-BK7 0.35–2.0 µm, silika 0.185–2.1 µm, CaF2 0.18–8 µm): https://www.thorlabs.com/catalogpages/700.pdf
23. Edmund Optics — Understanding Optical Lens Geometries: https://www.edmundoptics.com/knowledge-center/application-notes/optics/understanding-optical-lens-geometries
24. Edmund Optics — Anti-Reflection (AR) Coatings: https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/
25. Edmund Optics 1550 nm / 10 nm bandpass (OD4): https://www.edmundoptics.com/p/1550nm-cwl-50mm-dia-hard-coated-od-4-10nm-bandpass-filter/28581/
26. Edmund Optics 1550 nm ultra dar bant: https://www.edmundoptics.com/p/1550nm-cwl-25mm-dia-od-4-ultra-narrow-filter/41345/
27. Edmund Optics 850 nm / 10 nm bandpass: https://www.edmundoptics.com/p/850nm-cwl-25mm-dia-hard-coated-od-4-10nm-bandpass-filter-/28577/
28. RP Photonics — Optical density: https://www.rp-photonics.com/optical_density.html
29. Optics For Hire — FSO tasarım değerlendirmeleri: https://www.opticsforhire.com/blog/design-considerations-for-free-space-optics/
30. SPIE 5550 — 2.5 Gb/s FSO bağlantısı, 1.1 km, 200 mm Ritchey–Chrétien: https://spiedigitallibrary.org/conference-proceedings-of-spie/5550/0000/25-Gb-s-free-space-optics-link-over-11-km/10.1117/12.554886.short

Doğrulanamayanlar: ergimiş silika, PMMA ve PC için dn/dT; longpass/shortpass filtrelerinin belirli bir veri
sayfası; ZnSe'nin görünür-bölge Abbe sayısı (formül 0.54 µm'den başlar).
