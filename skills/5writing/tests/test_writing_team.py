from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "writing_team.py"


class WritingTeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root / "results").mkdir(); (self.root / "reports" / "agents" / "results").mkdir(parents=True)
        (self.root / "results" / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
        (self.root / "reports" / "PAPER_TRACEABILITY.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None: self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, expected, completed.stderr); return completed

    def init(self, *extra: str) -> None:
        self.run_cli("init", "--project-root", ".", "--section",
                     "paper/sections/5_problem1.tex", *extra)

    def result(self, task_id: str, role: str, model: str, status: str = "PROPOSED") -> None:
        value = {"kind": "AGENT_RESULT", "task_id": task_id, "role": role,
                 "provider": "test", "model": model, "status": status}
        (self.root / "reports" / "agents" / "results" / f"{task_id}.json").write_text(
            json.dumps(value), encoding="utf-8")

    def test_requires_all_three_lanes(self) -> None:
        self.init()
        completed = self.run_cli("audit", "--project-root", ".", expected=1)
        self.assertIn("FAIL", completed.stdout)

    def test_accepts_separated_draft_and_reviews_for_merge(self) -> None:
        self.init(); prefix = "writing-5_problem1"
        self.result(prefix + "-draft", "section-drafter", "draft-model")
        self.result(prefix + "-equation-review", "equation-reviewer", "equation-model")
        self.result(prefix + "-evidence-review", "evidence-reviewer", "evidence-model")
        completed = self.run_cli("audit", "--project-root", ".")
        self.assertIn("READY_FOR_HUMAN_MERGE", completed.stdout)

    def test_upstream_change_invalidates_plan(self) -> None:
        self.init()
        (self.root / "results" / "claim_ledger.json").write_text('[{"id":"C1"}]\n', encoding="utf-8")
        completed = self.run_cli("audit", "--project-root", ".", expected=1)
        self.assertIn("FAIL", completed.stdout)

    def test_can_require_a_different_provider_or_model(self) -> None:
        self.init("--require-heterogeneous-review")
        prefix = "writing-5_problem1"
        self.result(prefix + "-draft", "section-drafter", "same-model")
        self.result(prefix + "-equation-review", "equation-reviewer", "same-model")
        self.result(prefix + "-evidence-review", "evidence-reviewer", "same-model")
        completed = self.run_cli("audit", "--project-root", ".", expected=1)
        self.assertIn("FAIL", completed.stdout)


if __name__ == "__main__": unittest.main()
