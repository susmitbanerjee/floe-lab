# 0001: Object store for the local lab

- Status: accepted
- Date: 2026-11-28

## Context

Floe reads Iceberg tables through an Iceberg REST catalog (Lakekeeper) from S3-compatible
storage. The lab needs that storage on one laptop and in CI, started by `make up`, listening on
127.0.0.1 only. The store must handle what Floe and the Iceberg clients do: put, head, get,
range reads, ListObjectsV2 with prefix and pagination, multipart upload, and delete.

MinIO's community edition is source-only and archived upstream, so it is out.

## Options

Facts checked on 2026-11-28 against GitHub and the image registries.

1. **RustFS** (`rustfs/rustfs`)
   - Licence: Apache-2.0.
   - Latest release: 1.0.7, published 2026-11-26. Releases ship every one to two weeks.
   - Image: `rustfs/rustfs:1.0.7` on Docker Hub, a multi-arch index (linux/amd64, linux/arm64)
     pinnable by digest
     `sha256:69bd3c4d8d79b74723bfd68c9fbd9085155d4d4b44a5a136d87502f3075cc68c`.
   - Single process, one data directory, a `/health` endpoint, and `curl` in the image for the
     healthcheck. Runs as a non-root user.
2. **SeaweedFS** (`seaweedfs/seaweedfs`), fallback
   - Licence: Apache-2.0.
   - Latest release: 4.55, published 2026-11-23. Releases ship weekly.
   - Image: `chrislusf/seaweedfs:4.55` on Docker Hub, multi-arch, pinnable by digest
     `sha256:7ca46aeae2f223b37e5d91593c17ab37738eec6df6ab69b48a0487f7643342b9`.
   - Needs master, volume, filer and S3 gateway roles (one `server -s3` process can run them
     all), so it has more moving parts.

## Decision

Use **RustFS 1.0.7**, pinned by digest.

It passed every check in the smoke test (`make smoke`, `datagen/src/datagen/smoke.py`) against
127.0.0.1:9000, both with boto3 and with pyarrow's S3 filesystem: put, head, get, range get of
bytes 100-199 (HTTP 206), ListObjectsV2 with a prefix and 2-key pages, a 3-part multipart upload
with 5 MiB parts (ETag `-3`), a 15 MiB streamed multipart write from pyarrow, and delete.
Lakekeeper's warehouse creation also validates the storage profile by writing to and reading
from the bucket, and that passed. Nothing failed, so we did not need SeaweedFS.

One finding during testing turned out to be pyarrow behaviour, not RustFS: when pyarrow deletes
the last file in a "directory", it writes an empty marker object so the directory still
exists. The smoke test removes the prefix with `delete_dir` to clean those up.

## Consequences

- One small container serves S3 on 127.0.0.1:9000. Its data lives in the `rustfs-data`
  volume, so `make down` keeps it and `make reset` deletes it.
- The lab uses RustFS root credentials as static keys (fake values in `.env.example`). The
  warehouse has STS and remote signing off, so clients bring those keys themselves.
- Under heavy parallel writes on a slow disk (Docker Desktop's volume while Lakekeeper purged a
  1,350-file table), RustFS once answered CompleteMultipartUpload with 503
  ServiceUnavailable (`x-rustfs-error-cause: namespace_lock_timeout`, `Retry-After: 1`). 503 is
  retryable in S3, so the lab's Iceberg clients use 10 retry attempts instead of Arrow's 3
  (`datagen.catalog.LabS3RetryStrategy`). If 503s persist, revisit this decision.
- RustFS 1.0.x is recent (1.0.2 shipped on 2026-10-16). If a release breaks the smoke test,
  pin back or switch to SeaweedFS and update this ADR. Only `compose.yaml` and the internal
  endpoint in `datagen/src/datagen/settings.py` name the store.
