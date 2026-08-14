from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "spatial_audit.py"


class SpatialAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, expected, completed.stderr)
        return completed

    def contract(self, dimension: int = 3) -> Path:
        axes = ["x", "y", "z"][:dimension]
        args = ["init-contract", "--output", "contract.json",
                "--coordinate-system", "cartesian", "--dimension", str(dimension)]
        for axis in axes:
            args.extend(["--axis", axis])
        args.extend(["--unit", "m", "--distance-metric", "euclidean"])
        self.run_cli(*args)
        return self.root / "contract.json"

    def test_three_dimensional_points_and_coverage(self) -> None:
        self.contract(3)
        (self.root / "demand.csv").write_text(
            "id,x,y,z,w\na,0,0,0,2\nb,3,4,0,1\n", encoding="utf-8")
        (self.root / "facility.csv").write_text(
            "id,x,y,z\nf,0,0,0\n", encoding="utf-8")
        self.run_cli("points", "--contract", "contract.json", "--csv", "demand.csv",
                     "--id", "id", "--coord", "x", "--coord", "y", "--coord", "z",
                     "--output", "points.json")
        self.run_cli("coverage", "--contract", "contract.json", "--demand", "demand.csv",
                     "--facility", "facility.csv", "--demand-id", "id", "--facility-id", "id",
                     "--coord", "x", "--coord", "y", "--coord", "z", "--radius", "4",
                     "--weight", "w", "--output", "coverage.json")
        report = json.loads((self.root / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(report["covered_count"], 1)
        self.assertAlmostEqual(report["weighted_coverage"], 2 / 3)
        self.assertEqual(report["scope"], "discrete demand points only")

    def test_distance_triangle_violation_fails(self) -> None:
        (self.root / "distance.csv").write_text(
            ",a,b,c\na,0,1,3\nb,1,0,1\nc,3,1,0\n", encoding="utf-8")
        self.run_cli("distance", "--csv", "distance.csv", "--labels",
                     "--output", "distance-report.json", expected=1)
        report = json.loads((self.root / "distance-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("TRIANGLE_INEQUALITY", {item["code"] for item in report["findings"]})

    def test_non_increasing_trajectory_fails(self) -> None:
        self.contract(2)
        (self.root / "track.csv").write_text(
            "id,t,x,y\na,1,0,0\na,1,1,0\n", encoding="utf-8")
        self.run_cli("trajectory", "--contract", "contract.json", "--csv", "track.csv",
                     "--id", "id", "--time", "t", "--coord", "x", "--coord", "y",
                     "--output", "track-report.json", expected=1)


if __name__ == "__main__":
    unittest.main()
