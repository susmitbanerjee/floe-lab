"""Stack tests: run the real `make` targets against Docker Compose.

These reset the stack, which deletes its data volumes. They run in CI's stack job
(`make test-stack`), and locally only when you ask for them with that target.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.stack

ROOT = Path(__file__).resolve().parents[1]
UP_BUDGET_SECONDS = 120
SMOKE_CHECKS = 17


def run(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0])
    assert executable, f"{args[0]} not found on PATH"
    return subprocess.run(
        [executable, *args[1:]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def succeeded(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode != 0:
        print(result.stdout, result.stderr)
    return result.returncode == 0


def container_ids() -> list[str]:
    result = run("docker", "compose", "--env-file", ".env", "ps", "--all", "--quiet")
    assert succeeded(result)
    return sorted(result.stdout.split())


@pytest.fixture(scope="module")
def first_up() -> tuple[subprocess.CompletedProcess[str], float]:
    assert succeeded(run("make", "reset"))
    started = time.monotonic()
    result = run("make", "up")
    return result, time.monotonic() - started


def test_compose_ports_bind_to_loopback_only() -> None:
    result = run("docker", "compose", "--env-file", ".env.example", "config", "--format", "json")
    assert succeeded(result)
    services = json.loads(result.stdout)["services"]
    published = {
        name: [port.get("host_ip") for port in service.get("ports", [])]
        for name, service in services.items()
    }
    assert published == {
        "rustfs": ["127.0.0.1"],
        "postgres": [],
        "lakekeeper-migrate": [],
        "lakekeeper": ["127.0.0.1"],
    }


def test_make_up_from_scratch(first_up: tuple[subprocess.CompletedProcess[str], float]) -> None:
    result, seconds = first_up
    assert succeeded(result)
    for line in (
        "bucket floe-lab: created",
        "lakekeeper bootstrap: created",
        "warehouse lab: created",
    ):
        assert line in result.stdout
    assert f"smoke: {SMOKE_CHECKS} ok, 0 failed" in result.stdout
    assert seconds < UP_BUDGET_SECONDS, f"make up took {seconds:.0f}s"


def test_second_make_up_is_a_no_op(
    first_up: tuple[subprocess.CompletedProcess[str], float],
) -> None:
    assert succeeded(first_up[0])
    before = container_ids()
    result = run("make", "up")
    assert succeeded(result)
    for line in (
        "bucket floe-lab: exists",
        "lakekeeper bootstrap: exists",
        "warehouse lab: exists",
    ):
        assert line in result.stdout
    assert "created" not in result.stdout.replace("created .env", "")
    assert container_ids() == before


def test_smoke_is_all_ok(first_up: tuple[subprocess.CompletedProcess[str], float]) -> None:
    assert succeeded(first_up[0])
    result = run("make", "smoke")
    assert succeeded(result)
    lines = [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL "))]
    assert len(lines) == SMOKE_CHECKS
    assert all(line.startswith("ok ") for line in lines), "\n".join(lines)
