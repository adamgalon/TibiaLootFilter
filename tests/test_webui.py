import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tibia_loot_manager import lootfile
from tibia_loot_manager.datastore import DataStore
from tibia_loot_manager.sources import tibia_client
from tibia_loot_manager.storage import write_json_atomic
from tibia_loot_manager.webui import service as service_mod
from tibia_loot_manager.webui.server import Server
from tibia_loot_manager.webui.service import AppService, Dialogs, UserError


def item(cid, name, category="Others"):
    return {"id": cid, "name": name, "category": category, "npc_offers": []}


class FakeDialogs(Dialogs):
    def __init__(self, save_to=None):
        self.save_to = save_to

    def save_file(self, initial_name, extension, kinds, initial_dir=None):
        return self.save_to


def make_store(root: Path) -> DataStore:
    chars = root / "characterdata"
    (chars / "111").mkdir(parents=True)
    (chars / "111" / lootfile.FILE_NAME).write_text(json.dumps(
        {"blacklistTypes": [2920], "listType": "blacklist", "whitelistTypes": [3031]}))
    items = [item(3031, "gold coin"), item(3035, "platinum coin"), item(17829, "buckle", "Armors"),
             item(2915, "lit lamp")]
    cache = root / "cache"
    write_json_atomic(cache / "catalog.json", {
        "source": {"parser_version": tibia_client.PARSER_VERSION, "client_version": "test"},
        "items": {str(i["id"]): i for i in items}})
    write_json_atomic(cache / "delivery.json", {"source": {"url": "u"}, "items": {
        "Buckle": {"title": "Buckle", "task_category": "Armors", "min_qty": 10, "max_qty": 20, "npc_buy_price": 7000,
                   "wiki": {"itemids": [17829], "actualname": "buckle"}}}})
    write_json_atomic(cache / "wiki_index.json", {"source": {}, "pages": {
        "Lamp": {"title": "Lamp", "url": "https://tibia.fandom.com/wiki/Lamp", "itemids": [2914, 2915],
                 "actualname": "lamp", "dropped_by": []}}})
    write_json_atomic(root / "user_state.json", {"characterdata_dir": str(chars), "onboarded": True})
    return DataStore(root)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.svc = AppService(make_store(self.root), FakeDialogs(str(self.root / "export.json")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_and_catalog(self):
        st = self.svc.state()
        self.assertTrue(st["reference_ok"])
        self.assertEqual(st["counts"]["accepted"], 1)  # buckle, from the Delivery Task list
        conflicting = self.svc.catalog(idf="conflicting")["rows"]
        self.assertEqual([r["id"] for r in conflicting], [2915])
        self.assertEqual(self.svc.item("2915")["state"], "conflicting")

    def test_toggles(self):
        self.assertTrue(self.svc.toggle_accepted("2915")["in_accepted"])
        self.assertFalse(self.svc.toggle_accepted("2915")["in_accepted"])
        self.assertFalse(self.svc.toggle_delivery("wiki:Buckle")["in_delivery"])
        self.assertEqual(self.svc.state()["counts"]["accepted"], 0)
        self.assertTrue(self.svc.toggle_delivery("wiki:Buckle")["in_delivery"])

    def test_conflicting_item_is_never_exported(self):
        self.svc.toggle_accepted("2915")
        self.assertEqual(self.svc.export()["export_count"], 1)
        result = self.svc.export_file()
        data = lootfile.read_file(Path(result["path"]))
        self.assertEqual(data["whitelistTypes"], [17829])

    def test_install_and_restore(self):
        with mock.patch.object(service_mod, "is_tibia_running", return_value=False):
            preview = self.svc.install_preview("111", lootfile.MERGE)
            self.assertIn("swap", [r["icon"] for r in preview["rows"]])  # mode change is shown
            result = self.svc.install_apply("111", lootfile.MERGE)
            self.assertIsNotNone(result["backup"])
            path = self.root / "characterdata" / "111" / lootfile.FILE_NAME
            self.assertEqual(lootfile.read_file(path)["whitelistTypes"], [3031, 17829])
            backup = self.svc.install("111")["backups"][0]["file"]
            self.svc.restore_apply("111", backup)
            self.assertEqual(lootfile.read_file(path)["listType"], "blacklist")

    def test_install_refused_while_tibia_runs(self):
        with mock.patch.object(service_mod, "is_tibia_running", return_value=True):
            self.assertTrue(self.svc.install_preview("111", lootfile.REPLACE)["tibia_running"])
            with self.assertRaises(UserError):
                self.svc.install_apply("111", lootfile.REPLACE)

    def test_apply_needs_matching_preview(self):
        with mock.patch.object(service_mod, "is_tibia_running", return_value=False):
            self.svc.install_preview("111", lootfile.MERGE)
            with self.assertRaises(UserError):
                self.svc.install_apply("111", lootfile.REPLACE)

    def test_report_template_and_urls(self):
        t = self.svc.report_template("item_id", "2915")
        self.assertEqual(t["item_id"], "2915")
        self.assertIn("Tibia item ID: 2915", t["diagnostics"])
        self.assertNotIn(str(self.root), t["diagnostics"])
        with self.assertRaises(UserError):
            self.svc.open_url("javascript:alert(1)")


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = Server(AppService(make_store(Path(self.tmp.name))))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=True, host=None, conn=None):
        if conn is None:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            self.addCleanup(conn.close)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Token"] = self.server.token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.read()

    def test_token_and_host_required(self):
        self.assertEqual(self.request("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["counts"]["catalog"], 4)

    def test_static_files_and_traversal(self):
        status, body = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Tibia Loot List Manager", body)
        self.assertEqual(self.request("GET", "/../tibia_loot_manager/webui/server.py", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/webui/server.py", token=False)[0], 404)

    def test_keep_alive_bodies_are_consumed(self):
        # Regression: an unread POST body used to corrupt the next request on the same connection.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        self.assertEqual(self.request("POST", "/api/ping", {}, conn=conn)[0], 200)
        self.assertEqual(self.request("POST", "/api/theme", {"theme": "light"}, conn=conn)[0], 200)
        self.assertEqual(json.loads(self.request("GET", "/api/state", conn=conn)[1])["theme"], "light")

    def test_user_errors_are_reported(self):
        status, body = self.request("POST", "/api/settings/limit", {"limit": "-3"})
        self.assertEqual(status, 409)
        self.assertTrue(json.loads(body)["user"])


if __name__ == "__main__":
    unittest.main()
