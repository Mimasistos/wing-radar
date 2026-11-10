import struct

from a2radar import schemascan
from a2radar.protocol import Decoder
from a2radar.state import State

POS = (-143900.0, -3800.0, 26000.0)


def pkt(eid_bytes, pad, xyz, tail=b""):
    return eid_bytes + pad + struct.pack("<fff", *xyz) + tail


def test_scan_finds_pos_offset():
    s = State()
    s.pos = POS
    s.scan = schemascan.SchemaScan(30)
    d = Decoder(s, cfg={"self_pos_fixed": [], "self_pos_tail": [], "self_pos_n": [],
                        "self_info": {"op": 1, "off": 0}, "zone_enter": {"op": 2, "off": 0},
                        "teleport": {"op": 3, "off": 0}, "spawn": [], "despawn": [],
                        "cube_pickup_nearby": 4, "autobind": []})
    for i in range(20):
        d.packet(9999, pkt(b"\x8a\xbf\x02", b"\x04\x20\x00\x21\x88\x1e\x00\x00\x02",
                           (POS[0] + 100 * i, POS[1] - 50 * i, POS[2])))
        d.packet(7777, bytes(range(40)))          # gürültü: isabet vermemeli
    rep = s.scan.report()
    assert rep["ops"][0]["op"] == 9999
    assert rep["ops"][0]["best_abs_off"][0]["off"] == 12
    assert rep["ops"][0]["best_rel_off(n+)"][0]["off"] == 9
    assert all(r["op"] != 7777 for r in rep["ops"])
    assert rep["ops"][0]["entities"] == 1


def test_scan_inactive_without_pos():
    sc = schemascan.SchemaScan(30)
    sc.feed(1, bytes(40), None)
    assert sc.packets == 0
