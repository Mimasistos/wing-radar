from __future__ import annotations

import io
import math
import struct
import threading
import wave
from functools import lru_cache

try:
    import winsound
except ImportError:
    winsound = None

PROFILES = {
    "Radar Ping": [(1250, 220, True)],
    "Klasik Bip": [(900, 150, False)],
    "Çift Çan": [(750, 70, False), (0, 25, False), (1250, 140, True)],
    "Sci-Fi": [(520, 70, False), (1040, 160, True)],
    "Hafif Klik": [(1600, 45, True)],
}
RATE = 22050


@lru_cache(maxsize=32)
def wav(profile: str, volume: float) -> bytes:
    amp = int(32767 * max(0.0, min(1.0, volume)))
    out = bytearray()
    for f, ms, decay in PROFILES.get(profile, PROFILES["Radar Ping"]):
        n = RATE * ms // 1000
        for i in range(n):
            env = 1 - i / n if decay else 1
            out += struct.pack("<h", int(amp * env * math.sin(2 * math.pi * f * i / RATE)) if f else 0)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(out))
    return b.getvalue()


def play(profile: str, volume: float):
    if winsound is None or volume <= 0.01:
        return
    data = wav(profile, round(volume, 2))
    threading.Thread(target=lambda: winsound.PlaySound(data, winsound.SND_MEMORY), daemon=True).start()
