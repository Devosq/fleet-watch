#!/usr/bin/env python3
"""Tests for fleet_watch.py — all external calls (psql/ssh/http) are mocked."""

import json
import unittest
from unittest.mock import patch

import fleet_watch as fw


class TestPgCron(unittest.TestCase):
    CFG = {
        "type": "pg_cron",
        "name": "csuite pg_cron",
        "db_url_env": "X_DB",
        "expect_active": ["scout-job"],
        "stale_after_hours": {"scout-job": 24},
    }

    def test_all_ok(self):
        rows = [["scout-job", "t", "succeeded", "3600"]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.OK)

    def test_failed_last_run(self):
        rows = [["scout-job", "t", "failed", "3600"]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("FAILED", res.detail)

    def test_stale_job(self):
        rows = [["scout-job", "t", "succeeded", str(48 * 3600)]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("stale", res.detail)

    def test_expected_active_but_inactive(self):
        rows = [["scout-job", "f", "succeeded", "3600"]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("INACTIVE", res.detail)

    def test_expected_job_missing(self):
        with patch.object(fw, "run_psql", return_value=(True, [])):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("not found", res.detail)

    def test_inactive_unlisted_job_ignored(self):
        cfg = dict(self.CFG, expect_active=[], stale_after_hours={})
        rows = [["other-job", "f", "never", ""]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(cfg)
        self.assertEqual(res.status, fw.OK)

    def test_db_error(self):
        with patch.object(fw, "run_psql", return_value=(False, "env X_DB not set")):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)

    def test_unexpected_status_flagged(self):
        rows = [["scout-job", "t", "weird", "3600"]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("unexpected status", res.detail)

    def test_running_within_stale_window_ok(self):
        rows = [["scout-job", "t", "running", "3600"]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.OK)

    def test_hung_running_job_caught_by_staleness(self):
        rows = [["scout-job", "t", "running", str(48 * 3600)]]
        with patch.object(fw, "run_psql", return_value=(True, rows)):
            res = fw.check_pg_cron(self.CFG)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("stale", res.detail)


class TestFreshness(unittest.TestCase):
    CFG = {
        "type": "freshness",
        "name": "opportunities fresh",
        "db_url_env": "X_DB",
        "timestamp_sql": "SELECT max(created_at) FROM opportunities",
        "max_age_hours": 96,
    }

    def test_fresh(self):
        with patch.object(fw, "run_psql", return_value=(True, [["3600"]])):
            res = fw.check_freshness(self.CFG)
        self.assertEqual(res.status, fw.OK)

    def test_stale(self):
        with patch.object(fw, "run_psql", return_value=(True, [[str(200 * 3600)]])):
            res = fw.check_freshness(self.CFG)
        self.assertEqual(res.status, fw.FAIL)

    def test_null_timestamp(self):
        with patch.object(fw, "run_psql", return_value=(True, [[""]])):
            res = fw.check_freshness(self.CFG)
        self.assertEqual(res.status, fw.FAIL)

    def test_semicolon_rejected(self):
        cfg = dict(self.CFG, timestamp_sql="SELECT now(); DROP TABLE x")
        with patch.object(fw, "run_psql", return_value=(True, [["1"]])) as mock_psql:
            res = fw.check_freshness(cfg)
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("semicolon", res.detail)
        mock_psql.assert_not_called()

    def test_trailing_semicolon_allowed(self):
        cfg = dict(self.CFG, timestamp_sql="SELECT max(created_at) FROM opportunities;")
        with patch.object(fw, "run_psql", return_value=(True, [["3600"]])):
            res = fw.check_freshness(cfg)
        self.assertEqual(res.status, fw.OK)

    def test_future_timestamp_warns(self):
        with patch.object(fw, "run_psql", return_value=(True, [["-7200"]])):
            res = fw.check_freshness(self.CFG)
        self.assertEqual(res.status, fw.WARN)
        self.assertIn("FUTURE", res.detail)


class TestEmptyConfig(unittest.TestCase):
    def test_no_checks_is_error_not_ok(self):
        import os as _os
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump({"checks": []}, fh)
            path = fh.name
        try:
            with patch.object(fw.sys, "argv", ["fw", "--config", path, "--dry-run"]):
                code = fw.main()
            self.assertEqual(code, 2)
        finally:
            _os.unlink(path)


class TestTelegram(unittest.TestCase):
    def test_chunk_failure_includes_context(self):
        with patch.object(
            fw.urllib.request, "urlopen", side_effect=OSError("boom")
        ), self.assertRaises(RuntimeError) as ctx:
            fw.send_telegram("tok", "123", "hello")
        self.assertIn("chunk 1/1", str(ctx.exception))


class TestSshChecks(unittest.TestCase):
    def test_file_age_ok(self):
        cfg = {"name": "backup", "host": "myserver", "path": "/backup", "max_age_hours": 26}
        recent = fw.datetime.now(fw.timezone.utc).timestamp() - 3600
        with patch.object(fw, "run_cmd", return_value=(0, f"{recent}", "")):
            res = fw.check_ssh_file_age(cfg)
        self.assertEqual(res.status, fw.OK)

    def test_file_age_stale(self):
        cfg = {"name": "backup", "host": "myserver", "path": "/backup", "max_age_hours": 26}
        old = fw.datetime.now(fw.timezone.utc).timestamp() - 80 * 3600
        with patch.object(fw, "run_cmd", return_value=(0, f"{old}", "")):
            res = fw.check_ssh_file_age(cfg)
        self.assertEqual(res.status, fw.FAIL)

    def test_file_age_no_files(self):
        cfg = {"name": "backup", "host": "myserver", "path": "/backup", "max_age_hours": 26}
        with patch.object(fw, "run_cmd", return_value=(0, "", "")):
            res = fw.check_ssh_file_age(cfg)
        self.assertEqual(res.status, fw.FAIL)

    def test_ssh_ok_pass_and_fail(self):
        cfg = {"name": "ollama up", "host": "myserver", "command": "true"}
        with patch.object(fw, "run_cmd", return_value=(0, "running", "")):
            self.assertEqual(fw.check_ssh_ok(cfg).status, fw.OK)
        with patch.object(fw, "run_cmd", return_value=(1, "", "not running")):
            self.assertEqual(fw.check_ssh_ok(cfg).status, fw.FAIL)


class TestReportAndDispatch(unittest.TestCase):
    def test_report_flags_fails(self):
        results = [
            fw.Result("a", fw.OK, "fine"),
            fw.Result("b", fw.FAIL, "broken"),
        ]
        report = fw.build_report(results)
        self.assertIn("1 FAIL", report)
        self.assertIn("❌ b: broken", report)

    def test_report_all_ok(self):
        report = fw.build_report([fw.Result("a", fw.OK, "fine")])
        self.assertIn("kaikki OK", report)

    def test_unknown_type_and_disabled(self, tmp_path=None):
        config = {
            "checks": [
                {"type": "nope", "name": "x"},
                {"type": "http", "name": "y", "disabled": True},
            ]
        }
        import io
        import os as _os
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(config, fh)
            path = fh.name
        try:
            with (
                patch.object(fw.sys, "argv", ["fw", "--config", path, "--dry-run"]),
                patch.object(fw.sys, "stdout", io.StringIO()) as out,
            ):
                code = fw.main()
            self.assertEqual(code, 1)  # unknown type counts as FAIL
            self.assertIn("⏭", out.getvalue())  # disabled check is SKIP
        finally:
            _os.unlink(path)

    def test_run_psql_missing_env(self):
        with patch.dict(fw.os.environ, {}, clear=True):
            ok, err = fw.run_psql("MISSING_DB_URL", "SELECT 1")
        self.assertFalse(ok)
        self.assertIn("MISSING_DB_URL", err)


if __name__ == "__main__":
    unittest.main()
