from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("task_versions.py")


class TaskVersionsCliTests(unittest.TestCase):
    def run_cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(item) for item in args)],
            text=True,
            capture_output=True,
            check=False,
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "reports").mkdir()
        problem_manifest = {
            "review_mode": "human-supervised",
            "root_hash": "a" * 64,
        }
        (self.root / "reports" / "PROBLEM_MANIFEST.json").write_text(
            json.dumps(problem_manifest), encoding="utf-8"
        )
        (self.root / "reports" / "HUMAN_REVIEW.json").write_text(
            json.dumps(
                {
                    "problem_root_hash": problem_manifest["root_hash"],
                    "checkpoints": [
                        {
                            "id": "model",
                            "status": "APPROVED",
                            "approval_id": "review:model:approval-1",
                            "reviewer_type": "human",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "work").mkdir()
        (self.root / "work" / "answer.txt").write_text("one\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def snapshot(self, *extra: object) -> dict[str, object]:
        result = self.run_cli(
            "snapshot",
            "--project-root",
            self.root,
            "--task-id",
            "task-a",
            "--branch",
            "main",
            "--actor",
            "tester",
            "--message",
            "snapshot",
            "--path",
            "result=work/answer.txt",
            *extra,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_snapshot_branch_diff_materialize_and_deduplicate(self) -> None:
        first = self.snapshot()
        (self.root / "work" / "answer.txt").write_text("two\n", encoding="utf-8")
        second = self.snapshot("--parent", first["version_hash"])
        branch = self.run_cli(
            "snapshot",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--branch",
            "alternative",
            "--parent",
            first["version_hash"],
            "--actor",
            "tester",
            "--message",
            "branch",
            "--input",
            "result=work/answer.txt",
        )
        self.assertEqual(branch.returncode, 0, branch.stderr)

        listed = self.run_cli("list", "--root", self.root, "--task-id", "task-a")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["count"], 3)
        objects = list((self.root / ".task_versions" / "objects").glob("*/*"))
        self.assertEqual(len(objects), 2)

        diff = self.run_cli(
            "diff",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--from-version",
            first["version_hash"],
            "--to-version",
            second["version_hash"],
        )
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertEqual(len(json.loads(diff.stdout)["changed"]), 1)

        destination = self.root / "restored"
        materialized = self.run_cli(
            "materialize",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--version",
            first["version_hash"],
            "--destination",
            destination,
        )
        self.assertEqual(materialized.returncode, 0, materialized.stderr)
        self.assertEqual(
            (destination / "work" / "answer.txt").read_text(encoding="utf-8"),
            "one\n",
        )
        refused = self.run_cli(
            "materialize",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--version",
            first["version_hash"],
            "--destination",
            destination,
        )
        self.assertNotEqual(refused.returncode, 0)
    def test_select_requires_review_reference_and_appends_decision(self) -> None:
        version = self.snapshot()
        refused = self.run_cli(
            "select",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--version",
            version["version_hash"],
            "--actor",
            "tester",
            "--message",
            "choose",
        )
        self.assertNotEqual(refused.returncode, 0)
        forged = self.run_cli(
            "select",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--version",
            version["version_hash"],
            "--actor",
            "tester",
            "--message",
            "choose",
            "--human-review-ref",
            "review:model:forged",
        )
        self.assertNotEqual(forged.returncode, 0)
        self.assertIn("APPROVED human checkpoint", forged.stderr)
        selected = self.run_cli(
            "select",
            "--root",
            self.root,
            "--task-id",
            "task-a",
            "--version",
            version["version_hash"],
            "--actor",
            "tester",
            "--message",
            "choose",
            "--human-review-ref",
            "review:model:approval-1",
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        summary = json.loads(selected.stdout)
        current = json.loads(
            (self.root / "reports" / "CURRENT_VERSIONS.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(current["selection_hash"], summary["selection_hash"])
        decisions = (
            self.root / "reports" / "VERSION_DECISIONS.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(decisions), 1)
        event = json.loads(decisions[0])
        self.assertEqual(event["selection_hash"], summary["selection_hash"])
        self.assertEqual(event["seq"], 1)
        self.assertEqual(event["action"], "select")
        self.assertEqual(event["previous_event_hash"], None)
        self.assertEqual(len(event["event_hash"]), 64)

    def test_rejects_escape_and_symbolic_link_when_supported(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            escaped = self.run_cli(
                "snapshot",
                "--root",
                self.root,
                "--task-id",
                "task-a",
                "--actor",
                "tester",
                "--message",
                "escape",
                "--path",
                f"result=../{outside.name}",
            )
            self.assertNotEqual(escaped.returncode, 0)
            self.assertIn("escapes project root", escaped.stderr)

            link = self.root / "linked.txt"
            try:
                os.symlink(outside, link)
            except OSError as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")
            linked = self.run_cli(
                "snapshot",
                "--root",
                self.root,
                "--task-id",
                "task-a",
                "--actor",
                "tester",
                "--message",
                "link",
                "--path",
                "result=linked.txt",
            )
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("symbolic links", linked.stderr)
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
