// interactivemap.app Altgard sayfası açıkken F12 -> Console'a yapıştır. 64 parçayı çekip TEK bir altgard_map.png indirir.
// Chrome "allow pasting" diye engellerse önce konsola:  allow pasting   yazıp Enter'a bas, sonra tekrar yapıştır.
// Ayarlar: Z=3 -> 8x8 parça (~2048 px, yeterli). Daha keskin: Z=4 (256 parça, ~4096 px). FLIP_Y: harita baş aşağı çıkarsa true yap.
(async () => {
  const Z = 3, FLIP_Y = false;
  const base = 'https://interactivemap.app/aion2/maps/imapp/uploads/tiles/altgard_img14_20261002_webp/';
  const n = 2 ** Z;
  const load = (x, y) => new Promise(res => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => res(null);
    im.src = `${base}${Z}_${x}_${y}.webp`;
  });
  const queue = [];
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) queue.push([x, y]);
  const total = queue.length, imgs = {};
  let tw = 0;
  await Promise.all(Array.from({ length: 6 }, async () => {
    while (queue.length) {
      const [x, y] = queue.shift();
      const im = await load(x, y);
      if (im) { imgs[`${x},${y}`] = im; tw = tw || im.naturalWidth; }
    }
  }));
  if (!tw) { console.error('Hiç parça yüklenemedi: adres değişmiş olabilir (Network sekmesinde .webp adresine bak).'); return; }
  const c = document.createElement('canvas');
  c.width = c.height = tw * n;
  const g = c.getContext('2d');
  g.fillStyle = '#000';
  g.fillRect(0, 0, c.width, c.height);
  for (const [k, im] of Object.entries(imgs)) {
    const [x, y] = k.split(',').map(Number);
    g.drawImage(im, x * tw, (FLIP_Y ? n - 1 - y : y) * tw);
  }
  console.log(`${Object.keys(imgs).length}/${total} parça, ${c.width}x${c.height}px. İndirme başlıyor...`);
  c.toBlob(b => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = 'altgard_map.png';
    a.click();
  }, 'image/png');
})();
