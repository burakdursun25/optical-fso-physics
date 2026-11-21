# Optical FSO Physics Project – Teknik Rapor

## 1. Giriş

Bu çalışma, serbest uzay optik (FSO) haberleşme bağlantılarının fiziksel kanal davranışını modelleyen ve simüle eden bir yazılım altyapısı üzerine odaklanmaktadır. Proje, optik sinyalin atmosfer tarafından nasıl zayıfladığını, lens sistemleriyle nasıl yönlendirildiğini, kırılma olaylarını nasıl izlediğini ve veri iletiminde paket bütünlüğünü nasıl koruduğunu tek bir akış içinde göstermektedir.

Amaç, yalnızca teorik bir anlatım sunmak değil; gerçekçi bir fizik tabanlı simülasyon aracı üretmek ve bu modeli API, testler ve kullanıcı arayüzü katmanlarıyla birlikte çalışır hale getirmektir.

## 2. Proje Hedefi ve Kapsamı

Proje kapsamında aşağıdaki ana unsurlar incelenmiştir:

- Atmosferik kayıpların modellenmesi
- Işın yolu takibi ve Snell kırılma yasası uygulaması
- Lens ve ortam parametrelerinin benzetimi
- Pointing loss ve verici-alıcı hizalama etkileri
- Veri paketleri üzerinde CRC-16/CCITT-FALSE doğrulama
- Tarayıcı arayüzünden çalıştırılabilen web tabanlı kullanım
- Test ve regresyon doğrulama altyapısı

Bu yaklaşım, FSO sistemlerinin fiziksel davranışını yalnızca optik prensiplere göre değil, aynı zamanda iletim güvenilirliği ve veri bütünlüğü açısından da analiz etmeyi mümkün kılmaktadır.

## 3. Sistem Mimarisi

Proje, modüler bir yapı üzerine inşa edilmiştir:

- `backend/engine.py`: fizik modeli, atmosfer kontrolü, ray trace, kırılma muhasebesi ve CRC yardımcı fonksiyonları
- `backend/app.py`: FastAPI tabanlı sunucu, dizi sınır kontrolü, Pydantic parametre modelleri ve HTTP API uç noktaları
- `backend/comsol.py`: COMSOL entegrasyonu için yardımcı katman ve veri hazırlama
- `app.js`, `renderer.js`, `index.html`, `style.css`: kullanıcı arayüzü ve görsel çıktı katmanı
- `tests/`: matematik, fizik, API ve regresyon testlerinin merkezi yerleşimi
- `reports/`: test raporları ve çıktı dosyaları

Bu yapı, fiziksel hesaplamaların arka planda yürütülmesini sağlarırken aynı zamanda kullanıcı deneyimini sade ve erişilebilir tutar.

## 4. Fiziksel Modelleme Temeli

### 4.1 Atmosferik zayıflama

Projede atmosferik zayıflama, Beer-Lambert yasası temelinde hesaplanır. Bu yaklaşım, optik ışının ortam içinde kaybolma oranını modellemeyi sağlar.

```text
I = I0 * exp(-alpha * L)
```

Burada:

- `I0`: giriş gücü
- `I`: iletim sonrası güç
- `alpha`: atmosferik kayıp katsayısı
- `L`: iletim mesafesi

Farklı hava koşulları için farklı kayıp katsayıları kullanılmaktadır. Bu, netlik, sis, yağmur ve yüksek turbulans gibi farklı atmosfer senaryolarının simülasyonunu mümkün kılar.

### 4.2 Kırılma indisi ve Snell yasası

Işığın ortamlar arası geçişinde yön değiştirme davranışı Snell yasası ile modellenir:

```text
n1 * sin(theta1) = n2 * sin(theta2)
```

Projede bu hesaplama, 3B vektör çerçevesinde yürütülür ve optik ışınların adım adım yolunu izlemek için kullanılmaktadır. Bu sayede her hücrede oluşan refraktif indeks değişimi, ışının yönünde ve konumunda birikimli etkiye dönüşür.

### 4.3 Atmosfer gridi ve türbülans

`AtmosphereGrid` sınıfı, uzayda ayrılmış hücrelerden oluşan bir grid yapısı kurar. Her hücre için sıcaklık, basınç, humidity ve `Cn2` değeri tanımlanır. Bu değerler, ortamın optik değişkenliğini temsil eder. `Cn2` değerleri, zayıf, orta ve güçlü türbülans koşullarını yansıtarak kanalın bozulma seviyesini belirlemeye yardımcı olur.

Proje ayrıca Hufnagel-Valley yaklaşımıyla `Cn2` dağılımını türetmekte ve buna göre kanal kırılma/dağılma etkisini değerlendirmektedir.

### 4.4 Lens ve odaklama etkisi

