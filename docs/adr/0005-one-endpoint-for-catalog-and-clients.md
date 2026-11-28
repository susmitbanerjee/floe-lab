# 0005: One S3 endpoint for Lakekeeper and its clients

- Status: accepted
- Date: 2026-11-28

## Context

The warehouse's storage profile had `endpoint: http://rustfs:9000`, the store's name on the
Compose network, because Lakekeeper must reach the store to validate the profile and write
metadata. Lakekeeper also returns that endpoint to clients in every table's config
(`s3.endpoint`), and pyiceberg prefers the table config over the client's own `s3.endpoint`.
So every client outside the Compose network wrote to `rustfs:9000`, which does not resolve
there. That includes `make data` on a laptop or a CI runner, and Iceblink itself. The writes
fail with `NETWORK_CONNECTION during CreateMultipartUpload`.

CI's stack job has failed this way since the data tests arrived (2daa909). Local runs passed
only because they ran inside the Compose network, where `rustfs` resolves.

Lakekeeper v0.15.1 has one `endpoint` per storage profile and no separate address for clients.

## Options

1. **Override on the client.** Replace the table's FileIO properties after loading. This needs
   pyiceberg internals, and every other client, Iceblink included, would need the same trick.
2. **`host.docker.internal:9000` in the profile.** Docker Desktop resolves it, but a Linux host
   (CI) does not without extra setup, and inside Lakekeeper's container it needs `extra_hosts`.
3. **Run Lakekeeper in RustFS's network namespace** (`network_mode: service:rustfs`). Then
   `http://127.0.0.1:9000` is the store both inside Lakekeeper and on the host, and the profile
   can advertise it.

## Decision

Option 3. Lakekeeper runs with `network_mode: service:rustfs`. The rustfs service publishes
both `127.0.0.1:9000` (S3) and `127.0.0.1:8181` (Lakekeeper). The warehouse endpoint is
`http://127.0.0.1:9000` (`Settings.warehouse_endpoint`). Bootstrap now also checks the
endpoint of an existing warehouse and asks for `make reset` when it differs.

## Consequences

- `make data`, `make check`, the stack tests and Iceblink work from the host, in CI and on a
  laptop, with one address for the store and one for the catalog.
- A stack test checks that a loaded table's FileIO uses `http://127.0.0.1:9000`.
- Lakekeeper's network lives in the rustfs container. If rustfs is recreated on its own,
  restart Lakekeeper too; `make up` and `make reset` handle both.
- A client in another container must join that namespace too
  (`--network container:iceblink-lab-rustfs-1`). A client on the plain Compose network would
  get `127.0.0.1:9000` from the catalog and reach itself instead.
- A lab bootstrapped before this change has a warehouse with `http://rustfs:9000/`; `make up`
  says so and asks for `make reset`.
