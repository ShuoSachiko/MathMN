"""Synthetic tests for skills/1start-mathmodel/scripts/time_tracker.py.

Uses fixed ISO deadlines far in the future/past so results do not depend on the
wall clock, and a workspace-local temp dir with tolerant cleanup.
"""

import importlib.util
import json
import os
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "time_tracker.py"


def load_module():
    spec = importlib.util.spec_from_file_location("time_tracker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker = load_module()

FAR_FUTURE = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
FAR_PAST = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")


class TimeTrackerTests(unittest.TestCase):
    def setUp(self):
        # Plain cwd-local dir: restricted sandboxes deny writes inside
        # tempfile-created directories, while the workspace root is writable.
        self._tmp = Path.cwd() / f".tmp-time-tracker-{os.getpid()}-{id(self)}"
        self.root = self._tmp / "project"
        self.root.mkdir(parents=True)
        self.state = self.root / "reports" / "TIME_BUDGET.json"

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def init(self, deadline=FAR_FUTURE, budget=72.0):
        return tracker.cmd_init(
            type("A", (), {
                "root": self.root, "deadline": deadline,
                "budget_hours": budget, "stage_budget": ["analysis=12", "coding=36"],
            })()
        )

    def mark(self, stage, spent, percent):
        return tracker.cmd_mark(
            type("A", (), {"root": self.root, "stage": stage, "spent_hours": spent,
                           "percent": percent, "note": ""})()
        )

    def status(self):
        return tracker.cmd_status(type("A", (), {"root": self.root})())

    def test_init_creates_state(self):
        self.assertEqual(self.init(), 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["budget_hours"], 72.0)
        self.assertEqual(sorted(state["stages"]), ["analysis", "coding"])

    def test_init_refuses_overwrite(self):
        self.init()
        self.assertEqual(self.init(), 2)

    def test_mark_unknown_stage_fails(self):
        self.init()
        self.assertEqual(self.mark("typo", 1.0, 10), 2)

    def test_on_schedule_status_is_clean(self):
        self.init()
        self.mark("analysis", 2.0, 20)  # 2/12h = 17% expected, 20% done
        self.assertEqual(self.status(), 0)

    def test_behind_stage_warns(self):
        self.init()
        self.mark("analysis", 6.0, 10)  # 50% of budget spent, only 10% done
        self.assertEqual(self.status(), 1)

    def test_over_budget_warns(self):
        self.init(budget=1.0)
        self.mark("analysis", 2.0, 100)
        self.assertEqual(self.status(), 1)

    def test_passed_deadline_warns(self):
        self.init(deadline=FAR_PAST)
        self.assertEqual(self.status(), 1)

    def test_missing_state_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self.status()
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
