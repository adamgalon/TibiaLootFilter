import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tibia_loot_manager import lootfile as LF

EXISTING = {"blacklistTypes": [2920, 236], "listType": "blacklist", "whitelistTypes": [3031], "futureKey": {"x": 1}}


class ValidateTest(unittest.TestCase):
    def test_known_format(self):
        self.assertEqual(LF.validate(EXISTING), [])

    def test_rejects_unknown_shapes(self):
        self.assertTrue(LF.validate({"listType": "everything", "blacklistTypes": [], "whitelistTypes": []}))
        self.assertTrue(LF.validate({"listType": "whitelist", "blacklistTypes": ["torch"], "whitelistTypes": []}))
        self.assertTrue(LF.validate({"listType": "whitelist", "whitelistTypes": []}))
        self.assertTrue(LF.validate([1, 2]))


class PlanTest(unittest.TestCase):
    def test_merge_keeps_existing_and_unknown_fields(self):
        plan = LF.plan_install(EXISTING, [236, 17829], LF.MERGE)
        self.assertEqual(plan.new_data["whitelistTypes"], [3031, 236, 17829])
        self.assertEqual(plan.new_data["listType"], "whitelist")
        self.assertEqual(plan.new_data["blacklistTypes"], [2920, 236])
        self.assertEqual(plan.new_data["futureKey"], {"x": 1})
        self.assertEqual((plan.added, plan.removed, plan.kept), ([236, 17829], [], [3031]))
        self.assertTrue(plan.mode_change)
        self.assertEqual(plan.also_skipped, [236])

    def test_replace_shows_removals(self):
        plan = LF.plan_install(EXISTING, [17829], LF.REPLACE)
        self.assertEqual(plan.new_data["whitelistTypes"], [17829])
        self.assertEqual(plan.removed, [3031])

    def test_new_file(self):
        plan = LF.plan_install(None, [5, 4], LF.MERGE)
        self.assertTrue(plan.creates_file)
        self.assertEqual(LF.validate(plan.new_data), [])


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.chardata = root / "characterdata"
        (self.chardata / "111").mkdir(parents=True)
        (self.chardata / "222").mkdir()
        (self.chardata / "333").mkdir()
        (self.chardata / "111" / LF.FILE_NAME).write_text(json.dumps(EXISTING, indent=4))
        (self.chardata / "333" / LF.FILE_NAME).write_text("{not json")
        (self.chardata / "111" / "other.json").write_text("untouched")
        self.backups = root / "backups"

    def tearDown(self):
        self.tmp.cleanup()

    def chars(self):
        return {c.folder_id: c for c in LF.list_characters(self.chardata)}

    def test_detect_format(self):
        report = LF.detect_format(list(self.chars().values()))
        self.assertTrue(report.validated)
        self.assertEqual(report.extra_keys, {"futureKey"})
        self.assertIsNotNone(self.chars()["333"].error)
        self.assertFalse(LF.detect_format([]).validated)

    def test_install_backup_verify_restore(self):
        folder = self.chars()["111"]
        plan = LF.plan_install(folder.data, [17829], LF.MERGE)
        result = LF.install(plan, folder, self.backups)
        self.assertEqual(LF.read_file(folder.file_path), plan.new_data)
        self.assertEqual(json.loads(result.backup.read_text()), EXISTING)
        self.assertEqual((self.chardata / "111" / "other.json").read_text(), "untouched")
        self.assertEqual(sorted(p.name for p in (self.chardata / "111").iterdir()), [LF.FILE_NAME, "other.json"])

        LF.restore_backup(result.backup, self.chars()["111"], self.backups)
        self.assertEqual(LF.read_file(folder.file_path), EXISTING)
        self.assertEqual(len(LF.list_backups(self.backups, "111")), 2)  # original + pre-restore safety copy

    def test_new_file_without_backup(self):
        folder = self.chars()["222"]
        result = LF.install(LF.plan_install(None, [1], LF.REPLACE), folder, self.backups)
        self.assertIsNone(result.backup)
        self.assertEqual(LF.read_file(folder.file_path)["whitelistTypes"], [1])

    def test_failed_new_file_is_removed(self):
        folder = self.chars()["222"]
        plan = LF.plan_install(None, [1], LF.REPLACE)

        def broken(file_path, data, staging):
            file_path.write_text("{half")
            raise OSError("disk full")
        with mock.patch.object(LF, "_write_verified", broken):
            with self.assertRaises(LF.LootFileError):
                LF.install(plan, folder, self.backups)
        self.assertFalse(folder.file_path.exists())

    def test_refuses_if_file_changed_since_preview(self):
        folder = self.chars()["111"]
        plan = LF.plan_install(folder.data, [1], LF.MERGE)
        folder.file_path.write_text(json.dumps({**EXISTING, "whitelistTypes": [3031, 99]}))
        with self.assertRaises(LF.LootFileError):
            LF.install(plan, folder, self.backups)
        self.assertEqual(LF.list_backups(self.backups, "111"), [])


if __name__ == "__main__":
    unittest.main()
