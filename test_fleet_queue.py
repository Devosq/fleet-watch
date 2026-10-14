#!/usr/bin/env python3
"""Tests for the fleet_queue check — gh calls are injected, nothing hits the network."""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fleet_watch as fw

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def beat(minutes_old, budget_pct=10):
    ts = (NOW - timedelta(minutes=minutes_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"ts": ts, "budget_pct": budget_pct})


def make_gh(heartbeat=None, hb_error=None, prs=None, decisions=None, list_error=None):
    """Fake gh runner keyed on the API path. Returns (code, stdout, stderr)."""

    def gh(args):
        path = args[0]
        if path.startswith("repos/Devosq/fleet-queue/contents/heartbeat/"):
            if hb_error:
                return 1, "", hb_error
            return 0, heartbeat, ""
        if list_error:
            return 1, "", list_error
        if path.startswith("search/issues"):
            return 0, json.dumps({"items": prs or []}), ""
        if path.startswith("repos/Devosq/fleet-queue/issues"):
            return 0, json.dumps(decisions or []), ""
        return 1, "", "unexpected path " + path

    return gh


class TestFleetQueue(unittest.TestCase):
    CFG = {
        "type": "fleet_queue",
        "name": "fleet queue",
        "repo": "Devosq/fleet-queue",
        "owner": "Devosq",
        "machines": ["vps2"],
    }

    def run_check(self, gh, cfg=None):
        return fw.check_fleet_queue(cfg or self.CFG, gh=gh, now=NOW)

    def test_fresh_beat_ok(self):
        res = self.run_check(make_gh(heartbeat=beat(5)))
        self.assertEqual(res.status, fw.OK)
        self.assertIn("vps2", res.detail)

    def test_stale_beat_fails(self):
        res = self.run_check(make_gh(heartbeat=beat(45)))
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("stale", res.detail)

    def test_missing_file_fails(self):
        res = self.run_check(make_gh(hb_error="gh: Not Found (HTTP 404)"))
        self.assertEqual(res.status, fw.FAIL)
        self.assertIn("missing", res.detail)

    def test_api_error_degrades_to_warn(self):
        res = self.run_check(make_gh(hb_error="gh: HTTP 502 Bad Gateway"))
        self.assertEqual(res.status, fw.WARN)

    def test_gh_raising_degrades_to_warn(self):
        def boom(args):
            raise OSError("gh exploded")

        res = self.run_check(boom)
        self.assertEqual(res.status, fw.WARN)

    def test_invalid_json_warns(self):
        res = self.run_check(make_gh(heartbeat="not json"))
        self.assertEqual(res.status, fw.WARN)

    def test_budget_warning(self):
        res = self.run_check(make_gh(heartbeat=beat(1, budget_pct=92)))
        self.assertEqual(res.status, fw.WARN)
        self.assertIn("budget", res.detail)

    def test_stale_beats_budget_warning(self):
        res = self.run_check(make_gh(heartbeat=beat(60, budget_pct=95)))
        self.assertEqual(res.status, fw.FAIL)

    def test_lists_prs_and_decisions(self):
        prs = [{"title": "Add thing", "html_url": "https://x/pr/1",
                "repository_url": "https://api.github.com/repos/Devosq/fleet-watch"}]
        decisions = [
            {"number": 7, "title": "Pick DB"},
            {"number": 8, "title": "a PR", "pull_request": {}},  # PRs are not decisions
        ]
        res = self.run_check(make_gh(heartbeat=beat(1), prs=prs, decisions=decisions))
        self.assertEqual(res.status, fw.OK)
        self.assertIn("1 open fleet PR", res.detail)
        self.assertIn("fleet-watch: Add thing", res.detail)
        self.assertIn("1 open decision", res.detail)
        self.assertIn("#7 Pick DB", res.detail)
        self.assertNotIn("#8", res.detail)

    def test_list_error_degrades_to_warn(self):
        res = self.run_check(make_gh(heartbeat=beat(1), list_error="gh: rate limited"))
        self.assertEqual(res.status, fw.WARN)

    def test_registered_and_crash_safe_in_report(self):
        self.assertIs(fw.CHECK_TYPES["fleet_queue"], fw.check_fleet_queue)

    def test_does_not_touch_report_path(self):
        # The check must never write a report file, so production
        # last-report.txt cannot be overwritten by tests.
        with patch.dict(os.environ, {"FLEET_REPORT_PATH": "/nonexistent/dir/x.txt"}):
            res = self.run_check(make_gh(heartbeat=beat(1)))
        self.assertEqual(res.status, fw.OK)


if __name__ == "__main__":
    unittest.main()
