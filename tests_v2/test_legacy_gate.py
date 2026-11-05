"""The v1 path stays archived: no legacy CLI shim may return to main.

Before the archive this gate ran the retired CLIs and asserted that they
refused to work without ``--legacy-retrospective``.  The shims now live on
``archive/v1``, so the equivalent invariant is structural: they must not exist
on main, the archive pointer must be present, and the content-addressed
manifest must be internally consistent and the only file left in ``data/``.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LegacyGateTest(unittest.TestCase):
    def test_retired_cli_shims_are_not_on_main(self):
        for script in ("backtest_engine.py", "backtest_rules.py", "run_pipeline.py"):
            self.assertFalse((ROOT / script).exists(),
                             f"{script} belongs on archive/v1, not main")
        for directory in ("legacy", "tests"):
            self.assertFalse((ROOT / directory).exists(),
                             f"{directory}/ belongs on archive/v1, not main")

    def test_archive_pointer_and_adr_are_present(self):
        archive = (ROOT / "ARCHIVE.md").read_text(encoding="utf-8")
        self.assertIn("archive/v1", archive)
        self.assertIn("data/manifest.json", archive)
        self.assertTrue((ROOT / "docs" / "adr" /
                         "0002-no-git-history-rewrite.md").is_file())

    def test_artifact_manifest_is_internally_consistent(self):
        manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["kind"], "archived-artifact-manifest")
        self.assertEqual(manifest["archive_ref"], "refs/heads/archive/v1")
        entries = manifest["files"]
        self.assertEqual(manifest["file_count"], len(entries))
        self.assertEqual(manifest["total_bytes"], sum(e["bytes"] for e in entries))
        paths = [e["path"] for e in entries]
        self.assertEqual(paths, sorted(paths))
        self.assertTrue(all(p.startswith("data/") and len(e["sha256"]) == 64
                            for p, e in zip(paths, entries)))

    def test_no_archived_artifact_bodies_remain_on_main(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "data").iterdir()),
                         ["manifest.json"])
