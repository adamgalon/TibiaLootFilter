import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tibia_loot_manager import weekly
from tibia_loot_manager.webui.service import AppService, UserError

from .test_webui import make_store


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class WeekMathTest(unittest.TestCase):
    def test_server_save_follows_german_time(self):
        self.assertEqual(weekly.server_save(utc("2026-10-05T00:00").date()), utc("2026-10-05T08:00"))  # CEST
        self.assertEqual(weekly.server_save(utc("2026-11-02T00:00").date()), utc("2026-11-02T09:00"))  # CET
        self.assertEqual(weekly.server_save(utc("2026-03-30T00:00").date()), utc("2026-03-30T08:00"))  # day after switch

    def test_week_boundaries(self):
        self.assertEqual(weekly.week_start(utc("2026-10-05T07:59")), utc("2026-09-28T08:00"))  # before Monday's save
        self.assertEqual(weekly.week_start(utc("2026-10-05T08:00")), utc("2026-10-05T08:00"))
        self.assertEqual(weekly.week_start(utc("2026-10-11T23:00")), utc("2026-10-05T08:00"))  # Sunday night
        self.assertEqual(weekly.next_reset(utc("2026-10-28T12:00")), utc("2026-11-02T09:00"))

    def test_roll_over_archives_the_old_week(self):
        old = {"week_start": "2026-09-28T08:00:00+00:00",
               "tasks": [{"key": "a", "name": "A", "required": 10, "collected": 12},
                         {"key": "b", "name": "B", "required": 5, "collected": 1}]}
        week, archive, rolled = weekly.roll_over(old, [], utc("2026-10-06T00:00"))
        self.assertTrue(rolled)
        self.assertEqual(week["tasks"], [])
        self.assertEqual((archive[0]["tasks"], archive[0]["done"]), (2, 1))
        same, _a, again = weekly.roll_over(week, archive, utc("2026-10-07T00:00"))
        self.assertFalse(again)
        self.assertIs(same, week)


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AppService(make_store(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_track_and_remove(self):
        self.assertEqual([r["name"] for r in self.svc.weekly_search("buck")["rows"]], ["buckle"])
        self.svc.weekly_add("wiki:Buckle", 15)
        self.svc.weekly_set("wiki:Buckle", delta=5)
        self.svc.weekly_set("wiki:Buckle", delta=12)
        task = self.svc.weekly_overview()["tasks"][0]
        self.assertEqual((task["collected"], task["remaining"], task["done"], task["id"]), (17, 0, True, 17829))
        self.svc.weekly_set("wiki:Buckle", delta=-100)
        self.assertEqual(self.svc.weekly_overview()["tasks"][0]["collected"], 0)  # never negative
        self.svc.weekly_remove("wiki:Buckle")
        self.assertEqual(self.svc.weekly_overview()["tasks"], [])

    def test_required_amount_respects_the_task_range(self):
        with self.assertRaises(UserError):
            self.svc.weekly_add("wiki:Buckle", 5)  # Buckle tasks ask for 10–20
        with self.assertRaises(UserError):
            self.svc.weekly_add("wiki:Buckle", 21)
        self.svc.weekly_add("wiki:Buckle", None)  # defaults to the minimum
        self.assertEqual(self.svc.weekly_overview()["tasks"][0]["required"], 10)
        with self.assertRaises(UserError):
            self.svc.weekly_add("wiki:Buckle", 12)  # already tracked

    def test_only_delivery_items_can_be_tracked(self):
        with self.assertRaises(UserError):
            self.svc.weekly_add("3031", 10)

    def test_missing_task_items_are_added_to_accepted_loot(self):
        self.svc.weekly_add("wiki:Buckle", 10)
        self.svc.toggle_accepted("17829")  # remove it from the Accepted list
        self.assertFalse(self.svc.weekly_overview()["tasks"][0]["in_accepted"])
        self.assertEqual(self.svc.weekly_add_missing_to_accepted()["added"], ["buckle"])
        self.assertTrue(self.svc.weekly_overview()["tasks"][0]["in_accepted"])

    def test_new_week_is_reported_once(self):
        self.svc.weekly_add("wiki:Buckle", 10)
        later = weekly.week_start(utc("2030-01-07T12:00"))
        with mock.patch.object(weekly, "week_start", return_value=later):
            first = self.svc.weekly_overview()
            second = self.svc.weekly_overview()
        self.assertTrue(first["new_week"])
        self.assertEqual(first["tasks"], [])
        self.assertEqual(first["archive"][0]["tasks"], 1)
        self.assertFalse(second["new_week"])


if __name__ == "__main__":
    unittest.main()
