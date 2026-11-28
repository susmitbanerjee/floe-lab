-- Floe's region query over the last 7 Kolkata days (2026-11-12 .. 2026-11-18), with a fixed
-- timestamp in place of now() - interval '7 days'.
SELECT region, count(*) AS count, sum(amount) AS sum
FROM sales.orders
WHERE created_at >= TIMESTAMPTZ '2026-11-12 00:00:00+05:30'
GROUP BY region
ORDER BY region;
