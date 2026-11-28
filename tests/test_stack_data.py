"""Stack tests for make data, make append and make data-tpch.

They need the stack running. CI's stack job runs them through `make test-stack`, after
tests/test_stack.py has brought the stack up from scratch. They rebuild sales.orders and the
tpch_sf0_01 namespace, so they replace whatever data the lab held.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from pyiceberg.catalog import Catalog

from datagen.catalog import open_catalog
from datagen.settings import Settings

pytestmark = pytest.mark.stack

ROOT = Path(__file__).resolve().parents[1]
CI_BUDGET_SECONDS = 60
REFUSAL = "sales.orders already holds profile ci; run make reset or make data FORCE=1"
ORDERS = ("sales", "orders")
TPCH_SF0_01_ROWS = {
    "region": 5,
    "nation": 25,
    "supplier": 100,
    "customer": 1_500,
    "part": 2_000,
    "partsupp": 8_000,
    "orders": 15_000,
    "lineitem": 60_175,
}


def make(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("make")
    assert executable, "make not found on PATH"
    result = subprocess.run(
        [executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr)
    return result


def manifest(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / "data" / f"manifest-{name}.json").read_text())
    return data


def catalog() -> Catalog:
    return open_catalog(Settings.from_env(), "lab")


def scanned_rows(identifier: tuple[str, str]) -> int:
    table = catalog().load_table(identifier)
    rows: int = table.scan(selected_fields=(table.schema().fields[0].name,)).to_arrow().num_rows
    return rows


@pytest.fixture(scope="module")
def ci_data() -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    result = make("data", "PROFILE=ci", "FORCE=1")
    return result, time.monotonic() - started


def test_ci_profile_writes_in_under_a_minute(
    ci_data: tuple[subprocess.CompletedProcess[str], float],
) -> None:
    result, seconds = ci_data
    assert result.returncode == 0
    assert seconds < CI_BUDGET_SECONDS, f"make data PROFILE=ci took {seconds:.0f}s"


def test_catalog_hands_clients_the_host_endpoint(
    ci_data: tuple[subprocess.CompletedProcess[str], float],
) -> None:
    """Clients use the endpoint in the table config, so it must be reachable from the host."""
    assert ci_data[0].returncode == 0
    table = catalog().load_table(ORDERS)
    assert table.io.properties["s3.endpoint"].rstrip("/") == "http://127.0.0.1:9000"


def test_pyiceberg_reads_back_the_manifest_row_count(
    ci_data: tuple[subprocess.CompletedProcess[str], float],
) -> None:
    assert ci_data[0].returncode == 0
    written = manifest("ci")
    table = catalog().load_table(ORDERS)
    assert str(table.metadata.table_uuid) == written["table_uuid"]
    assert scanned_rows(ORDERS) == written["total_rows"] == 1_200_000
    assert written["day_count"] == written["snapshot_count"] == len(table.snapshots()) == 30
    assert all(day["rows"] == 40_000 for day in written["days"])
    assert sum(day["data_files"] for day in written["days"]) == written["total_data_files"]


def test_second_run_is_refused(ci_data: tuple[subprocess.CompletedProcess[str], float]) -> None:
    assert ci_data[0].returncode == 0
    result = make("data", "PROFILE=ci")
    assert result.returncode != 0
    assert REFUSAL in result.stderr


def test_force_drops_and_rebuilds(
    ci_data: tuple[subprocess.CompletedProcess[str], float],
) -> None:
    before = manifest("ci")
    assert make("data", "PROFILE=ci", "FORCE=1").returncode == 0
    after = manifest("ci")
    assert after["table_uuid"] != before["table_uuid"]
    assert after["total_rows"] == before["total_rows"] == scanned_rows(ORDERS)


def test_append_days_then_rows(ci_data: tuple[subprocess.CompletedProcess[str], float]) -> None:
    assert make("append", "DAYS=1").returncode == 0
    after_days = manifest("ci")
    assert after_days["days"][-1]["date"] == "2026-11-19"
    assert after_days["total_rows"] == 1_240_000

    assert make("append", "ROWS=214380").returncode == 0
    after_rows = manifest("ci")
    last = after_rows["days"][-1]
    assert last["date"] == "2026-11-19"
    assert [s["kind"] for s in last["snapshots"]] == ["day", "rows"]
    assert last["rows"] == 40_000 + 214_380
    assert after_rows["snapshot_count"] == 32
    assert after_rows["total_rows"] == 1_454_380 == scanned_rows(ORDERS)


def test_tpch_sf0_01_writes_the_eight_tables() -> None:
    assert make("data-tpch", "SF=0.01", "FORCE=1").returncode == 0
    written = manifest("tpch_sf0_01")
    assert {name: t["rows"] for name, t in written["tables"].items()} == TPCH_SF0_01_ROWS
    assert scanned_rows(("tpch_sf0_01", "lineitem")) == TPCH_SF0_01_ROWS["lineitem"]
