# Troubleshooting

## Old volumes after the rename (Floe to Iceblink)

The compose project is now `iceblink-lab` (ADR 0004). A lab started before the rename ran as
project `floe-lab` (or `rocketlake-lab`), and `make reset` with the new code no longer sees its
containers or volumes. Clean up once:

```bash
make reset                                                   # the new project, if any
docker compose -p floe-lab down --volumes --remove-orphans   # the old project's containers
docker volume ls --format '{{.Name}}' | grep -E '^(floe-lab|rocketlake-lab)_' \
  | xargs -r docker volume rm                                # anything still left
rm .env                                                      # it holds the old FLOE_* names
make up                                                      # recreates .env, then the stack
make data PROFILE=ci                                         # regenerate the data
```

Until you do, `make up` fails because the old containers still hold ports 9000 and 8181.
Python commands fail with "missing ICEBLINK_LAB_S3_ACCESS_KEY" while `.env` has the old names.

This page is on the legacy-name guard's allow-list because it has to name the old volumes.

## "warehouse 'lab' exists on (..., 'http://rustfs:9000')"

A lab bootstrapped before ADR 0005 advertises the store's Compose-only name to clients, so
writes from the host fail with `NETWORK_CONNECTION during CreateMultipartUpload`. Rebuild it:

```bash
make reset
make up
make data PROFILE=ci
```
