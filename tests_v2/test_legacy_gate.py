"""The legacy CLI must require explicit retrospective opt-in."""
import subprocess
import sys
import unittest
from pathlib import Path


class LegacyGateTest(unittest.TestCase):
    def test_old_cli_requires_opt_in_before_importing_legacy_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        for script in ("backtest_engine.py", "backtest_rules.py"):
            proc = subprocess.run([sys.executable, str(root / script)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("--legacy-retrospective", proc.stderr)
