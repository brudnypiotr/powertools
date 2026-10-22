"""The whole checker in dry mode must finish without a crashed check (exit code 2) on any OS."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_does_not_crash():
    p = subprocess.run([sys.executable, "-m", "checks.run", "--dry"], cwd=ROOT, capture_output=True, text=True,
                       timeout=300)
    assert p.returncode in (0, 1), p.stdout + p.stderr
    assert "crashed" not in p.stdout
