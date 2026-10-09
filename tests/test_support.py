import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from tibia_loot_manager import support
from tibia_loot_manager.state import UserState

from .test_integrity import make


class RedactTest(unittest.TestCase):
    def test_paths_ids_labels_emails(self):
        home = str(Path.home())
        text = (f"{home}\\AppData\\Local\\Tibia\\packages\\Tibia\\characterdata\\1234567\\lootBlackWhitelist.json "
                "failed for Knight Adam; mail me at someone@example.com")
        out = support.redact(text, ["Knight Adam"])
        self.assertNotIn(home, out)
        self.assertNotIn("1234567", out)
        self.assertNotIn("Knight Adam", out)
        self.assertNotIn("someone@example.com", out)
        self.assertIn("characterdata\\<character folder>", out)

    def test_backup_folder_number(self):
        out = support.redact("Could not read C:\\x\\backups\\4711\\lootBlackWhitelist-20261010.json")
        self.assertNotIn("4711", out)

    def test_short_label_matches_whole_words_only(self):
        self.assertEqual(support.redact("week of Ek, ek.", ["Ek"]), "week of <character label>, <character label>.")


class DiagnosticsTest(unittest.TestCase):
    def test_item_diagnostics(self):
        lib = make(state=UserState(character_labels={"1": "Secret Name"}))
        rows = dict(support.diagnostics(lib, {}, client_id=2915, operation="Install loot list",
                                        error="Secret Name broke"))
        self.assertEqual(rows["Tibia item ID"], "2915")
        self.assertIn("Conflicting", rows["ID mapping"])
        self.assertEqual(rows["Conflicting wiki pages"], "Lamp")
        self.assertEqual(rows["Failing operation"], "Install loot list")
        self.assertNotIn("Secret Name", rows["Error message"])


class ComposeTest(unittest.TestCase):
    def test_item_report_fields(self):
        title, body = support.compose("item_id", "Wrong ID", {
            "item_name": "gold coin", "item_id": "3031", "expected": "3031", "source_url": "https://x",
            "description": "looks fine"}, "- App version: 0.2.0")
        self.assertEqual(title, "[Incorrect item ID or item name] Wrong ID")
        self.assertIn("**Tibia item ID:** 3031", body)
        self.assertIn("App version", body)
        self.assertNotIn("Steps to reproduce", body)

    def test_bug_report_fields(self):
        _title, body = support.compose("bug", "", {"steps": "1. click", "expected_behavior": "works",
                                                   "actual_behavior": "crash"}, None)
        self.assertIn("### Steps to reproduce\n1. click", body)
        self.assertNotIn("Diagnostic information", body)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_configured_by_default(self):
        config = support.SupportConfig.load(self.root)
        self.assertFalse(config.can_send_reports)
        self.assertFalse(config.has_contact)
        self.assertEqual(config.report_links("t", "b"), [])

    def test_local_override_and_validation(self):
        (self.root / support.CONFIG_FILE).write_text(json.dumps({
            "report_url_template": "https://example.org/new?title={title}&body={body}",
            "contact_email": "not-an-email", "contact_url": "javascript:alert(1)"}))
        config = support.SupportConfig.load(self.root)
        self.assertTrue(config.can_send_reports)
        self.assertFalse(config.has_contact)  # invalid values are dropped
        (label, url, short), = config.report_links("A title", "body text")
        self.assertFalse(short)
        self.assertIn(urllib.parse.quote("body text"), url)

    def test_long_reports_fall_back_to_clipboard(self):
        (self.root / support.CONFIG_FILE).write_text(json.dumps({"report_email": "help@example.org"}))
        config = support.SupportConfig.load(self.root)
        (_label, url, short), = config.report_links("t", "x" * 5000)
        self.assertTrue(short)
        self.assertLess(len(url), support.MAX_MAILTO_LENGTH)

    def test_save_report(self):
        path = support.save_report(self.root / "reports", "T", "body\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "# T\n\nbody\n")


if __name__ == "__main__":
    unittest.main()
