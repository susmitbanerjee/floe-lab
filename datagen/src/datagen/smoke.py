"""Smoke-test the lab: S3 compatibility on 127.0.0.1:9000, then the REST catalog.

The S3 checks run once with boto3 and once with pyarrow's S3 filesystem. They write only under
`smoke/` in the lab bucket and delete what they write. Payloads derive from a fixed seed, so
every run moves the same bytes. Prints one ok/FAIL line per operation; exits 1 on any FAIL.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

import pyarrow.fs as pafs
from pyiceberg.catalog import Catalog, load_catalog

from datagen.bootstrap import s3_client
from datagen.settings import Settings, SettingsError

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

MIB = 1024 * 1024
PART_SIZE = 5 * MIB
PARTS = 3
OBJECT_SIZE = 4096
LIST_OBJECTS = 5
PAGE_SIZE = 2
SEED = b"floe-lab-smoke-v1"

Check = tuple[str, str, Callable[[], None]]


class SmokeFailure(AssertionError):
    """A smoke check saw the wrong result."""


def payload(name: str, size: int) -> bytes:
    """Deterministic bytes for one object: same seed and name, same bytes, on every machine."""
    return hashlib.shake_256(SEED + name.encode()).digest(size)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


class Boto3Checks:
    def __init__(self, s3: S3Client, bucket: str) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.prefix = "smoke/boto3/"
        self.key = self.prefix + "object.bin"
        self.data = payload(self.key, OBJECT_SIZE)

    def checks(self) -> list[Check]:
        return [
            ("boto3", "put", self.put),
            ("boto3", "head", self.head),
            ("boto3", "get", self.get),
            ("boto3", "range get bytes 100-199", self.range_get),
            ("boto3", "list v2 prefix + pagination", self.list_paginated),
            ("boto3", "multipart upload 3 x 5 MiB", self.multipart),
            ("boto3", "delete", self.delete),
        ]

    def put(self) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self.key, Body=self.data)

    def head(self) -> None:
        size = self.s3.head_object(Bucket=self.bucket, Key=self.key)["ContentLength"]
        expect(size == OBJECT_SIZE, f"ContentLength {size}, want {OBJECT_SIZE}")

    def get(self) -> None:
        body = self.s3.get_object(Bucket=self.bucket, Key=self.key)["Body"].read()
        expect(body == self.data, "body differs from what was put")

    def range_get(self) -> None:
        response = self.s3.get_object(Bucket=self.bucket, Key=self.key, Range="bytes=100-199")
        status = response["ResponseMetadata"]["HTTPStatusCode"]
        expect(status == 206, f"HTTP {status}, want 206 Partial Content")
        expect(response["Body"].read() == self.data[100:200], "range bytes differ")

    def list_paginated(self) -> None:
        prefix = self.prefix + "list/"
        wanted = [f"{prefix}{i:02d}" for i in range(LIST_OBJECTS)]
        for key in wanted:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=payload(key, 16))
        seen: list[str] = []
        pages = 0
        token: str | None = None
        while True:
            if token is None:
                page = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=PAGE_SIZE)
            else:
                page = self.s3.list_objects_v2(
                    Bucket=self.bucket, Prefix=prefix, MaxKeys=PAGE_SIZE, ContinuationToken=token
                )
            pages += 1
            seen += [item["Key"] for item in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                break
            token = page["NextContinuationToken"]
        expect(seen == wanted, f"listed {seen}, want {wanted}")
        want_pages = -(-LIST_OBJECTS // PAGE_SIZE)
        expect(pages == want_pages, f"{pages} pages, want {want_pages}")

    def multipart(self) -> None:
        key = self.prefix + "multipart.bin"
        parts = [payload(f"{key}#{n}", PART_SIZE) for n in range(PARTS)]
        upload_id = self.s3.create_multipart_upload(Bucket=self.bucket, Key=key)["UploadId"]
        etags = [
            self.s3.upload_part(
                Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=n + 1, Body=part
            )["ETag"]
            for n, part in enumerate(parts)
        ]
        self.s3.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"ETag": etag, "PartNumber": n + 1} for n, etag in enumerate(etags)]
            },
        )
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        expect(head["ContentLength"] == PARTS * PART_SIZE, f"size {head['ContentLength']}")
        expect(head["ETag"].strip('"').endswith(f"-{PARTS}"), f"ETag {head['ETag']} not multipart")
        body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        expect(body == b"".join(parts), "multipart body differs from its parts")

    def delete(self) -> None:
        keys = [obj["Key"] for obj in self._list(self.prefix)]
        for key in keys:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        left = self._list(self.prefix)
        expect(not left, f"{len(left)} objects left under {self.prefix}")

    def _list(self, prefix: str) -> list[dict[str, str]]:
        page = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return [{"Key": item["Key"]} for item in page.get("Contents", [])]


class PyArrowChecks:
    def __init__(self, settings: Settings, s3: S3Client) -> None:
        scheme, _, host = settings.s3_endpoint.partition("://")
        self.fs = pafs.S3FileSystem(
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            scheme=scheme,
            endpoint_override=host,
        )
        self.s3 = s3
        self.bucket = settings.bucket
        self.prefix = "smoke/pyarrow/"
        self.key = self.prefix + "object.bin"
        self.data = payload(self.key, OBJECT_SIZE)

    def checks(self) -> list[Check]:
        return [
            ("pyarrow", "put", self.put),
            ("pyarrow", "head", self.head),
            ("pyarrow", "get", self.get),
            ("pyarrow", "range get bytes 100-199", self.range_get),
            ("pyarrow", "list prefix", self.list_prefix),
            ("pyarrow", "multipart upload 15 MiB stream", self.multipart),
            ("pyarrow", "delete", self.delete),
        ]

    def _path(self, key: str) -> str:
        return f"{self.bucket}/{key}"

    def _write(self, key: str, data: bytes) -> None:
        with self.fs.open_output_stream(self._path(key)) as out:
            out.write(data)

    def put(self) -> None:
        self._write(self.key, self.data)

    def head(self) -> None:
        info = self.fs.get_file_info(self._path(self.key))
        expect(info.type == pafs.FileType.File, f"type {info.type}, want File")
        expect(info.size == OBJECT_SIZE, f"size {info.size}, want {OBJECT_SIZE}")

    def get(self) -> None:
        with self.fs.open_input_stream(self._path(self.key)) as src:
            expect(src.read() == self.data, "body differs from what was put")

    def range_get(self) -> None:
        with self.fs.open_input_file(self._path(self.key)) as src:
            expect(src.read_at(100, 100) == self.data[100:200], "range bytes differ")

    def list_prefix(self) -> None:
        prefix = self.prefix + "list/"
        wanted = [f"{prefix}{i:02d}" for i in range(LIST_OBJECTS)]
        for key in wanted:
            self._write(key, payload(key, 16))
        selector = pafs.FileSelector(self._path(prefix), recursive=True)
        seen = sorted(info.path for info in self.fs.get_file_info(selector))
        expect(seen == [self._path(k) for k in wanted], f"listed {seen}")

    def multipart(self) -> None:
        key = self.prefix + "multipart.bin"
        data = payload(key, PARTS * PART_SIZE)
        self._write(key, data)
        etag = self.s3.head_object(Bucket=self.bucket, Key=key)["ETag"].strip('"')
        expect("-" in etag, f"ETag {etag} is not a multipart ETag")
        with self.fs.open_input_stream(self._path(key)) as src:
            expect(src.read() == data, "multipart body differs")

    def delete(self) -> None:
        self.fs.delete_file(self._path(self.key))
        info = self.fs.get_file_info(self._path(self.key))
        expect(info.type == pafs.FileType.NotFound, f"type {info.type} after delete")
        # Arrow keeps empty "directory" marker objects when a directory's last file goes;
        # delete_dir removes the rest of the prefix, markers included.
        self.fs.delete_dir(self._path(self.prefix))
        left = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix).get("KeyCount", 0)
        expect(left == 0, f"{left} objects left under {self.prefix}")


class CatalogChecks:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog: Catalog | None = None

    def checks(self) -> list[Check]:
        name = self.settings.namespace
        return [
            ("pyiceberg", "connect to REST catalog", self.connect),
            ("pyiceberg", f"create namespace {name}", self.create_namespace),
            ("pyiceberg", f"load namespace {name}", self.load_namespace),
        ]

    def connect(self) -> None:
        self.catalog = load_catalog(
            self.settings.warehouse,
            type="rest",
            uri=self.settings.rest_catalog_url,
            warehouse=self.settings.warehouse,
        )

    def _catalog(self) -> Catalog:
        if self.catalog is None:
            raise SmokeFailure("not connected to the catalog")
        return self.catalog

    def create_namespace(self) -> None:
        self._catalog().create_namespace_if_not_exists(self.settings.namespace)

    def load_namespace(self) -> None:
        catalog = self._catalog()
        expect((self.settings.namespace,) in catalog.list_namespaces(), "namespace not listed")
        catalog.load_namespace_properties(self.settings.namespace)


def clean(s3: S3Client, bucket: str) -> None:
    """Remove anything an interrupted earlier run left under smoke/."""
    for item in s3.list_objects_v2(Bucket=bucket, Prefix="smoke/").get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=item["Key"])


def run_checks(checks: list[Check]) -> int:
    failed = 0
    for client, name, check in checks:
        try:
            check()
        except Exception as exc:  # report every failure and carry on; the exit code says FAIL
            failed += 1
            print(f"FAIL  {client:<9} {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {client:<9} {name}")
    print(f"smoke: {len(checks) - failed} ok, {failed} failed")
    return failed


def main() -> int:
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1
    s3 = s3_client(settings)
    clean(s3, settings.bucket)
    checks = (
        Boto3Checks(s3, settings.bucket).checks()
        + PyArrowChecks(settings, s3).checks()
        + CatalogChecks(settings).checks()
    )
    if run_checks(checks):
        print("Check `make logs`, then run `make smoke` again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
