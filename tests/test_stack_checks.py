"""Stack tests for make check and make verify on the ci profile.

They rebuild sales.orders as the ci profile, so they replace whatever data the lab held. CI's
stack job runs them through `make test-stack`, after tests/test_stack.py brought the stack up.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from checks.compare import read_csv
from checks.compute import EXPECTED_DIR

pytestmark = pytest.mark.stack

ROOT = Path(__file__).resolve().parents[1]
ROWS = 214_380


def run(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0]) or args[0]
    result = subprocess.run(
        [executable, *args[1:]], cwd=ROOT, capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr)
    return result


@pytest.fixture(scope="module")
def ci_table() -> None:
    assert run("make", "data", "PROFILE=ci", "FORCE=1").returncode == 0


def test_verify_finds_no_difference(ci_table: None) -> None:
    result = run("make", "verify", "PROFILE=ci")
    assert result.returncode == 0
    assert "verify: no differences" in result.stdout


def test_append_changes_late_day_and_count_all(ci_table: None, tmp_path: Path) -> None:
    assert run("make", "append", f"ROWS={ROWS}").returncode == 0
    out = tmp_path / "ci-appended"
    compute = run(
        sys.executable, "-m", "checks.compute", "orders", "--profile", "ci", "--out", str(out)
    )
    assert compute.returncode == 0
    assert read_csv(out / "late_day.csv") != read_csv(EXPECTED_DIR / "ci" / "late_day.csv")
    before = int(read_csv(EXPECTED_DIR / "ci" / "count_all.csv")[1][0][0])
    after = int(read_csv(out / "count_all.csv")[1][0][0])
    assert after - before == ROWS
    assert read_csv(out / "status_mix.csv") != read_csv(EXPECTED_DIR / "ci" / "status_mix.csv")
