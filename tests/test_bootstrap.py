"""Unit tests for datagen.bootstrap: request bodies and idempotency, with fake HTTP and S3."""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests
from botocore.exceptions import ClientError

from datagen.bootstrap import (
    CREATED,
    EXISTS,
    BootstrapError,
    JsonDict,
    bootstrap_request,
    ensure_bootstrapped,
    ensure_bucket,
    ensure_warehouse,
    warehouse_request,
)
from datagen.settings import Settings, SettingsError

SETTINGS = Settings(
    s3_access_key="test-access",
    s3_secret_key="test-secret",
    s3_region="test-region",
    s3_internal_endpoint="http://store:9000",
    bucket="test-bucket",
    key_prefix="tables/",
    catalog_url="http://catalog:8181",
    warehouse="test-warehouse",
)
MGMT = "http://catalog:8181/management/v1"


def response(status: int, body: Any = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = b"" if body is None else json.dumps(body).encode()
    return result


def error(status: int, kind: str) -> requests.Response:
    return response(status, {"error": {"code": status, "type": kind, "message": kind}})


class FakeHttp:
    """Answers GET and POST from fixed responses and records every request."""

    def __init__(self, gets: dict[str, requests.Response], posts: dict[str, requests.Response]):
        self.gets = gets
        self.posts = posts
        self.posted: list[tuple[str, JsonDict]] = []

    def get(self, url: str, *, timeout: float) -> requests.Response:
        return self.gets[url]

    def post(self, url: str, *, json: JsonDict, timeout: float) -> requests.Response:
        self.posted.append((url, json))
        return self.posts[url]


class FakeBuckets:
    def __init__(self, head_error: str | None) -> None:
        self.head_error = head_error
        self.created: list[str] = []

    def head_bucket(self, *, Bucket: str) -> object:
        if self.head_error:
            raise ClientError({"Error": {"Code": self.head_error}}, "HeadBucket")
        return {}

    def create_bucket(self, *, Bucket: str) -> object:
        self.created.append(Bucket)
        return {}


def test_settings_require_credentials() -> None:
    with pytest.raises(SettingsError, match="FLOE_LAB_S3_ACCESS_KEY, FLOE_LAB_S3_SECRET_KEY"):
        Settings.from_env({})


def test_settings_read_from_env() -> None:
    env = {
        "FLOE_LAB_S3_ACCESS_KEY": "a",
        "FLOE_LAB_S3_SECRET_KEY": "b",
        "FLOE_LAB_S3_REGION": "r",
    }
    settings = Settings.from_env(env)
    assert (settings.s3_access_key, settings.s3_secret_key, settings.s3_region) == ("a", "b", "r")


def test_bootstrap_request_accepts_terms() -> None:
    assert bootstrap_request() == {"accept-terms-of-use": True}


def test_warehouse_request_is_built_from_settings() -> None:
    assert warehouse_request(SETTINGS) == {
        "warehouse-name": "test-warehouse",
        "storage-profile": {
            "type": "s3",
            "flavor": "s3-compat",
            "bucket": "test-bucket",
            "key-prefix": "tables/",
            "endpoint": "http://store:9000",
            "region": "test-region",
            "path-style-access": True,
            "sts-enabled": False,
            "remote-signing-enabled": False,
        },
        "storage-credential": {
            "type": "s3",
            "credential-type": "access-key",
            "access-key-id": "test-access",
            "secret-access-key": "test-secret",
        },
    }


def test_bucket_exists() -> None:
    buckets = FakeBuckets(head_error=None)
    assert ensure_bucket(buckets, "b") == EXISTS
    assert buckets.created == []


def test_bucket_created_when_missing() -> None:
    buckets = FakeBuckets(head_error="404")
    assert ensure_bucket(buckets, "b") == CREATED
    assert buckets.created == ["b"]


def test_bucket_other_errors_raise() -> None:
    with pytest.raises(ClientError):
        ensure_bucket(FakeBuckets(head_error="403"), "b")


def test_bootstrap_skipped_when_already_bootstrapped() -> None:
    http = FakeHttp({f"{MGMT}/info": response(200, {"bootstrapped": True})}, {})
    assert ensure_bootstrapped(http, SETTINGS) == EXISTS
    assert http.posted == []


def test_bootstrap_posts_request() -> None:
    http = FakeHttp(
        {f"{MGMT}/info": response(200, {"bootstrapped": False})},
        {f"{MGMT}/bootstrap": response(204)},
    )
    assert ensure_bootstrapped(http, SETTINGS) == CREATED
    assert http.posted == [(f"{MGMT}/bootstrap", {"accept-terms-of-use": True})]


def test_bootstrap_already_bootstrapped_error_is_success() -> None:
    http = FakeHttp(
        {f"{MGMT}/info": response(200, {"bootstrapped": False})},
        {f"{MGMT}/bootstrap": error(400, "CatalogAlreadyBootstrapped")},
    )
    assert ensure_bootstrapped(http, SETTINGS) == EXISTS


def test_bootstrap_other_errors_raise() -> None:
    http = FakeHttp(
        {f"{MGMT}/info": response(200, {"bootstrapped": False})},
        {f"{MGMT}/bootstrap": error(500, "InternalError")},
    )
    with pytest.raises(BootstrapError, match="HTTP 500"):
        ensure_bootstrapped(http, SETTINGS)


def test_warehouse_created_with_body_from_settings() -> None:
    http = FakeHttp(
        {f"{MGMT}/warehouse": response(200, {"warehouses": []})},
        {f"{MGMT}/warehouse": response(201, {"id": "w"})},
    )
    assert ensure_warehouse(http, SETTINGS) == CREATED
    assert http.posted == [(f"{MGMT}/warehouse", warehouse_request(SETTINGS))]


def test_warehouse_already_exists_error_is_success() -> None:
    http = FakeHttp(
        {f"{MGMT}/warehouse": response(200, {"warehouses": []})},
        {f"{MGMT}/warehouse": error(409, "WarehouseAlreadyExists")},
    )
    assert ensure_warehouse(http, SETTINGS) == EXISTS


def test_warehouse_in_listing_is_not_created_again() -> None:
    listed = {
        "name": "test-warehouse",
        "storage-profile": {"bucket": "test-bucket", "key-prefix": "tables"},
    }
    http = FakeHttp({f"{MGMT}/warehouse": response(200, {"warehouses": [listed]})}, {})
    assert ensure_warehouse(http, SETTINGS) == EXISTS
    assert http.posted == []


def test_warehouse_on_another_bucket_raises() -> None:
    listed = {"name": "test-warehouse", "storage-profile": {"bucket": "other", "key-prefix": ""}}
    http = FakeHttp({f"{MGMT}/warehouse": response(200, {"warehouses": [listed]})}, {})
    with pytest.raises(BootstrapError, match="make reset"):
        ensure_warehouse(http, SETTINGS)


def test_warehouse_other_errors_raise() -> None:
    http = FakeHttp(
        {f"{MGMT}/warehouse": response(200, {"warehouses": []})},
        {f"{MGMT}/warehouse": error(400, "CreateWarehouseStorageProfileOverlap")},
    )
    with pytest.raises(BootstrapError, match="HTTP 400"):
        ensure_warehouse(http, SETTINGS)
