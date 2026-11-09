"""Paket şeması tek yerde. Oyun güncellenince sadece burası (veya
%APPDATA%/A2Radar/opcodes.json ile üzerine yazma) değişir, kod değişmez.

Offset'ler v1 projesinden devralındı; ben canlı oyunda doğrulamadım.
Her kayıt: opcode listesi + yükün (opcode sonrası) içindeki konum ofseti.
Ofset ``"n"`` içeren değerler, baştaki entity-id varint uzunluğunu temsil eder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    # Oyuncunun kendi konumu (sadece bağlı entity için)
    "self_pos_fixed": [{"op": [13893], "off": 25}, {"op": [14338], "off": 17}],
    "self_pos_tail": [{"op": [14378, 14379, 14380, 14340], "min_len": 30}],
    "self_pos_n": [{"op": list(range(14096, 14161)), "off": 2}],
    # Kimlik + konum içeren paketler
    "self_info": {"op": 13857, "off": 16},
    "zone_enter": {"op": 13859, "off": 5},          # n + off
    "teleport": {"op": 22046, "off": 17},
    # Varlık oluşma / silinme
    "spawn": [{"op": 13876, "id_off": 2, "pos_off": 6},
              {"op": 13889, "id_off": 5, "pos_off": 11}],
    "despawn": [13877, 13882, 13890],
    "cube_pickup_nearby": 36205,
    # Oyuncunun ilk hareket paketi: otomatik kimlik bağlama için
    "autobind": [],   # kalabalıkta başkasına bağlanıyordu; kalibrasyon kullan
}

# Hidden Cube NPC kimlikleri: aralıklar (dahil) ve tekiller
CUBE_IDS = {
    "ranges": [[1000548, 1000566], [1200039, 1200041], [1300081, 1300082]],
    "single": [1000295, 1300754, 1301199, 1301200],
    "asmodian": [1301199, 1301200, 1300754, 1300081, 1300082,
                 1000566, 1200039, 1200040, 1200041],
}


def config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "opcodes.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(config_path().read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def is_cube(npc_id: int) -> bool:
    return npc_id in CUBE_IDS["single"] or any(a <= npc_id <= b for a, b in CUBE_IDS["ranges"])


def is_asmodian(npc_id: int) -> bool:
    return npc_id in CUBE_IDS["asmodian"]
