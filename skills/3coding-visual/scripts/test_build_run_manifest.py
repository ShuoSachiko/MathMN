from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("build_run_manifest.py")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class BuildRunManifestCliTests(unittest.TestCase):
    def run_cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sorted_manifest_stable_hash_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = root / "code"
            results = root / "results"
            code.mkdir()
            results.mkdir()
            (code / "z.py").write_text("z\n", encoding="utf-8")
            (code / "a.py").write_text("a\n", encoding="utf-8")
            (results / "metrics.json").write_text("{}\n", encoding="utf-8")

            first = self.run_cli(
                "--project-root",
                root,
                "--artifact",
                "results=results",
                "--input",
                "code=code",
                "--command",
                "python code/a.py",
                "--command",
                "python code/z.py",
                "--runtime",
                "Python test-runtime",
                "--output",
                "run-one.json",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            manifest_one = json.loads(
                (root / "run-one.json").read_text(encoding="utf-8")
            )
            keys = [(item["role"], item["path"]) for item in manifest_one["files"]]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual(
                manifest_one["root_hash"],
                hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "commands": manifest_one["commands"],
                            "files": manifest_one["files"],
                            "runtime": manifest_one["runtime"],
                            "sources": manifest_one["sources"],
                        }
                    )
                ).hexdigest(),
            )
            self.assertEqual(
                manifest_one["commands"],
                ["python code/a.py", "python code/z.py"],
            )
            self.assertEqual(manifest_one["runtime"], "Python test-runtime")

            second = self.run_cli(
                "--root",
                root,
                "--input",
                "code=code",
                "--input",
                "results=results",
                "--command",
                "python code/a.py",
                "--command",
                "python code/z.py",
                "--runtime",
                "Python test-runtime",
                "--output",
                "run-two.json",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            manifest_two = json.loads(
                (root / "run-two.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest_one["root_hash"], manifest_two["root_hash"])
            self.assertEqual(manifest_one["files"], manifest_two["files"])

            original = (root / "run-one.json").read_bytes()
            refused = self.run_cli(
                "--root",
                root,
                "--input",
                "code=code",
                "--command",
                "python code/a.py",
                "--command",
                "python code/z.py",
                "--runtime",
                "Python test-runtime",
                "--output",
                "run-one.json",
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(original, (root / "run-one.json").read_bytes())

    def test_rejects_escape_and_excludes_output_inside_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "project"
            root.mkdir()
            (parent / "outside.txt").write_text("outside", encoding="utf-8")
            escaped = self.run_cli(
                "--root",
                root,
                "--input",
                "result=../outside.txt",
                "--output",
                "nested/RUN_MANIFEST.json",
            )
            self.assertNotEqual(escaped.returncode, 0)
            self.assertIn("escapes project root", escaped.stderr)
            self.assertFalse((root / "nested").exists())

            data = root / "data"
            data.mkdir()
            (data / "value.txt").write_text("1", encoding="utf-8")
            recursive = self.run_cli(
                "--project-root",
                root,
                "--artifact",
                "result=data",
                "--output",
                "data/RUN_MANIFEST.json",
            )
            self.assertEqual(recursive.returncode, 0, recursive.stderr)
            manifest = json.loads(
                (data / "RUN_MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["path"] for item in manifest["files"]], ["data/value.txt"]
            )

    def test_rejects_symbolic_link_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            link = root / "link.txt"
            target.write_text("result", encoding="utf-8")
            try:
                os.symlink(target, link)
            except OSError as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")
            result = self.run_cli(
                "--root", root, "--input", "result=link.txt"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symbolic links", result.stderr)


if __name__ == "__main__":
    unittest.main()
