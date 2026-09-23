"""Audit log tests. No hardware needed."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from wristband import audit


class AuditTempFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "audit.jsonl"
        self.addCleanup(self.dir.cleanup)
        os.environ.pop(audit.ENV_DISABLE, None)


class TestRecord(AuditTempFile):
    def test_writes_one_json_object_per_line(self):
        audit.record("write-url", uid="04:01", ok=True, path=self.path)
        audit.record("lock", uid="04:01", ok=True, path=self.path)
        lines = self.path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        for line in lines:
            json.loads(line)  # each line stands alone

    def test_appends_rather_than_overwrites(self):
        for i in range(5):
            audit.record("write-url", uid=f"04:0{i}", path=self.path)
        self.assertEqual(len(audit.read(self.path)), 5)

    def test_entry_has_timestamp_and_action(self):
        entry = audit.record("write-url", uid="04:01", path=self.path)
        self.assertIn("ts", entry)
        self.assertTrue(entry["ts"].endswith("Z"), "timestamp must be UTC")
        self.assertEqual(entry["action"], "write-url")

    def test_details_are_kept(self):
        audit.record("write-url", uid="04:01", url="https://example.com",
                     bytes=43, path=self.path)
        entry = audit.read(self.path)[0]
        self.assertEqual(entry["url"], "https://example.com")
        self.assertEqual(entry["bytes"], 43)

    def test_failures_are_recorded_too(self):
        audit.record("write-url", uid="04:01", ok=False,
                     message="Write page 5 failed", path=self.path)
        entry = audit.read(self.path)[0]
        self.assertFalse(entry["ok"])
        self.assertIn("failed", entry["message"])

    def test_bytes_are_hex_encoded(self):
        audit.record("config", payload=b"\xde\xad", path=self.path)
        self.assertEqual(audit.read(self.path)[0]["payload"], "DEAD")

    def test_absurdly_long_values_are_truncated(self):
        audit.record("write-url", url="x" * 5000, path=self.path)
        self.assertLess(len(audit.read(self.path)[0]["url"]), 600)


class TestSecretsNeverLogged(AuditTempFile):
    """A tag's PWD cannot be read back, so a log holding it would be the only
    copy -- and a liability. It must never be written."""

    def test_password_is_redacted(self):
        audit.record("password", password="DEADBEEF", pack="1122", path=self.path)
        raw = self.path.read_text()
        self.assertNotIn("DEADBEEF", raw)
        self.assertNotIn("1122", raw)
        entry = audit.read(self.path)[0]
        self.assertEqual(entry["password"], "<redacted>")
        self.assertEqual(entry["pack"], "<redacted>")

    def test_redaction_is_case_insensitive(self):
        audit.record("password", PWD="CAFEBABE", Secret="x", path=self.path)
        self.assertNotIn("CAFEBABE", self.path.read_text())

    def test_non_secret_password_metadata_survives(self):
        audit.record("password", password="DEADBEEF", auth0=4,
                     protectReads=False, path=self.path)
        entry = audit.read(self.path)[0]
        self.assertEqual(entry["auth0"], 4)
        self.assertEqual(entry["password"], "<redacted>")


class TestRead(AuditTempFile):
    def setUp(self):
        super().setUp()
        audit.record("write-url", uid="04:AA", url="https://a", path=self.path)
        audit.record("lock", uid="04:AA", path=self.path)
        audit.record("write-url", uid="53:BB", url="https://b", path=self.path)

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(audit.read(Path(self.dir.name) / "nope.jsonl"), [])

    def test_filter_by_uid(self):
        self.assertEqual(len(audit.read(self.path, uid="04:AA")), 2)

    def test_uid_filter_ignores_case(self):
        self.assertEqual(len(audit.read(self.path, uid="04:aa")), 2)

    def test_filter_by_action(self):
        self.assertEqual(len(audit.read(self.path, action="lock")), 1)

    def test_limit_returns_the_newest(self):
        newest = audit.read(self.path, limit=1)
        self.assertEqual(newest[0]["uid"], "53:BB")

    def test_truncated_final_line_is_skipped(self):
        # An interrupted append can only damage the last line; the rest must
        # still be readable.
        with self.path.open("a") as fh:
            fh.write('{"ts":"2026-01-01T00:00:00Z","action":"wri')
        self.assertEqual(len(audit.read(self.path)), 3)


class TestDisabling(AuditTempFile):
    def test_env_var_disables_logging(self):
        os.environ[audit.ENV_DISABLE] = "1"
        self.addCleanup(os.environ.pop, audit.ENV_DISABLE, None)
        self.assertIsNone(audit.record("write-url", path=self.path))
        self.assertFalse(self.path.exists())


class TestNeverRaises(AuditTempFile):
    def test_unwritable_path_does_not_raise(self):
        # An audit log that breaks the tool it audits is worse than none.
        bad = Path("/proc/version/nope/audit.jsonl")
        entry = audit.record("write-url", uid="04:01", path=bad)
        self.assertIn("_log_error", entry)


class TestDefaultPath(unittest.TestCase):
    def test_env_override(self):
        os.environ[audit.ENV_PATH] = "/tmp/custom-audit.jsonl"
        self.addCleanup(os.environ.pop, audit.ENV_PATH, None)
        self.assertEqual(audit.default_path(), Path("/tmp/custom-audit.jsonl"))

    def test_default_is_outside_the_repo(self):
        os.environ.pop(audit.ENV_PATH, None)
        path = audit.default_path()
        self.assertNotIn("open-source-wristband", str(path))
        self.assertTrue(str(path).endswith("wristband/audit.jsonl"))


class TestSummarise(AuditTempFile):
    def test_includes_action_and_uid(self):
        entry = audit.record("write-url", uid="04:AA", url="https://a", path=self.path)
        line = audit.summarise(entry)
        self.assertIn("write-url", line)
        self.assertIn("04:AA", line)

    def test_failure_is_visible(self):
        entry = audit.record("write-url", uid="04:AA", ok=False,
                             message="boom", path=self.path)
        self.assertIn("FAIL", audit.summarise(entry))
