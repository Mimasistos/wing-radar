# A2 Radar

mimasdev tarafından kanatlı bir oyunun ikincisi için geliştirilmiş; Hidden Cube için pasif ağ dinleyen yön radarı, arama rotası planlayıcı ve yarı saydam yol haritası paneli.
Oyun belleğini okumaz, DLL enjekte etmez, oyun dosyalarını değiştirmez; yalnızca yerel TCP trafiğini dinler.

> **Sorumluluk reddi.** Resmî olmayan bir topluluk aracıdır; Oyunun yayıncısıyla bağlantısı yoktur. Oyun ve ilgili görseller
> sahiplerine aittir. Aracı kullanıp kullanamayacağın oyunun güncel kurallarına bağlıdır, bu senin sorumluluğundadır.
> Yazılım "olduğu gibi" sunulur (bkz. [LICENSE](LICENSE)). Oyun görselleri ve site verisi depoda yoktur
> (bkz. [a2radar/data/README.md](a2radar/data/README.md)).

## Kurulum
Windows, Python 3.10+ ve [Npcap](https://npcap.com/#download) ("WinPcap API-compatible Mode" işaretli).
```
pip install .            # veya: pip install -r requirements.txt
a2radar                  # veya: python -m a2radar   (Baslat.bat yönetici olarak başlatır)
```
Paket yakalama için yönetici yetkisi gerekebilir. Testler: `python -m unittest discover -s tests`
Lisans: MIT, © 2026 mimasdev.

## Tek komutla kurulum 
PowerShell'de şunu yapıştır (Python 3.10+ ve Npcap yoksa kurar, radarı ayrı bir sanal ortama yükler, masaüstüne yönetici olarak çalışan kısayol koyar):
```
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Mimasistos/wing-radar/main/install.ps1 | iex"
```
Depoyu indirdiysen (ZIP) `install.bat` dosyasına çift tıklaman yeterli; `Baslat.bat` de eksik Python paketlerini ilk açılışta kendisi kurar (Python ve Npcap yine gerekir). Betik şunları yapar:
- **Python:** `py` başlatıcısıyla 3.12/3.13/3.11/3.10 arar; yoksa `winget` ile Python 3.12 kurar.
- **Npcap:** Kuruluysa dokunmaz. Değilse resmî siteden (npcap.com) indirir, dijital imzasını doğrular ve Npcap'in kendi kurulum penceresini açar.
  Lisansı sen onaylarsın; "WinPcap API-compatible Mode" kutusu işaretli kalsın.
- **Radar:** `%LOCALAPPDATA%\A2Radar\venv` içine kurar; sistem Python'una dokunmaz. Aynı komutu tekrar çalıştırmak günceller.
- Yönetici yetkisi (UAC) yalnızca Python/Npcap kurulacaksa istenir. Yalnızca durum görmek için: `install.ps1 -Check`. Kaldırmak için: `install.ps1 -Uninstall`.
- Rota/yol haritası için veri dosyalarını (depoda yok, bkz. `a2radar/data/README.md`) ilk bilgisayardan `%APPDATA%\A2Radar\` klasörüne kopyala
  ya da `install.ps1 -DataFrom <klasör>` kullan.

## v1'e göre değişenler
- TCP akışı seq numarasına göre yeniden birleştirilir (v1 sırasız/tekrar paketlerde bozuluyordu).
- BPF filtresi + oyun portu otomatik izlenir; oyun kapanıp açılınca sniffer yeniden kurulur.
- Kimlik yalnızca kendi-konum paketlerinden bağlanır. v1 başka oyuncunun hareketine bağlanabiliyordu.
- Paket şeması `a2radar/opcodes.py` içinde; oyun yaması sonrası `%APPDATA%\A2Radar\opcodes.json` ile düzeltilir.
- Ayarlar `%APPDATA%` altında (v1 kod klasörüne yazıyordu, karakter adı repoya sızıyordu).
- Konum bayatlığı, kimlik/oyun durumu ekranda gösterilir. Yön bilinmiyorsa kuzey = yukarı.
- Birim testleri (parçalı, sırasız, tekrar, bozuk, geçersiz koordinat).

## Arama rotası (Altgard)
Küp paketi yokken radar, haritadaki ~1500 olası noktayı en az duraktan kapsayan bir rota çıkarır ve sonraki durağı gösterir.
1. ⚙ → **Karakterimi bul** (4 sn zıpla). 2. ⚙ → **Harita kalibrasyonu**: bilinen 3+ yerin yanında zıplayıp "Buradayım".
3. ⚙ → **Rota haritası**: noktalar, rota, oyuncu ve tarama yarıçapı. Yarıçap, küpün ilk görüldüğü mesafelerden ölçülür.
Radarın altında yarı saydam **yol haritası paneli** (kuzey ↑) canlı çizilir: noktalar, rota, duraklar, tarama çemberi, canlı küpler.
Fare tekerleği yakınlaştırır; ⚙'dan kapatılabilir. Sonraki durak ekran dışındaysa kenarda sarı ok + mesafe görünür.
Durak, oyuncu yarıçap içine girip küp paketi gelmeyince elenir. Konum yalnızca zıplama/dash'te güncellenir; gezerken zıpla.
Veri: `a2radar/data/altgard_cubes.json`, sitenin yakın çekim ekran görüntülerinden çıkarılmış ikon merkezleridir (`tools/extract_icons.py`, 9 yakın çekim + 1 tam harita; her biri harita görseline 0.90–0.97 skorla hizalandı). Doğruluk yaklaşık ±10–20 m. Hangilerinin aktif olduğu bilinmez, z yok. Sitenin ham `options.json` koordinatları sitenin çizdiği küp konumlarıyla örtüşmediği için kullanılmadı (`data/_old/`).

## Kalibrasyon yerleri
Önerilen 6 yer: Gravekeeper Camp, Moslan Forest Dry Sade, Faded Stump, Basfelt Ruins Cliff, Outskirts Stall, Abandoned Campsite.
Koordinatı kayıtlı değilse: Rota haritası → "Yer" seç → oyunda o yerin yanında zıpla → "Bulunduğum yeri kaydet" → haritada yerine tıkla.
Pencere her yerin dönüşümden sapmasını gösterir; tek büyük sapma yanlış tıklamadır, o yeri yeniden kaydet.

## Rota mantığı
1. **Aday noktalar:** ekran görüntülerinden çıkarılan ~1500 ikon merkezi. İsteğe bağlı: kara dışındakileri ele (⚙: "Kara dışındaki şüpheli noktaları kullanma", varsayılan kapalı).
2. **Duraklar:** açgözlü küme örtüsü. Tarama yarıçapı R içinde tüm noktaları bırakan en az durak seçilir.
3. **Sıra:** Hilbert eğrisiyle anında ilk sıra, arka planda en yakın komşu + komşu listeli 2-opt (arayüz donmaz).
4. **İlerleme:** oyuncu bir durağın R'sine girip küp paketi gelmezse oradaki noktalar elenir; duraklar düşer.
Dönüşüm varsayılanı: dünya(cm) = 200·(piksel−2048) (2 m/piksel). Kalibrasyon yoksa tahmini çalışır, 3+ yer kaydedince iyileşir.

## Sorun giderme: "Zıplama algılanmadı"
"Karakterimi bul" 6 sn boyunca yakındaki varlıkların konum paketlerini dinler ve yüksekliği (z) en çok değişen varlığı karakter sayar.
- Kalabalıktan uzak dur; 6 sn boyunca sürekli zıpla, dash yap veya kanat aç (tek zıplama yetmeyebilir).
- Hata mesajı sayıları gösterir (varlık sayısı, örnek sayısı, gelen paket, yükseklik cm). Gelen paket 0 ise radar oyunun trafiğini göremiyor demektir (Npcap kurulu mu, yönetici olarak mı açıldı, oyun bağlı mı?).
- Tanı bilgisi `%APPDATA%\A2Radar\calibration.log` dosyasına yazılır (varlıklar, örnekler, en sık opcode'lar). Sorun sürerse bu dosyayı paylaş.

## Sınırlar
Offset ve opcode değerleri v1'den alındı, canlı oyunda doğrulanmadı. Oyun güncellenirse bozulabilir.
Konum sadece zıplama/dash/skill gibi eylemlerde güncellenir (v1 notu).

## Küp noktalarını yenilemek
Sitede haritayı yakınlaştırıp (küp kategorisi açık) bölge bölge ekran görüntüsü al; tam harita görüntüsü de ekleyebilirsin. Sonra:
`python tools/extract_icons.py ekran1.png ekran2.png ... --out a2radar/data/altgard_cubes.json --check onizleme_klasoru`
Araç her görüntüyü harita görseline otomatik hizalar (skor 0.45'in altındakini atlar), yakın çekim önce işlenir ve kapsadığı alanı sahiplenir.
Gereken: opencv-python-headless (`pip install opencv-python-headless numpy`).
