"""Bootstrap the lab: bucket, Lakekeeper bootstrap and the "lab" warehouse.

`make up` runs this after `docker compose up --wait`. Every step checks first and treats
"already exists" as success, so running it twice changes nothing.

API reference: Lakekeeper v0.15.1 management API (docs/docs/api/management-open-api.yaml).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Protocol

import boto3
import requests
from botocore.exceptions import ClientError

from datagen.settings import Settings, SettingsError

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

JsonDict = dict[str, Any]
TIMEOUT_SECONDS = 30.0
CREATED = "created"
EXISTS = "exists"


class BootstrapError(RuntimeError):
    """A bootstrap step failed in a way that is not "already exists"."""


class Http(Protocol):
    """The part of requests.Session that bootstrap uses (lets tests pass a fake)."""

    def get(self, url: str, *, timeout: float) -> requests.Response: ...

    def post(self, url: str, *, json: JsonDict, timeout: float) -> requests.Response: ...


class BucketApi(Protocol):
    """The part of the boto3 S3 client that bootstrap uses."""

    def head_bucket(self, *, Bucket: str) -> object: ...

    def create_bucket(self, *, Bucket: str) -> object: ...


def s3_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def bootstrap_request() -> JsonDict:
    return {"accept-terms-of-use": True}


def warehouse_request(settings: Settings) -> JsonDict:
    """Warehouse on the lab bucket. STS and remote signing are off: clients bring static keys."""
    return {
        "warehouse-name": settings.warehouse,
        "storage-profile": {
            "type": "s3",
            "flavor": "s3-compat",
            "bucket": settings.bucket,
            "key-prefix": settings.key_prefix,
            "endpoint": settings.warehouse_endpoint,
            "region": settings.s3_region,
            "path-style-access": True,
            "sts-enabled": False,
            "remote-signing-enabled": False,
        },
        "storage-credential": {
            "type": "s3",
            "credential-type": "access-key",
            "access-key-id": settings.s3_access_key,
            "secret-access-key": settings.s3_secret_key,
        },
    }


def ensure_bucket(s3: BucketApi, bucket: str) -> str:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
        s3.create_bucket(Bucket=bucket)
        return CREATED
    return EXISTS


def ensure_bootstrapped(http: Http, settings: Settings) -> str:
    info = _json(http.get(f"{settings.management_url}/info", timeout=TIMEOUT_SECONDS), "GET info")
    if info.get("bootstrapped") is True:
        return EXISTS
    response = http.post(
        f"{settings.management_url}/bootstrap", json=bootstrap_request(), timeout=TIMEOUT_SECONDS
    )
    if response.status_code == 204:
        return CREATED
    if _error_type(response) == "CatalogAlreadyBootstrapped":
        return EXISTS
    raise BootstrapError(_describe("POST bootstrap", response))


def ensure_warehouse(http: Http, settings: Settings) -> str:
    listing = _json(
        http.get(f"{settings.management_url}/warehouse", timeout=TIMEOUT_SECONDS), "GET warehouse"
    )
    for warehouse in listing.get("warehouses", []):
        if warehouse.get("name") == settings.warehouse:
            _check_profile(warehouse, settings)
            return EXISTS
    response = http.post(
        f"{settings.management_url}/warehouse",
        json=warehouse_request(settings),
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code == 201:
        return CREATED
    if response.status_code == 409 and _error_type(response) == "WarehouseAlreadyExists":
        return EXISTS
    raise BootstrapError(_describe("POST warehouse", response))


def _check_profile(warehouse: JsonDict, settings: Settings) -> None:
    profile = warehouse.get("storage-profile", {})
    actual = (
        profile.get("bucket"),
        str(profile.get("key-prefix", "")).strip("/"),
        str(profile.get("endpoint", "")).rstrip("/"),
    )
    wanted = (
        settings.bucket,
        settings.key_prefix.strip("/"),
        settings.warehouse_endpoint.rstrip("/"),
    )
    if actual != wanted:
        raise BootstrapError(
            f"warehouse {settings.warehouse!r} exists on {actual}, not {wanted}. "
            "Run `make reset` to start the lab from scratch."
        )


def _json(response: requests.Response, what: str) -> JsonDict:
    if response.status_code != 200:
        raise BootstrapError(_describe(what, response))
    body: JsonDict = response.json()
    return body


def _error_type(response: requests.Response) -> str | None:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return None
    kind = error.get("type") if isinstance(error, dict) else None
    return kind if isinstance(kind, str) else None


def _describe(what: str, response: requests.Response) -> str:
    return f"{what} returned HTTP {response.status_code}: {response.text[:500]}"


def run(settings: Settings, http: Http, s3: BucketApi) -> None:
    print(f"bucket {settings.bucket}: {ensure_bucket(s3, settings.bucket)}")
    print(f"lakekeeper bootstrap: {ensure_bootstrapped(http, settings)}")
    print(f"warehouse {settings.warehouse}: {ensure_warehouse(http, settings)}")


def main() -> int:
    try:
        settings = Settings.from_env()
        with requests.Session() as http:
            run(settings, http, s3_client(settings))
    except (SettingsError, BootstrapError, ClientError, requests.RequestException) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        print("Check `make logs`, then run `make up` again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
