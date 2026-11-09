# Veri klasörü

Bu klasörün içeriği depoya konmaz: oyun görseli ve site verisi üçüncü taraf içeriğidir. Radar bunlar olmadan da çalışır
(küp paketlerini gösterir); rota/yol haritası özellikleri için kendi dosyalarını üretip buraya koy:

| Dosya | Ne için | Nasıl üretilir |
| --- | --- | --- |
| `altgard_map.png` | Harita arka planı | Site haritasının parçalarını `tools/browser_stitch.js` (tarayıcı konsolu) veya `tools/stitch_tiles.py` ile tek PNG'ye birleştir |
| `altgard_cubes.json` | Olası küp noktaları (`[[x, y], ...]`, 0..4096 harita uzayı) | Haritayı yakınlaştırıp ekran görüntüleri al, `python tools/extract_icons.py ekran1.png ... --out a2radar/data/altgard_cubes.json` |
| `landmarks.json` | Kalibrasyon için bilinen yerler (`[["Ad", x, y], ...]`, isteğe bağlı) | Elle yaz; yoksa kalibrasyon rota haritasında tıklayarak yapılır |

Kullandığın kaynağın hizmet şartlarına uymak sana aittir.
