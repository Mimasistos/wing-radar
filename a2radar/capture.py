from __future__ import annotations

import threading

import psutil

from .protocol import Decoder

KNOWN_PORTS = (13328, 13320)


def find_game_port():
    """(port, pid): Oyun sürecinin kurulu TCP bağlantısının uzak portu."""
    fallback = None
    for p in psutil.process_iter(["pid", "name"]):
        if "aion2" not in (p.info["name"] or "").lower():
            continue
        try:
            for c in p.net_connections(kind="tcp"):
                if c.status == "ESTABLISHED" and c.raddr:
                    if c.raddr.port in KNOWN_PORTS:
                        return c.raddr.port, p.pid
                    if 10000 <= c.raddr.port <= 20000:
                        fallback = (c.raddr.port, p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return fallback or (None, None)


class Capture(threading.Thread):
    """Oyun portunu izler; port değişirse veya oyun kapanırsa sniffer'ı yeniden kurar."""

    def __init__(self, state, on_error=None):
        super().__init__(daemon=True, name="capture")
        self.state, self.on_error = state, on_error
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        from scapy.all import AsyncSniffer, IP, TCP, Raw  # geç import: yavaş yüklenir

        sniffer, port = None, None
        try:
            while True:
                cur, _ = find_game_port()
                self.state.connected = cur is not None
                if cur != port:
                    if sniffer:
                        sniffer.stop()
                        sniffer = None
                    port = cur
                    if port is not None:
                        dec = Decoder(self.state, port)

                        def on_pkt(pkt, dec=dec):
                            if IP in pkt and TCP in pkt and Raw in pkt:
                                ip, tcp = pkt[IP], pkt[TCP]
                                dec.feed(ip.src, tcp.sport, ip.dst, tcp.dport, tcp.seq, bytes(pkt[Raw].load))

                        sniffer = AsyncSniffer(filter=f"tcp src port {port}", prn=on_pkt, store=False)
                        sniffer.start()
                if self.stop_event.wait(2.0):
                    break
        except Exception as exc:  # Npcap yok, yetki yok vb.
            if self.on_error:
                self.on_error(f"Yakalama durdu: {exc}")
        finally:
            if sniffer:
                try:
                    sniffer.stop()
                except Exception:
                    pass
