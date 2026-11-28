"""Open an Iceberg catalog: the lab's Lakekeeper (`lab`) or AWS Glue (`glue`).

The same writer code runs against both; only the connection properties differ.
"""

from __future__ import annotations

import pyarrow.fs as pafs
from pyiceberg.catalog import Catalog, load_catalog

from datagen.settings import Settings, SettingsError

CATALOGS = ("lab", "glue")
S3_MAX_ATTEMPTS = 10


class LabS3RetryStrategy(pafs.AwsStandardS3RetryStrategy):  # type: ignore[misc]  # untyped base
    """AWS standard retries with more attempts than Arrow's default of 3.

    Under parallel writes RustFS can answer CompleteMultipartUpload with 503 ServiceUnavailable
    (`x-rustfs-error-cause: namespace_lock_timeout`, `Retry-After: 1`) while a slow disk holds
    the object lock. 503 is retryable in S3, so the client backs off and tries again.
    pyiceberg instantiates this class by name (`s3.retry-strategy-impl`).
    """

    def __init__(self) -> None:
        super().__init__(max_attempts=S3_MAX_ATTEMPTS)


RETRY_STRATEGY_IMPL = f"{__name__}.{LabS3RetryStrategy.__name__}"


def open_catalog(settings: Settings, kind: str = "lab") -> Catalog:
    if kind == "lab":
        # The warehouse vends no credentials (STS and remote signing are off), so the client
        # brings the lab's static keys and the host-side endpoint.
        return load_catalog(
            "lab",
            type="rest",
            uri=settings.rest_catalog_url,
            warehouse=settings.warehouse,
            **{
                "s3.endpoint": settings.s3_endpoint,
                "s3.access-key-id": settings.s3_access_key,
                "s3.secret-access-key": settings.s3_secret_key,
                "s3.region": settings.s3_region,
                "s3.force-virtual-addressing": "false",
                "s3.retry-strategy-impl": RETRY_STRATEGY_IMPL,
            },
        )
    if kind == "glue":
        if not (settings.glue_region and settings.glue_warehouse):
            raise SettingsError(
                "--catalog glue needs FLOE_LAB_GLUE_REGION and FLOE_LAB_GLUE_WAREHOUSE in .env "
                "(see .env.example) and AWS credentials from your own profile or SSO session."
            )
        return load_catalog(
            "glue",
            type="glue",
            warehouse=settings.glue_warehouse,
            **{"glue.region": settings.glue_region, "s3.region": settings.glue_region},
        )
    raise SettingsError(f"unknown catalog {kind!r}; use one of {', '.join(CATALOGS)}")
