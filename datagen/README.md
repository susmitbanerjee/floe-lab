# datagen

Deterministic lab data: `generate.py` makes sales.orders rows from splitmix64 (ADR 0002),
`write.py` writes them through the Iceberg catalog and the manifest, `tpch.py` loads TPC-H.
`bootstrap.py` creates the bucket and warehouse; `smoke.py` checks S3 and the catalog.
