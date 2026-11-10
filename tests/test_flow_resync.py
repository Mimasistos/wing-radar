"""Akış dayanıklılığı: kaçan TCP parçası ve bozuk (sahte uzunluklu) çerçeve konum akışını dondurmamalı."""
import struct
import unittest

from a2radar import protocol
from a2radar.protocol import Decoder
from a2radar.state import State

OWN = 10599


def vi(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pos_frame(i):
    """14108 çerçevesi: kendi konumum, x = -143000 - 100*i cm."""
    body = struct.pack("<H", 14108) + vi(OWN) + b"\x04" + struct.pack("<fff", -143000.0 - 100 * i, -600.0, 25600.0) + b"\xaa\xbb\xaa\xbb\x01"
    return vi(len(body) + 4) + body


class Stream:
    def __init__(self):
        self.s = State()
        self.s.set_player_id(OWN, lock=True)
        self.d = Decoder(self.s)
        self.seq = 1000
        self.t = 0.0

    def send(self, data: bytes, drop: bool = False, step: float = 0.05):
        self.t += step
        if not drop:
            self.d.feed("1.1.1.1", 13328, "2.2.2.2", 5, self.seq, data, now=self.t)
        self.seq = (self.seq + len(data)) & 0xFFFFFFFF

    def send_stream(self, data: bytes, size=37, drop_index=None):
        for k in range(0, len(data), size):
            self.send(data[k:k + size], drop=(k // size == drop_index))

    @property
    def x(self):
        return self.s.pos[0] if self.s.pos else None


class ResyncTests(unittest.TestCase):
    def test_normal_stream_untouched(self):
        st = Stream()
        st.send_stream(b"".join(pos_frame(i) for i in range(40)))
        self.assertAlmostEqual(st.x, -143000.0 - 100 * 39, delta=1)
        self.assertEqual(st.s.diag["gap"] + st.s.diag["stall"], 0)

    def test_lost_segment_recovers(self):
        st = Stream()
        st.send_stream(b"".join(pos_frame(i) for i in range(15)))          # ops öğrenilsin
        data = b"".join(pos_frame(i) for i in range(15, 160))
        st.send_stream(data, drop_index=10)                                 # ortada bir parça kaçtı
        self.assertAlmostEqual(st.x, -143000.0 - 100 * 159, delta=1)       # sonrası yine çözülüyor
        self.assertGreaterEqual(st.s.diag["gap"], 1)

    def test_bogus_length_does_not_stall_forever(self):
        st = Stream()
        st.send_stream(b"".join(pos_frame(i) for i in range(10)))
        st.send(b"\xf0\x80\x08")                                            # ~131 KB bekletecek sahte uzunluk
        st.send_stream(b"".join(pos_frame(i) for i in range(10, 200)))
        self.assertAlmostEqual(st.x, -143000.0 - 100 * 199, delta=1)
        self.assertGreaterEqual(st.s.diag["stall"], 1)

    def test_recovery_time_is_bounded(self):
        """Bozulmadan sonra konum en geç GAP/STALL süreleri + küçük pay içinde tekrar güncellenmeli."""
        st = Stream()
        st.send_stream(b"".join(pos_frame(i) for i in range(10)))
        st.send(b"\xf0\x80\x08")
        t0 = st.t
        updated_at = None
        last_x = st.x
        for i in range(10, 400):
            st.send(pos_frame(i), step=0.1)
            if st.x != last_x and st.x == -143000.0 - 100 * i:
                updated_at = st.t - t0
                break
        self.assertIsNotNone(updated_at)
        self.assertLess(updated_at, protocol.STALL_S + 1.0)

    def test_unexpected_exception_does_not_lock_buffer(self):
        st = Stream()
        orig = st.d.packet
        calls = {"n": 0}

        def boom(op, p):
            calls["n"] += 1
            if calls["n"] == 3:
                raise TypeError("test")
            return orig(op, p)

        st.d.packet = boom
        st.send_stream(b"".join(pos_frame(i) for i in range(20)))
        self.assertAlmostEqual(st.x, -143000.0 - 100 * 19, delta=1)
        self.assertEqual(st.s.diag["decode_error"], 1)
        self.assertIn("TypeError", st.s.last_decode_error)


if __name__ == "__main__":
    unittest.main()
