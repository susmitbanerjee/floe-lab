"""make export writes only inside the Floe checkout's tests/expected/."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from checks.export import ExportError, export


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def floe(tmp_path: Path) -> Path:
    root = tmp_path / "floe"
    for name, text in {
        "Cargo.toml": "[workspace]\n",
        "README.md": "floe\n",
        "tests/tpch.rs": "// reads tests/expected\n",
        "tests/expected/KNOWN_FAILURES.md": "# Known TPC-H failures\n",
        "tests/expected/README.md": "old readme\n",
        "tests/expected/tpch-sf0.01/q01.csv": "old|answer\n",
        "tests/expected/stale-but-not-ours.txt": "keep me\n",
        "crates/floe-scan/src/lib.rs": "// code\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_export_touches_only_tests_expected(floe: Path) -> None:
    before = tree(floe)
    written = export(floe)
    after = tree(floe)
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed, "export wrote nothing"
    assert all(name.startswith("tests/expected/") for name in changed)
    assert all(p.is_relative_to(floe / "tests" / "expected") for p in written)
    for kept in ("tests/expected/KNOWN_FAILURES.md", "tests/expected/stale-but-not-ours.txt"):
        assert after[kept] == before[kept]


def test_export_writes_tpch_orders_and_readme(floe: Path) -> None:
    export(floe)
    target = floe / "tests" / "expected"
    for sf in ("0.01", "1"):
        for n in range(1, 23):
            assert (target / f"tpch-sf{sf}" / f"q{n:02d}.sql").is_file()
            assert (target / f"tpch-sf{sf}" / f"q{n:02d}.csv").is_file()
        assert (target / f"tpch-sf{sf}" / "LICENSE-duckdb").is_file()
    for profile in ("unit", "ci", "small"):
        assert (target / f"orders-{profile}" / "region_7d.csv").is_file()
        assert (target / f"orders-{profile}" / "files.json").is_file()
    readme = (target / "README.md").read_text()
    assert "make export" in readme
    assert "KNOWN_FAILURES.md" in readme


def test_export_refuses_a_directory_that_is_not_floe(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="does not look like the floe repository"):
        export(tmp_path)