Lens sistemi, optik huzmenin genliğini ve yönünü değiştiren bir başka kritik bileşendir. Projeyi oluşturan model, kolimasyon, odaklama ve diverging/galilean/cassegrain benzeri lens türlerine göre benzetim yapabilir. Bu türler, FSO kanalında kullanılacak optik başlık tasarımı açısından önemli bir karar noktasıdır.

## 5. Veri iletimi ve veri bütünlüğü

Proje yalnızca optik kaybı değil, aynı zamanda paket iletişimini de ele alır. Işının fiziksel yolunu geçtikten sonra, simülasyon taşıma başarısı ve veri bütünlüğü ile ilgili sonuç üretir.

Bu bağlamda kullanılan temel mekanizma CRC kontrolüdür. `backend/engine.py` içindeki `crc16_ccitt` fonksiyonu, payload üzerinde CRC-16/CCITT-FALSE checksum hesaplar. Daha sonra `validate_crc` fonksiyonu, iletilen veya doğrulanan değerin gerçek payload ile eşleşip eşleşmediğini kontrol eder.

```python
crc = crc16_ccitt(payload, 0xFFFF)
valid = validate_crc(payload, checksum, 0xFFFF)
```

Bu yaklaşım, taşıma sırasında paketlerde meydana gelebilecek bozulmaları fiziksel simülasyon neticesinde tespit etmek için kritik bir doğrulama katmanı sağlar.

## 6. Kullanım Akışı

1. Kullanıcı, parametreleri belirleyerek simülasyon isteği oluşturur.
2. Arka uç, `AtmosphereGrid` ve `RayTracer` gibi yapıları kurar.
3. Atmosfer profili, sıcaklık, nem, basınç ve `Cn2` parametreleri uygulanır.
4. Işın, ağ boyunca ilerler; kırılma, yön değişimi ve kayıp hesaplanır.
5. Lens, pointing loss ve beam divergence etkileri eklenir.
6. Veri iletimi gerçekleştirilir ve CRC doğrulaması yapılır.
7. Sonuçlar JSON formatında geri döner; arayüzde görselleştirilir.

Görüldüğü üzere, sistem bir simülasyon motoru olmanın ötesinde, uygulama katmanlarını birleştiren bir FSO mühendislik platformu olarak çalışmaktadır.

## 7. Test ve Doğrulama

Projede otomatik testler; matematiksel doğruluk, fiziksel uyumluluk ve API davranışlarını kapsayacak şekilde düzenlenmiştir. Testler, temel olarak aşağıdaki alanları kapsamaktadır:

- Vektör operasyonları
- Snell yasası doğrulaması
- Atmosferik kayıp hesapları
- Lens davranışı ve ortam düzeni
- CRC kodu ve iletişim doğrulaması
- COMSOL/JSON export doğrulaması
- Regresyon tabanlı deterministik ışın izi kontrolleri

Çalışmanın güncel doğrulama sonucu aşağıdaki gibidir:

- Toplam test sayısı: 39
- Başarılı test: 39
- Başarısız test: 0
- Hata: 0
- Geçersiz/atlanmış: 0

Bu durum, mevcut fizik modeli ve doğrulama katmanının tutarlı bir seviyede çalıştığını göstermektedir.

## 8. Sonuç ve Değerlendirme

Bu proje, serbest uzay optik haberleşme alanında fiziksel kanalı modellemek için güçlü ve kullanışlı bir altyapı sunmaktadır. Özellikle atmosfer değişkenleri, kırılma davranışı, optik bileşen etkileri ve veri bütünlüğü kontrolü gibi konuların tek bir senaryoda bir araya getirilmesi, çalışmayı akademik deney, simülasyon prototipi ve mühendislik değerlendirmesi için değerli kılmaktadır.

Projenin güçlü yönleri şunlardır:

- Fizik ve veri iletişimi arasında sağlam bir bağ kurması
- Modüler ve geliştirilebilir mimarisi
- Test odaklı doğrulama yaklaşımı
- Web arayüzüyle erişilebilir olması
- COMSOL ve otomasyon desteğine açık olması

Gelecekteki geliştirmeler için öneriler şu şekilde özetlenebilir:

- Daha geniş atmosferik senaryo ve hava koşulu profili
- Gelişmiş hata oranı modellemesi
- Daha yüksek doğruluklu dalga boyu ve cihaz parametreleri
- Gerçek zamanlı görselleştirme ve grafik çıktıları
- Mühendislik raporları için daha kapsamlı PDF ve analitik çıktılar

## 9. Kısa Değerlendirme

Özetle, Optical FSO Physics projesi; teori, simülasyon ve uygulama katmanlarını birleştiren, FSO iletişim sistemlerinin fiziksel davranışını analiz etmeye yönelik tamamlanmış bir prototip niteliğindedir. Kullanılan yöntemler, hem bilimsel değerlendirme hem de mühendislik tabanlı karar alma için uygun bir zemin oluşturmaktadır.

Bu rapor, proje kapsamının ve güncel doğrulama sonuçlarının açık ve anlaşılır bir şekilde özetlenmesini hedeflemektedir.

