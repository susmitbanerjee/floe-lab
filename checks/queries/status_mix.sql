-- Rows and revenue by status.
SELECT status, count(*) AS count, sum(amount) AS revenue
FROM sales.orders
GROUP BY status
ORDER BY status;
