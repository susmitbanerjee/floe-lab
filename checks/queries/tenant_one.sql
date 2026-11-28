-- One tenant's orders over the 30 Kolkata days 2026-10-20 .. 2026-11-18. Partitions prune
-- the dates, but nothing can prune the tenant filter.
SELECT order_id, customer_id, region, status, amount
FROM sales.orders
WHERE tenant_id = 't-0042'
  AND created_at >= TIMESTAMPTZ '2026-10-20 00:00:00+05:30'
  AND created_at < TIMESTAMPTZ '2026-11-19 00:00:00+05:30'
ORDER BY order_id;
