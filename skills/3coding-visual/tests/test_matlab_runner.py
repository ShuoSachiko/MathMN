from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "matlab_runner.py"
SPEC = importlib.util.spec_from_file_location("matlab_runner", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class MatlabRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "code" / "main.m"
        self.script.parent.mkdir()
        self.script.write_text("disp('test')\n", encoding="utf-8")
        self.runtime = ("matlab", self.root / "fake-matlab")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def invoke(
        self,
        completed: subprocess.CompletedProcess[str] | None = None,
        extra: list[str] | None = None,
        side_effect=None,
    ) -> tuple[int, str, str]:
        argv = [
            "matlab_runner.py",
            str(self.script),
            "--project-root",
            str(self.root),
            *(extra or []),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(runner, "find_runtime", return_value=self.runtime),
            mock.patch.object(
                runner,
                "run_script",
                return_value=completed,
                side_effect=side_effect,
            ),
            redirect_stdout(StringIO()) as stdout,
            redirect_stderr(StringIO()) as stderr,
        ):
            code = runner.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_nonzero_exit_is_failure_even_with_marker(self) -> None:
        completed = subprocess.CompletedProcess(
            [], 1, runner.MATLAB_COMPLETION_MARKER + "\n", "shutdown failed"
        )
        code, _, stderr = self.invoke(completed)
        self.assertEqual(code, 1)
        self.assertIn("runtime exited with code 1", stderr)

    def test_missing_marker_is_failure(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "RESULT score=1\n", "")
        code, _, stderr = self.invoke(completed)
        self.assertEqual(code, 1)
        self.assertIn("completion marker count is 0", stderr)

    def test_result_and_fresh_artifact_can_be_required(self) -> None:
        artifact = self.root / "results" / "value.json"

        def produce(**_kwargs):
            artifact.parent.mkdir()
            artifact.write_text('{"value": 1}\n', encoding="utf-8")
            return subprocess.CompletedProcess(
                [],
                0,
                "RESULT score=1\n" + runner.MATLAB_COMPLETION_MARKER + "\n",
                "",
            )

        code, _, stderr = self.invoke(
            extra=[
                "--require-result",
                "--expected-artifact",
                str(artifact),
            ],
            side_effect=produce,
        )
        self.assertEqual(code, 0, stderr)

    def test_timeout_keeps_partial_log(self) -> None:
        log = self.root / "code" / "outputs" / "run.log"
        timeout = subprocess.TimeoutExpired(
            cmd=["fake"],
            timeout=1,
            output="partial stdout",
            stderr="partial stderr",
        )
        code, _, _ = self.invoke(extra=["--log", str(log)], side_effect=timeout)
        self.assertEqual(code, 124)
        self.assertIn("partial stdout", log.read_text(encoding="utf-8"))
        self.assertIn("partial stderr", log.read_text(encoding="utf-8"))

    def test_path_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside.json"
        completed = subprocess.CompletedProcess(
            [], 0, runner.MATLAB_COMPLETION_MARKER + "\n", ""
        )
        code, _, stderr = self.invoke(
            completed,
            extra=["--expected-artifact", str(outside)],
        )
        self.assertEqual(code, 2)
        self.assertIn("escapes --project-root", stderr)


if __name__ == "__main__":
    unittest.main()
