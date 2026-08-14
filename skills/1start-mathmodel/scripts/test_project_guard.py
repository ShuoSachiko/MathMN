from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("project_guard.py")
OUTPUT_NAMES = (
    "PROBLEM_MANIFEST.json",
    "PROVENANCE.md",
    "DECISION_LOG.md",
    "HANDOFF.json",
    "STAGE_GATES.json",
    "HUMAN_REVIEW.json",
)


class ProjectGuardCliTests(unittest.TestCase):
    def run_cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
            text=True,
            capture_output=True,
            check=False,
        )

    def init_args(self, root: Path) -> tuple[object, ...]:
        return (
            "init",
            "--project-root",
            root,
            "--project-id",
            "test-project",
            "--mode",
            "live-competition",
            "--task-provenance",
            "user-provided",
            "--runtime-isolation",
            "declared-only",
        )

    def test_init_verify_and_detect_change_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "problem.txt").write_text("source\n", encoding="utf-8")
            (inputs / "table.bin").write_bytes(b"\x00\x01")

            declarations = (
                "--input",
                "problem=inputs/problem.txt",
                "--input",
                "attachment=inputs/table.bin",
            )
            initialized = self.run_cli(*self.init_args(root), *declarations)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            for name in OUTPUT_NAMES:
                self.assertTrue((root / "reports" / name).is_file(), name)

            manifest = json.loads(
                (root / "reports" / "PROBLEM_MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["review_mode"], "human-supervised")
            self.assertEqual(
                [item["role"] for item in manifest["inputs"]],
                ["attachment", "problem"],
            )
            before = hashlib.sha256(
                (root / "reports" / "DECISION_LOG.md").read_bytes()
            ).hexdigest()
            refused = self.run_cli(*self.init_args(root), *declarations)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(
                before,
                hashlib.sha256(
                    (root / "reports" / "DECISION_LOG.md").read_bytes()
                ).hexdigest(),
            )

            verified = self.run_cli(
                "verify", "--project-root", root, *declarations
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            marked = self.run_cli(
                "mark-verified",
                "--project-root",
                root,
                "--input",
                "problem=inputs/problem.txt",
                "--actor",
                "human-reviewer",
                "--note",
                "formula and units checked",
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)
            updated = json.loads(
                (root / "reports" / "PROBLEM_MANIFEST.json").read_text(
                    encoding="utf-8"
                )
            )
            problem = next(item for item in updated["inputs"] if item["role"] == "problem")
            self.assertEqual(problem["verification"], "verified")
            human_review = json.loads(
                (root / "reports" / "HUMAN_REVIEW.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["status"] for item in human_review["checkpoints"]],
                ["PENDING"] * 7,
            )
            gates = json.loads(
                (root / "reports" / "STAGE_GATES.json").read_text(encoding="utf-8")
            )
            required = {item["id"]: item["required"] for item in gates["stages"]}
            self.assertFalse(required["literature"])
            self.assertFalse(required["drawing"])
            self.assertTrue(required["analysis"])
            verified_after_mark = self.run_cli("verify", "--root", root)
            self.assertEqual(
                verified_after_mark.returncode, 0, verified_after_mark.stderr
            )
            (inputs / "problem.txt").write_text("changed\n", encoding="utf-8")
            changed = self.run_cli("verify", "--root", root)
            self.assertNotEqual(changed.returncode, 0)

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "project"
            root.mkdir()
            (parent / "outside.txt").write_text("outside", encoding="utf-8")
            result = self.run_cli(
                *self.init_args(root),
                "--output-dir",
                "nested",
                "--input",
                "problem=../outside.txt",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("escapes project root", result.stderr)
            self.assertFalse((root / "nested").exists())

    def test_rejects_symbolic_link_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            link = root / "link.txt"
            target.write_text("source", encoding="utf-8")
            try:
                os.symlink(target, link)
            except OSError as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")
            result = self.run_cli(
                *self.init_args(root), "--input", "problem=link.txt"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symbolic links", result.stderr)

    def test_live_competition_rejects_autonomous_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "problem.txt").write_text("source", encoding="utf-8")
            result = self.run_cli(
                *self.init_args(root),
                "--review-mode",
                "autonomous-simulation",
                "--input",
                "problem=problem.txt",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("human-supervised", result.stderr)

    def test_human_review_is_explicit_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "problem.txt").write_text("source", encoding="utf-8")
            initialized = self.run_cli(
                *self.init_args(root), "--input", "problem=problem.txt"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            reviewed = self.run_cli(
                "review",
                "--root",
                root,
                "--checkpoint",
                "intake",
                "--status",
                "APPROVED",
                "--reviewer",
                "team-member-1",
                "--reviewer-type",
                "human",
                "--source-id",
                "meeting-note-001",
                "--scope",
                "problem statement, formulas, units and attachment list",
                "--comments",
                "checked against every original page",
                "--evidence",
                "reports/PROBLEM_MANIFEST.json",
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            document = json.loads(
                (root / "reports" / "HUMAN_REVIEW.json").read_text(encoding="utf-8")
            )
            intake = document["checkpoints"][0]
            self.assertEqual(intake["status"], "APPROVED")
            self.assertEqual(intake["reviewer_type"], "human")
            self.assertEqual(intake["reviewer"], "team-member-1")
            self.assertEqual(document["authorship"]["type"], "human")
            self.assertFalse(document["authorship"]["agent_generated"])
            repeated = self.run_cli(
                "review",
                "--root",
                root,
                "--checkpoint",
                "intake",
                "--status",
                "CHANGES_REQUESTED",
                "--reviewer",
                "team-member-2",
                "--reviewer-type",
                "human",
                "--source-id",
                "meeting-note-002",
                "--scope",
                "same checkpoint",
                "--comments",
                "attempted overwrite",
                "--evidence",
                "reports/PROBLEM_MANIFEST.json",
            )
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("immutable", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
