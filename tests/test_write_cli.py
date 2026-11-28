"""Unit tests for the writer and TPC-H command lines (no stack, no network)."""

from __future__ import annotations

import argparse
from decimal import Decimal

import pytest

from datagen.generate import PROFILES
from datagen.tpch import namespace_for, parse_scale_factor
from datagen.write import ORDERS_SCHEMA, ORDERS_SPEC, parse_args, target_file_size


def test_tpch_namespace_names() -> None:
    assert namespace_for(Decimal("0.01")) == "tpch_sf0_01"
    assert namespace_for(Decimal("1")) == "tpch_sf1"
    assert namespace_for(Decimal("1.0")) == "tpch_sf1"
    assert namespace_for(Decimal("10")) == "tpch_sf10"


@pytest.mark.parametrize("text", ["0", "-1", "abc", "nan", "inf"])
def test_tpch_rejects_bad_scale_factors(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_scale_factor(text)


def test_append_needs_exactly_one_of_days_or_rows() -> None:
    assert parse_args(["append", "--days", "1"]).days == 1
    assert parse_args(["append", "--rows", "214380"]).rows == 214380
    for bad in (["append"], ["append", "--days", "1", "--rows", "2"], ["append", "--days", "0"]):
        with pytest.raises(SystemExit):
            parse_args(bad)


def test_data_needs_a_known_profile() -> None:
    assert parse_args(["data", "--profile", "ci"]).profile == "ci"
    with pytest.raises(SystemExit):
        parse_args(["data", "--profile", "huge"])


def test_schema_matches_the_design() -> None:
    assert [(f.field_id, f.name, str(f.field_type)) for f in ORDERS_SCHEMA.fields] == [
        (1, "order_id", "long"),
        (2, "tenant_id", "string"),
        (3, "customer_id", "string"),
        (4, "region", "string"),
        (5, "status", "string"),
        (6, "amount", "decimal(14, 2)"),
        (7, "created_at", "timestamptz"),
    ]
    assert all(f.required for f in ORDERS_SCHEMA.fields)
    (field,) = ORDERS_SPEC.fields
    assert (field.source_id, str(field.transform)) == (7, "day")


def test_target_file_size_scales_with_rows_per_file() -> None:
    ci, small = target_file_size(PROFILES["ci"], 1), target_file_size(PROFILES["small"], 1)
    assert 50 * 10_000 < ci < 120 * 10_000
    assert small == pytest.approx(2 * ci, rel=0.05)
