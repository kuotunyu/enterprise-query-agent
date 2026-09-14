CREATE OR REPLACE SQL SECURITY INVOKER VIEW v_order_gmv AS
SELECT order_id, COUNT(*) AS n_items, SUM(price+freight_value) AS gmv FROM order_items GROUP BY order_id;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW v_order_review AS
SELECT order_id,review_id,review_score,review_creation_date,review_answer_timestamp
FROM (SELECT order_id,review_id,review_score,review_creation_date,review_answer_timestamp,
ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_creation_date DESC,review_answer_timestamp DESC,review_id DESC) AS rn FROM order_reviews) r WHERE rn=1;
