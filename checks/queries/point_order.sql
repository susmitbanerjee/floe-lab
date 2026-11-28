-- A single-row lookup: the first order of the anchor day 2026-11-18 (epoch day 20775).
SELECT order_id, tenant_id, customer_id, region, status, amount
FROM sales.orders
WHERE order_id = 20775000000001;
