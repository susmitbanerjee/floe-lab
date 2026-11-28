# datagen

Python package that generates deterministic lab data and writes it to storage.
Every value derives from a seed and row coordinates. Code lives in `src/datagen/`.
`bootstrap.py` creates the bucket and warehouse; `smoke.py` checks S3 and the catalog.
