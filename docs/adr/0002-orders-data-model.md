# 0002: sales.orders data model, determinism and day boundaries

- Status: accepted
- Date: 2026-11-28

## Context

Iceblink's checks compare its answers with known ones, so `sales.orders` must hold the same bytes on
every run and every machine. The design asks for rows spread across Asia/Kolkata calendar days,
stored as UTC `timestamptz`, and a partition spec of `day(created_at)`.

Iceberg's `day` transform always cuts at UTC midnight. A Kolkata day (UTC+05:30, no daylight
saving) runs from 18:30 UTC the previous day to 18:30 UTC, so it can never fill exactly one
`day(created_at)` partition.

## Options

1. **Kolkata calendar days, UTC partitions.** Each day's rows span the Kolkata day. Each daily
   append writes into two UTC partitions: about 23% of rows (18:30 to 24:00 UTC) in the earlier
   one and 77% in the later one.
2. **UTC calendar days.** Each day's rows span 00:00 to 24:00 UTC, so a daily append fills
   exactly one partition. This matches the design's "57 of 1,204 files" arithmetic, but the
   "days" are no longer Kolkata days.

## Decision

Option 1, chosen by the project owner on 2026-11-28: keep the Kolkata calendar day and let
partitions fall on UTC days. A tenant in India sees its own days, and Iceblink has to prune a
Kolkata-day window that straddles UTC partitions, which is the realistic case.

How the data is made:

- Every value derives from `splitmix64` chained over (seed, day, file index, row index, column
  id). `day` is the calendar date as days since 1970-01-01, so a given date has the same rows
  in every profile with the same file layout. The column id is the column's Iceberg field ID.
  The hash is vectorised in numpy with wrapping uint64 arithmetic.
- Categorical columns use `hash % 10000` against cumulative integer weights in basis points,
  so shares are exact integer comparisons on every platform.
- `amount` is `100 + hash % 2_500_000` paise (₹1.00 to ₹25,000.99), built as a decimal(14,2)
  from the integer, so no float is involved.
- `created_at` is the Kolkata midnight of the day in UTC plus `hash % 86,400,000,000` µs.
- `order_id` is `epoch_day * 1_000_000_000 + position in the day + 1`. That is unique and
  ascending in write order. Rows appended to a day later continue its count, so they stay
  below the next day's ids.
- Appended rows (`make append ROWS=n`) hash with file index `files_per_day + k`, where k counts
  earlier row appends to that day, so they never repeat base rows.

## Consequences

- A daily snapshot adds files in two partitions. pyiceberg sizes files by in-memory Arrow bytes,
  so `write.target-file-size-bytes` is set from the profile's rows per file times the measured
  Arrow bytes per row. With the 23/77 split, a day lands in about `files_per_day` to
  `files_per_day + 1` files. The manifest records the real counts, and nobody types them.
- A 7-day query over Kolkata days touches 8 UTC partitions, and reads part of the two end
  ones. That is expected, and Iceblink's pruning must handle it.
- Changing the hash chain, the weights, the epoch-day key or the column ids changes every
  golden hash. That needs a new ADR and regenerated goldens (`make golden`).
