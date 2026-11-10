"""Hub arayüz akışı (Tk gerekir; yoksa atlanır). Kullanıcı verisine dokunmamak için APPDATA geçici klasöre alınır."""
import os
import tempfile
import traceback
import unittest
from pathlib import Path

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None

BRAX = "8f860304200092a224000002d5650cc86384d3c30052c846004cb34300ff0194a70794a7076400000064000000000000"
ODYLE = "c192020400b0a61700c02b09c800b081c56319d4460000000000000000010101f9323ac5a10100000000000000000000"


@unittest.skipIf(tk is None, "tkinter yok")
class HubUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp
        try:
            self.root = tk.Tk()
        except tk.TclError:
            os.environ["APPDATA"] = self.old or ""
            self.skipTest("ekran yok")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *a: self.errors.append("".join(traceback.format_exception(*a)))

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self.old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old

    def pump(self, n=4):
        for _ in range(n):
            self.root.update()

    def test_find_then_main_and_tabs(self):
        from a2radar import hub as H
        from a2radar.protocol import Decoder
        hub = H.Hub(self.root, start_capture=False)
        self.assertIsNone(hub.main)                      # karakter yok: önce bulma paneli
        s = hub.state
        s.set_pos(-143700.0, -600.0, 25600.0)
        s.set_player_id(10599, lock=True)
        d = Decoder(s)
        d.packet(13889, bytes.fromhex(BRAX))
        d.packet(13876, bytes.fromhex(ODYLE))
        for i in range(10):
            hub.db.record(2400914, -143700 + i * 900, -600, 25600)
            hub.db.record(1550000, -145000 + i * 900, -1500, 26000)
        hub.show_main()
        self.pump()
        self.assertEqual(len(hub.tabs), 4)

        home = hub.tabs["home"]
        names = [e.name for e in home.mon.visible]
        self.assertIn("Brax", names)
        home.mon.q.set("bra")
        self.pump()
        self.assertEqual([e.name for e in home.mon.visible], ["Brax"])
        home.mon.lb.selection_set(0)
        home.mon._on_select()
        self.assertEqual(hub.sel_monsters, {"Brax"})
        home.mon.q.set("")
        self.pump()
        self.assertIn("Brax", hub.sel_monsters)           # filtre değişince seçim kaybolmaz
        self.assertEqual(len(home.layers()["monsters"]), 10)

        g = hub.tabs["gather"]
        g.sel.add("Odyle")
        g._res_changed()
        g._generate()
        self.assertEqual(len(g.pts), 11)                  # 10 öğrenilmiş + canlı spawn edilen 1 nokta
        g._activate()
        hub._assist()
        self.assertIn("Asistan: durak", hub.assist_text)
        self.assertTrue(hub.assist_on.get())
        g._reverse()
        g._del_wp()
        self.assertEqual(len(g.pts), 11)                  # seçili durak yok: silme yok

        c = hub.tabs["combat"]
        hub.profile["attack_skills"].append({"name": "X", "key": "1"})
        c.on_profile_changed()
        hub.state.stats["hp"] = (50.0, 0)
        c._tick()

        hub._create(__import__("a2radar.profiles", fromlist=["x"]).blank("Ikinci"))
        self.assertEqual(hub.profile["name"], "Ikinci")
        hub.switch_profile("Varsayılan")
        self.assertEqual(len(hub.profile["routes"]["Rota 1"]), 11)   # profil geçişinde rota korunur
        files = {p.stem for p in (Path(self.tmp) / "A2Radar" / "profiles").glob("*.json")}
        self.assertEqual(files, {"Varsayılan", "Ikinci"})
        self.pump()
        self.assertEqual(self.errors, [])

    def test_main_skipped_when_player_already_known(self):
        from a2radar import hub as H
        from a2radar.state import State
        orig = H.State
        H.State = lambda: self._known_state(orig)
        try:
            hub = H.Hub(self.root, start_capture=False)
        finally:
            H.State = orig
        self.assertIsNotNone(hub.main)
        self.assertEqual(self.errors, [])

    @staticmethod
    def _known_state(State):
        s = State()
        s.set_pos(-143700.0, -600.0, 25600.0)
        s.set_player_id(1234, lock=True)
        return s


if __name__ == "__main__":
    unittest.main()
