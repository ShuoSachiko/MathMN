from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
REPO = ROOT.parents[1]
PROJECT_PYTHON = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
PYTHON = PROJECT_PYTHON if PROJECT_PYTHON.is_file() else Path(sys.executable)
PSO = ROOT / "scripts" / "pso_runner.py"
SELECTOR = ROOT / "scripts" / "algorithm_selector.py"
EXPERIMENT_RUNNER = ROOT / "scripts" / "experiment_runner.py"
AGGREGATOR = ROOT / "scripts" / "aggregate_experiments.py"
REGISTRY = ROOT / "assets" / "algorithm_registry.json"
PROFILES = ROOT / "assets" / "compute_profiles.json"


class AlgorithmLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run([str(PYTHON), str(script), *args], cwd=self.root,
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def test_pso_is_deterministic_and_improves_sphere(self) -> None:
        arguments = ("--benchmark", "sphere", "--dimension", "3", "--lower", "-5",
                     "--upper", "5", "--particles", "24", "--iterations", "80", "--seed", "7")
        self.run_cli(PSO, *arguments, "--output", "a.json")
        self.run_cli(PSO, *arguments, "--output", "b.json")
        a = json.loads((self.root / "a.json").read_text(encoding="utf-8"))
        b = json.loads((self.root / "b.json").read_text(encoding="utf-8"))
        self.assertEqual(a["best_position"], b["best_position"])
        self.assertEqual(a["objective"], b["objective"])
        self.assertLess(a["objective"], 1e-4)
        self.assertEqual(a["claim_strength"], "best-found-in-this-run")

    def test_selector_prefers_structural_methods_when_applicable(self) -> None:
        self.run_cli(SELECTOR, "--registry", str(REGISTRY), "--variable", "continuous",
                     "--objective", "single", "--constraints", "linear",
                     "--differentiable", "yes", "--convex", "yes",
                     "--evaluation-cost", "medium", "--output", "candidates.json")
        candidates = json.loads((self.root / "candidates.json").read_text(encoding="utf-8"))["candidates"]
        ids = [item["id"] for item in candidates]
        self.assertIn("lp-milp-cp", ids)
        self.assertIn("convex-optimization", ids)
        self.assertNotIn("particle-swarm", ids)

    def test_selector_emits_queries_for_evidence_search(self) -> None:
        self.run_cli(SELECTOR, "--registry", str(REGISTRY), "--variable", "continuous",
                     "--objective", "single", "--constraints", "nonlinear",
                     "--differentiable", "no", "--convex", "unknown",
                     "--evaluation-cost", "medium", "--domain", "3D coverage",
                     "--output", "search.json")
        document = json.loads((self.root / "search.json").read_text(encoding="utf-8"))
        self.assertTrue(document["research_queries"])
        self.assertTrue(all("3D coverage" in query for query in document["research_queries"]))

    def test_experiment_runner_records_fresh_artifact(self) -> None:
        script = self.root / "write_result.py"
        script.write_text(
            "from pathlib import Path\n"
            "Path('result.json').write_text('{\\\"ok\\\": true}', encoding='utf-8')\n",
            encoding="utf-8",
        )
        plan = {
            "profile": "weak-dev",
            "runs": [{"run_id": "smoke", "argv": ["python", "write_result.py"],
                      "expected_artifacts": ["result.json"]}],
        }
        (self.root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.run_cli(
            EXPERIMENT_RUNNER,
            "--project-root", ".",
            "--plan", "plan.json",
            "--profiles", str(PROFILES),
            "--output", "manifest.json",
        )
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "PASS")
        self.assertTrue(manifest["runs"][0]["artifacts"][0]["fresh"])

    def test_aggregate_reports_cross_seed_statistics(self) -> None:
        result_dir = self.root / "results"
        result_dir.mkdir()
        runs = []
        for seed, objective in enumerate((4.0, 1.0, 2.0), start=1):
            path = result_dir / f"pso-{seed}.json"
            path.write_text(json.dumps({"algorithm": "global-best-pso",
                                        "objective": objective, "violation": 0.0}), encoding="utf-8")
            runs.append({"run_id": f"pso-{seed}", "status": "PASS", "elapsed_seconds": seed,
                         "artifacts": [{"path": f"results/pso-{seed}.json", "fresh": True}]})
        (self.root / "manifest.json").write_text(json.dumps({"runs": runs}), encoding="utf-8")
        self.run_cli(AGGREGATOR, "--project-root", ".", "--manifest", "manifest.json",
                     "--output", "aggregate.json")
        summary = json.loads((self.root / "aggregate.json").read_text(encoding="utf-8"))["algorithms"][0]
        self.assertEqual(summary["objective_best"], 1.0)
        self.assertEqual(summary["objective_median"], 2.0)
        self.assertEqual(summary["feasible_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
