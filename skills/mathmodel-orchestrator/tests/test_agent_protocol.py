from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "agent_protocol.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AgentProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        reports = self.root / "reports"
        reports.mkdir()
        self.contract = reports / "PROBLEM_CONTRACT.json"
        self.contract.write_text('{"requirements":["REQ-1"]}\n', encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "project_id": "synthetic-spatial",
            "root_hash": "a" * 64,
            "mode": "isolated-benchmark",
            "review_mode": "autonomous-simulation",
        }
        (reports / "PROBLEM_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.root,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, expected, completed.stderr)
        return completed

    def issue(self, task_id: str = "REQ-1-model-a") -> Path:
        self.run_cli("init", "--project-root", ".")
        self.run_cli(
            "issue", "--project-root", ".", "--task-id", task_id,
            "--role", "modeler-independent", "--stage", "analysis",
            "--objective", "Propose an independently checkable model",
            "--req-id", "REQ-1", "--input", "contract=reports/PROBLEM_CONTRACT.json",
            "--provider", "openai", "--model", "test-model",
        )
        return self.root / "reports" / "agents" / "packets" / f"{task_id}.json"

    def make_result(self, packet: Path, status: str = "PROPOSED") -> Path:
        result = self.root / "candidate.json"
        result.write_text(json.dumps({
            "schema_version": 1, "task_id": "REQ-1-model-a",
            "packet_sha256": sha256(packet), "role": "modeler-independent",
            "provider": "openai", "model": "test-model", "status": status,
            "summary": "Synthetic candidate only.", "req_ids": ["REQ-1"],
            "claims": [{"claim_id": "C1", "text": "The candidate is testable.",
                        "evidence": ["reports/PROBLEM_CONTRACT.json#REQ-1"],
                        "confidence": 0.5, "limitations": ["Not executed"]}],
            "artifacts": [], "open_questions": [],
            "recommended_checks": ["Run a synthetic instance"],
        }), encoding="utf-8")
        return result

    def test_issue_submit_verify(self) -> None:
        packet = self.issue()
        result = self.make_result(packet)
        self.run_cli("submit", "--project-root", ".", "--packet",
                     str(packet.relative_to(self.root)), "--result", result.name)
        completed = self.run_cli("verify", "--project-root", ".")
        self.assertIn("PASS", completed.stdout)

    def test_forbids_agent_pass(self) -> None:
        packet = self.issue()
        result = self.make_result(packet, "PASS")
        completed = self.run_cli("submit", "--project-root", ".", "--packet",
                                 str(packet.relative_to(self.root)), "--result",
                                 result.name, expected=2)
        self.assertIn("cannot approve", completed.stderr)

    def test_changed_input_invalidates_packet(self) -> None:
        self.issue()
        self.contract.write_text('{"requirements":["REQ-1","REQ-2"]}\n', encoding="utf-8")
        completed = self.run_cli("verify", "--project-root", ".", expected=2)
        self.assertIn("packet input changed", completed.stderr)


class ExternalReviewerHelpersTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location(
            "external_reviewer", SCRIPT.parent / "external_reviewer.py")
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def test_endpoint_normalization(self) -> None:
        module = self.load_module()
        self.assertEqual(module.endpoint("https://api.deepseek.com"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(module.endpoint("https://example.test/v1"),
                         "https://example.test/v1/chat/completions")

    def test_load_packet_documents_rejects_changed_input(self) -> None:
        module = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "contract.md"
            source.write_text("frozen\n", encoding="utf-8")
            packet = {"inputs": [{"name": "contract", "path": "contract.md",
                                   "sha256": sha256(source)}]}
            documents = module.load_packet_documents(root, packet, 100)
            self.assertEqual(documents[0]["content"].strip(), "frozen")
            source.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "packet input changed"):
                module.load_packet_documents(root, packet, 100)


if __name__ == "__main__":
    unittest.main()
