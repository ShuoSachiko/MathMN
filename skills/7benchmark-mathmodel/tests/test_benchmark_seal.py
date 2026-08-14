from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_seal.py"
SPEC = importlib.util.spec_from_file_location("benchmark_seal", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import contract
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
benchmark_seal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark_seal)


class BenchmarkSealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "staging"
        self.output = self.root / "judge-owned"
        self.source.mkdir()
        self.output.mkdir()
        (self.source / "code").mkdir()
        (self.source / "reports").mkdir()
        (self.source / "code" / "main.txt").write_text(
            "deterministic payload\n", encoding="utf-8"
        )
        (self.source / "reports" / "final.md").write_text(
            "# Final\n\nResult record.\n", encoding="utf-8"
        )
        self.members = ["code/main.txt", "reports/final.md"]
        self.allowlist = self.root / "allowlist.json"
        self.archive = self.output / "submission.zip"
        self.manifest = self.output / "submission.manifest.json"
        self._write_allowlist(self.members)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_allowlist(self, members: list[str], path: Path | None = None) -> Path:
        target = path or self.allowlist
        target.write_text(
            json.dumps(
                {
                    "schema": benchmark_seal.ALLOWLIST_SCHEMA,
                    "files": members,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return target

    def _seal(
        self,
        *,
        source: Path | None = None,
        allowlist: Path | None = None,
        archive: Path | None = None,
        manifest: Path | None = None,
        human_review_status: str = "not-reviewed",
        human_review_record_id: str | None = None,
    ) -> dict[str, object]:
        return benchmark_seal.seal(
            source=source or self.source,
            allowlist=allowlist or self.allowlist,
            archive=archive or self.archive,
            manifest=manifest or self.manifest,
            human_review_status=human_review_status,
            human_review_record_id=human_review_record_id,
        )

    def test_seal_and_verify_archive_and_source(self) -> None:
        result = self._seal()
        verified = benchmark_seal.verify(
            archive=self.archive,
            manifest=self.manifest,
            expected_root_hash=str(result["root_hash"]),
            source=self.source,
        )

        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["root_hash"], result["root_hash"])
        self.assertTrue(self.archive.is_file())
        self.assertTrue(self.manifest.is_file())

    def test_outputs_are_canonical_and_deterministic(self) -> None:
        first = self._seal()
        second_source = self.root / "staging-two"
        second_source.mkdir()
        (second_source / "code").mkdir()
        (second_source / "reports").mkdir()
        (second_source / "code" / "main.txt").write_bytes(
            (self.source / "code" / "main.txt").read_bytes()
        )
        (second_source / "reports" / "final.md").write_bytes(
            (self.source / "reports" / "final.md").read_bytes()
        )
        second_allowlist = self._write_allowlist(
            list(reversed(self.members)), self.root / "allowlist-two.json"
        )
        second_archive = self.output / "submission-two.zip"
        second_manifest = self.output / "submission-two.manifest.json"
        second = self._seal(
            source=second_source,
            allowlist=second_allowlist,
            archive=second_archive,
            manifest=second_manifest,
        )

        self.assertEqual(first["root_hash"], second["root_hash"])
        self.assertEqual(self.manifest.read_bytes(), second_manifest.read_bytes())
        self.assertEqual(self.archive.read_bytes(), second_archive.read_bytes())
        raw_manifest = self.manifest.read_bytes()
        self.assertTrue(raw_manifest.endswith(b"\n"))
        self.assertEqual(raw_manifest.count(b"\n"), 1)

    def test_verify_detects_changed_source_bytes(self) -> None:
        result = self._seal()
        (self.source / "code" / "main.txt").write_text("changed\n", encoding="utf-8")

        with self.assertRaisesRegex(benchmark_seal.SealError, "changed after sealing"):
            benchmark_seal.verify(
                archive=self.archive,
                manifest=self.manifest,
                expected_root_hash=str(result["root_hash"]),
                source=self.source,
            )

    def test_verify_detects_added_source_file(self) -> None:
        result = self._seal()
        (self.source / "unsealed.txt").write_text("unexpected", encoding="utf-8")

        with self.assertRaisesRegex(benchmark_seal.SealError, "unexpected=unsealed.txt"):
            benchmark_seal.verify(
                archive=self.archive,
                manifest=self.manifest,
                expected_root_hash=str(result["root_hash"]),
                source=self.source,
            )

    def test_verify_detects_archive_change(self) -> None:
        result = self._seal()
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.writestr("extra.txt", b"tamper")

        with self.assertRaisesRegex(benchmark_seal.SealError, "members do not exactly match"):
            benchmark_seal.verify(
                archive=self.archive,
                manifest=self.manifest,
                expected_root_hash=str(result["root_hash"]),
            )

    def test_verify_requires_externally_trusted_root_hash(self) -> None:
        self._seal()
        with self.assertRaisesRegex(benchmark_seal.SealError, "externally trusted"):
            benchmark_seal.verify(
                archive=self.archive,
                manifest=self.manifest,
                expected_root_hash="0" * 64,
            )

    def test_human_review_state_is_bound_to_root_hash(self) -> None:
        unreviewed = self._seal()
        reviewed = self._seal(
            archive=self.output / "reviewed.zip",
            manifest=self.output / "reviewed.manifest.json",
            human_review_status="human-reviewed",
            human_review_record_id="review-ledger:entry-1",
        )

        self.assertNotEqual(unreviewed["root_hash"], reviewed["root_hash"])
        self.assertEqual(
            reviewed["human_review"],
            {
                "record_id": "review-ledger:entry-1",
                "status": "human-reviewed",
            },
        )

    def test_simulation_waiver_cannot_claim_human_review_record(self) -> None:
        with self.assertRaisesRegex(benchmark_seal.SealError, "must not carry"):
            self._seal(
                human_review_status="simulation-waived",
                human_review_record_id="not-a-real-approval",
            )

    def test_human_review_requires_external_record(self) -> None:
        with self.assertRaisesRegex(benchmark_seal.SealError, "requires an external"):
            self._seal(human_review_status="human-reviewed")

    def test_human_review_record_rejects_surrounding_whitespace(self) -> None:
        with self.assertRaisesRegex(benchmark_seal.SealError, "surrounding whitespace"):
            self._seal(
                human_review_status="human-reviewed",
                human_review_record_id=" review-ledger:entry-1 ",
            )

    def test_manifest_tampering_is_rejected(self) -> None:
        result = self._seal()
        document = json.loads(self.manifest.read_text(encoding="utf-8"))
        document["total_bytes"] += 1
        self.manifest.write_bytes(benchmark_seal._canonical_json(document))

        with self.assertRaisesRegex(benchmark_seal.SealError, "total_bytes"):
            benchmark_seal.verify(
                archive=self.archive,
                manifest=self.manifest,
                expected_root_hash=str(result["root_hash"]),
            )

    def test_seal_rejects_unlisted_source_file(self) -> None:
        (self.source / "not-listed.txt").write_text("extra", encoding="utf-8")

        with self.assertRaisesRegex(benchmark_seal.SealError, "unexpected=not-listed.txt"):
            self._seal()

    def test_allowlist_rejects_escape_absolute_and_backslash_paths(self) -> None:
        bad_members = [
            "../outside.txt",
            "/absolute.txt",
            "code\\main.txt",
            "C:/drive.txt",
        ]
        for index, member in enumerate(bad_members):
            with self.subTest(member=member):
                bad_allowlist = self._write_allowlist(
                    [member], self.root / f"bad-{index}.json"
                )
                with self.assertRaises(benchmark_seal.SealError):
                    self._seal(allowlist=bad_allowlist)

    def test_allowlist_rejects_case_collisions(self) -> None:
        bad_allowlist = self._write_allowlist(
            ["code/main.txt", "CODE/MAIN.TXT"], self.root / "collision.json"
        )
        with self.assertRaisesRegex(benchmark_seal.SealError, "case-colliding"):
            self._seal(allowlist=bad_allowlist)

    def test_seal_outputs_must_be_outside_source(self) -> None:
        with self.assertRaisesRegex(benchmark_seal.SealError, "outside the source root"):
            self._seal(archive=self.source / "submission.zip")

    def test_seal_outputs_must_share_trusted_parent(self) -> None:
        other_output = self.root / "other-output"
        other_output.mkdir()
        with self.assertRaisesRegex(benchmark_seal.SealError, "share one trusted parent"):
            self._seal(manifest=other_output / "submission.manifest.json")

    def test_second_publication_failure_rolls_back_archive(self) -> None:
        original = benchmark_seal._publish_new
        calls = 0

        def fail_second(temp_path: Path, final_path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise benchmark_seal.SealError("injected publication failure")
            original(temp_path, final_path)

        with mock.patch.object(benchmark_seal, "_publish_new", side_effect=fail_second):
            with self.assertRaisesRegex(
                benchmark_seal.SealError, "injected publication failure"
            ):
                self._seal()

        self.assertFalse(self.archive.exists())
        self.assertFalse(self.manifest.exists())

    def test_seal_refuses_to_overwrite_outputs(self) -> None:
        self._seal()
        with self.assertRaisesRegex(benchmark_seal.SealError, "refusing to overwrite"):
            self._seal()

    def test_symbolic_link_in_source_is_rejected(self) -> None:
        external = self.root / "external.txt"
        external.write_text("outside", encoding="utf-8")
        link = self.source / "linked.txt"
        try:
            os.symlink(external, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")

        with self.assertRaisesRegex(
            benchmark_seal.SealError, "symbolic link or reparse file"
        ):
            self._seal()

    def test_cli_returns_nonzero_on_policy_violation(self) -> None:
        (self.source / "extra.txt").write_text("extra", encoding="utf-8")
        exit_code = benchmark_seal.main(
            [
                "seal",
                "--source",
                str(self.source),
                "--allowlist",
                str(self.allowlist),
                "--archive",
                str(self.archive),
                "--manifest",
                str(self.manifest),
                "--human-review-status",
                "not-reviewed",
            ]
        )
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
