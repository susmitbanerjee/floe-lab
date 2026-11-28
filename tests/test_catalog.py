"""Unit tests for catalog connection settings (no stack, no network)."""

import importlib

import pytest

from datagen.catalog import (
    RETRY_STRATEGY_IMPL,
    S3_MAX_ATTEMPTS,
    LabS3RetryStrategy,
    open_catalog,
)
from datagen.settings import Settings, SettingsError

CREDENTIALS = {"ICEBLINK_LAB_S3_ACCESS_KEY": "a", "ICEBLINK_LAB_S3_SECRET_KEY": "b"}


def test_retry_strategy_resolves_by_name_like_pyiceberg_does() -> None:
    module, _, name = RETRY_STRATEGY_IMPL.rpartition(".")
    strategy = getattr(importlib.import_module(module), name)()
    assert isinstance(strategy, LabS3RetryStrategy)
    assert strategy.max_attempts == S3_MAX_ATTEMPTS > 3


def test_endpoints_can_be_overridden_for_containers() -> None:
    settings = Settings.from_env(
        CREDENTIALS
        | {
            "ICEBLINK_LAB_S3_ENDPOINT": "http://rustfs:9000",
            "ICEBLINK_LAB_CATALOG_URL": "http://lakekeeper:8181",
        }
    )
    assert settings.s3_endpoint == "http://rustfs:9000"
    assert settings.rest_catalog_url == "http://lakekeeper:8181/catalog"


def test_defaults_point_at_the_host() -> None:
    settings = Settings.from_env(CREDENTIALS)
    assert settings.s3_endpoint == "http://127.0.0.1:9000"
    assert settings.rest_catalog_url == "http://127.0.0.1:8181/catalog"


def test_glue_needs_region_and_warehouse() -> None:
    with pytest.raises(SettingsError, match="ICEBLINK_LAB_GLUE_REGION"):
        open_catalog(Settings.from_env(CREDENTIALS), "glue")


def test_unknown_catalog_is_rejected() -> None:
    with pytest.raises(SettingsError, match="unknown catalog"):
        open_catalog(Settings.from_env(CREDENTIALS), "hive")
