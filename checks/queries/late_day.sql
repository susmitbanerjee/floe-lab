-- The anchor day only (Kolkata 2026-11-18), the day `make append ROWS=n` adds to.
SELECT status, count(*) AS count, sum(amount) AS sum
FROM sales.orders
WHERE created_at >= TIMESTAMPTZ '2026-11-18 00:00:00+05:30'
  AND created_at < TIMESTAMPTZ '2026-11-19 00:00:00+05:30'
GROUP BY status
ORDER BY status;
