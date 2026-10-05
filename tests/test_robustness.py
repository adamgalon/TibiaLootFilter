"""Regression tests for the review fixes: paging, damaged files, process check, cancellation."""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tibia_loot_manager import tibia_process
from tibia_loot_manager.datastore import DataStore
from tibia_loot_manager.sources.http import Cancelled, PoliteHttpClient
from tibia_loot_manager.state import UserState
from tibia_loot_manager.webui.service import AppService

from .test_webui import make_store


class CatalogPagingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AppService(make_store(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_cover_the_whole_catalog(self):
        first = self.svc.catalog(limit=2)
        second = self.svc.catalog(offset=2, limit=2)
        self.assertEqual(first["total"], 4)
        keys = [r["key"] for r in first["rows"] + second["rows"]]
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)  # no overlap between pages

    def test_limits_are_clamped(self):
        self.assertEqual(len(self.svc.catalog(offset=-5, limit=0)["rows"]), 1)
        self.assertEqual(len(self.svc.catalog(limit=10 ** 9)["rows"]), 4)


class DamagedFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = make_store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_damaged_state_restores_previous_save(self):
        state = self.store.load_state()
        state.accepted_extra = [3031]
        self.store.save_state(state)  # first save: no previous file to keep... except the fixture's
        state.accepted_extra = [3031, 3035]
        self.store.save_state(state)  # keeps [3031] as the backup
        (self.root / "user_state.json").write_text("{not json")
        store = DataStore(self.root)
        lib, notices = store.load_library()
        self.assertEqual(lib.state.accepted_extra, [3031])
        self.assertTrue(any("previous save was restored" in n for n in notices))
        self.assertTrue(list(self.root.glob("user_state.json.damaged-*")))

    def test_damaged_state_without_backup_starts_fresh(self):
        (self.root / "user_state.json").write_text("[1, 2")
        lib, notices = DataStore(self.root).load_library()
        self.assertEqual(lib.state.accepted_extra, [])
        self.assertTrue(any("start fresh" in n for n in notices))

    def test_damaged_caches_fall_back(self):
        (self.root / "cache" / "delivery.json").write_text('{"items": ')
        (self.root / "cache" / "wiki_index.json").write_text('"wrong shape"')
        (self.root / "cache" / "wiki_lookups.json").write_text("garbage")
        (self.root / "source_log.json").write_text("{")
        lib, notices = DataStore(self.root).load_library()
        self.assertTrue(lib.delivery["items"])  # the bundled list
        self.assertTrue(lib.wiki_index["pages"])  # the bundled index
        self.assertEqual(lib.wiki_lookups, {})
        self.assertGreaterEqual(sum("damaged" in n for n in notices), 3)

    def test_wrong_types_in_state_are_dropped(self):
        state = UserState.from_dict({"accepted_extra": "3031", "loot_list_limit": -1, "onboarded": "yes",
                                     "character_labels": {"1": 5}, "theme": "light", "unknown": 1})
        self.assertEqual((state.accepted_extra, state.loot_list_limit, state.onboarded, state.character_labels),
                         ([], None, False, {}))
        self.assertEqual(state.theme, "light")


class UnresolvedDeliveryTest(unittest.TestCase):
    def test_excluded_unresolved_item_is_not_shown_as_on_the_list(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_store(root)
        delivery = json.loads((root / "cache" / "delivery.json").read_text())
        delivery["items"]["Ghost Item"] = {"title": "Ghost Item", "task_category": "Others", "min_qty": 1,
                                           "max_qty": 2, "npc_buy_price": None, "wiki": {"itemids": []}}
        (root / "cache" / "delivery.json").write_text(json.dumps(delivery))
        svc = AppService(DataStore(root))
        self.assertTrue(next(r for r in svc.catalog()["rows"] if r["key"] == "wiki:Ghost Item")["in_delivery"])
        svc.toggle_delivery("wiki:Ghost Item")
        row = next(r for r in svc.catalog()["rows"] if r["key"] == "wiki:Ghost Item")
        self.assertFalse(row["in_delivery"])
        self.assertNotIn("wiki:Ghost Item", [r["key"] for r in svc.catalog(seg="del")["rows"]])


class CancelTest(unittest.TestCase):
    def test_cancelled_client_stops_before_requesting(self):
        cancel = threading.Event()
        http = PoliteHttpClient(min_interval=30, cancel=cancel)
        http._last_request = time.monotonic()  # the next request would have to wait 30 s
        threading.Timer(0.2, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            http.get_json("http://127.0.0.1:9/never")
        self.assertLess(time.monotonic() - started, 5)  # woke up on cancel, didn't sleep 30 s


@unittest.skipUnless(sys.platform == "win32", "Windows process check")
class ProcessCheckTest(unittest.TestCase):
    def test_detects_a_tibia_client_process(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = Path(tmp.name) / "Tibia" / "bin" / "client.exe"
        fake.parent.mkdir(parents=True)
        shutil.copy(Path(r"C:\Windows\System32\PING.EXE"), fake)
        proc = subprocess.Popen([str(fake), "-n", "20", "127.0.0.1"], stdout=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and tibia_process.tibia_status() != tibia_process.RUNNING:
            time.sleep(0.1)
        self.assertEqual(tibia_process.tibia_status(), tibia_process.RUNNING)
        self.assertIn(str(fake).lower(), [p.lower() for p in tibia_process.running_tibia_paths()])

    def test_path_matching(self):
        self.assertTrue(tibia_process.is_tibia_path(r"C:\Users\x\AppData\Local\Tibia\packages\Tibia\bin\client.exe"))
        self.assertFalse(tibia_process.is_tibia_path(r"C:\Program Files\Other\client.exe"))
        self.assertFalse(tibia_process.is_tibia_path(r"C:\Tibia\notes.exe"))


if __name__ == "__main__":
    unittest.main()
