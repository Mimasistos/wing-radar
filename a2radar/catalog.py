"""Canavar / kaynak kataloğu: yerleşik adlar + kullanıcı etiketleri + spawn veritabanı birleşimi."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import entities as E
from . import opcodes


@dataclass
class Entry:
    name: str
    kind: str                       # canavar | kaynak | npc | diğer | "" (etiketsiz)
    npc_ids: list = field(default_factory=list)
    spawns: int = 0                 # bilinen spawn noktası sayısı


def build(labels: E.Labels, db, kind: str) -> list[Entry]:
    """kind: 'canavar' | 'kaynak' | ... | '' (etiketsiz). Ada göre gruplanır (aynı ad, birden çok kimlik olabilir)."""
    ids = set(E.KNOWN) | set(labels.data) | set(db.npc_ids())
    groups: dict[str, Entry] = {}
    for n in ids:
        if opcodes.is_cube(n) or n <= 0 or n in E.IGNORED:
            continue
        lab = labels.get(n)
        k = lab["kind"] if lab else ""
        if k != kind:
            continue
        name = lab["name"] if lab else f"NPC {n}"
        g = groups.setdefault(name, Entry(name, k))
        g.npc_ids.append(n)
        g.spawns += db.count(n)
    for g in groups.values():
        g.npc_ids.sort()
    return sorted(groups.values(), key=lambda g: g.name.lower())


def filter_entries(entries: list[Entry], text: str) -> list[Entry]:
    t = text.strip().lower()
    return [e for e in entries if t in e.name.lower()] if t else list(entries)


def ids_for(entries: list[Entry], names) -> list[int]:
    wanted = set(names)
    return [n for e in entries if e.name in wanted for n in e.npc_ids]
