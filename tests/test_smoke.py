"""Unit tests for datagen.smoke helpers that need no stack."""

import hashlib

import pytest

from datagen.smoke import payload, run_checks

# sha256 of payload("smoke/boto3/object.bin", 4096). If this changes, every run moves new bytes.
GOLDEN_SHA256 = "fa68e1a0ab37cc14135403e2127364f4c8d8d39a7b9d97fe6bc010ca0b50d1c5"


def test_payload_is_deterministic() -> None:
    assert payload("a", 64) == payload("a", 64)
    assert payload("a", 64) != payload("b", 64)
    assert len(payload("a", 5 * 1024 * 1024)) == 5 * 1024 * 1024


def test_payload_matches_golden_hash() -> None:
    assert hashlib.sha256(payload("smoke/boto3/object.bin", 4096)).hexdigest() == GOLDEN_SHA256


def test_run_checks_reports_each_check(capsys: pytest.CaptureFixture[str]) -> None:
    def good() -> None:
        return None

    def bad() -> None:
        raise ValueError("boom")

    assert run_checks([("c", "good", good), ("c", "bad", bad)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "ok    c         good",
        "FAIL  c         bad: ValueError: boom",
        "smoke: 1 ok, 1 failed",
    ]
