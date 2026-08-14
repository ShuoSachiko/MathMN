from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "matlab_agentic_status.py"


class MatlabAgenticStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("matlab_agentic_status", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(cls.module)  # type: ignore[union-attr]

    def test_parse_probe(self) -> None:
        parsed = self.module.parse_probe(
            "noise\n__MATHMODEL_AGENTIC_STATUS__24.2|R2024b|1|0\n"
        )
        self.assertEqual(parsed["version"], "24.2")
        self.assertTrue(parsed["setup_function_available"])
        self.assertFalse(parsed["share_session_function_available"])

    def test_parse_probe_rejects_missing_marker(self) -> None:
        with self.assertRaises(ValueError):
            self.module.parse_probe("MATLAB output without marker")


if __name__ == "__main__":
    unittest.main()
