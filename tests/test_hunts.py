import tempfile
import unittest
from pathlib import Path

from tibia_loot_manager import hunts
from tibia_loot_manager.webui.service import AppService, UserError

from .test_webui import make_store

REPORT = """Session data: From 2026-10-05, 14:23:11 to 2026-10-05, 15:23:41
Session: 01:00h
Raw XP Gain: 1,234,567
XP Gain: 1,851,850
Raw XP/h: 1,234,567
XP/h: 1,851,850
Loot: 12,345
Supplies: 2,000
Balance: 10,345
Damage: 9,876,543
Killed Monsters:
  123x dragon
  4x dragon lord
Looted Items:
  3x buckles
  250x gold coins
  2x platinum coins
  1x a lit lamp
  5x mysterious things
"""


class ParseTest(unittest.TestCase):
    def test_fields_and_sections(self):
        r = hunts.parse_report(REPORT)
        self.assertEqual((r["from"], r["duration"], r["xp"], r["loot"], r["balance"]),
                         ("2026-10-05, 14:23:11", "01:00h", 1851850, 12345, 10345))
        self.assertEqual(r["monsters"], [{"count": 123, "name": "dragon"}, {"count": 4, "name": "dragon lord"}])
        self.assertEqual([i["count"] for i in r["items"]], [3, 250, 2, 1, 5])

    def test_tolerates_spacing_and_negative_balance(self):
        r = hunts.parse_report("Balance: -1,500\r\n\r\nLooted Items:\r\n\t1,200x  gold coins  \r\n")
        self.assertEqual(r["balance"], -1500)
        self.assertEqual(r["items"], [{"count": 1200, "name": "gold coins"}])

    def test_singular_forms(self):
        self.assertIn("piece of cloth", hunts.singular_forms("pieces of cloth"))
        self.assertIn("knife", hunts.singular_forms("knives"))
        self.assertEqual(hunts.singular_forms("a dragon shield")[0], "dragon shield")
        self.assertIn("ruby", hunts.singular_forms("rubies"))
        self.assertIn("box", hunts.singular_forms("boxes"))


class HuntServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AppService(make_store(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_analysis(self):
        a = self.svc.hunt_analyze(REPORT)
        rows = {r["line"]: r for r in a["rows"]}
        self.assertEqual(rows["buckles"]["id"], 17829)
        self.assertEqual(rows["gold coins"]["value"], 250)  # coins at their fixed value
        self.assertEqual(rows["platinum coins"]["value"], 200)
        self.assertEqual(a["npc_total"], 450)
        self.assertEqual(a["unmatched"], ["mysterious things"])
        self.assertTrue(rows["buckles"]["in_accepted"])  # buckle is on the Delivery Task list

    def test_import_is_saved_once(self):
        first = self.svc.hunt_analyze(REPORT)
        again = self.svc.hunt_analyze(REPORT.replace("\n", "\r\n"))
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.svc.hunt_list()["hunts"]), 1)
        self.svc.hunt_delete(first["id"])
        self.assertEqual(self.svc.hunt_list()["hunts"], [])

    def test_rejects_other_text(self):
        with self.assertRaises(UserError):
            self.svc.hunt_analyze("hello world")

    def test_apply_to_weekly_tasks_once(self):
        self.svc.weekly_add("wiki:Buckle", 10)
        a = self.svc.hunt_analyze(REPORT)
        self.assertEqual(next(r for r in a["rows"] if r["line"] == "buckles")["task"]["remaining"], 10)
        result = self.svc.hunt_apply_tasks(a["id"])
        self.assertEqual(result["updated"], [{"name": "buckle", "added": 3}])
        self.assertEqual(self.svc.weekly_overview()["tasks"][0]["collected"], 3)
        with self.assertRaises(UserError):
            self.svc.hunt_apply_tasks(a["id"])  # no double counting
        self.assertTrue(self.svc.hunt_open(a["id"])["applied_this_week"])


if __name__ == "__main__":
    unittest.main()
