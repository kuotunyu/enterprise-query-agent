"""Hand-authored direct-SQL fixtures for offline B plumbing, not model output quality.

Independent of both the A SQL template catalog and the C compiler. A real B model
returns the complete statement text directly; these fixtures only stand in for it.
"""
WHERE="o.order_purchase_timestamp >= %s AND o.order_purchase_timestamp < %s AND (%s IS NULL OR c.customer_state=%s)"
BASE=' FROM orders o JOIN customers c ON o.customer_id=c.customer_id '
DELIVERED=WHERE+" AND o.order_status='delivered'"
CTE='WITH totals AS (SELECT order_id,SUM(price) AS merchandise,SUM(price+freight_value) AS amount FROM order_items GROUP BY order_id) '
DIRECT_SQL={
 'sales_total': CTE+'SELECT COUNT(o.order_id) AS population_count,SUM(totals.amount) AS gmv,SUM(totals.merchandise) AS merchandise_value,COUNT(o.order_id) AS delivered_order_count'+BASE+'LEFT JOIN totals ON totals.order_id=o.order_id WHERE '+DELIVERED,
 'payment_total': 'WITH payments AS (SELECT order_id,SUM(payment_value) AS paid FROM order_payments GROUP BY order_id) SELECT COUNT(o.order_id) AS population_count,SUM(payments.paid) AS payment_value'+BASE+'JOIN payments ON payments.order_id=o.order_id WHERE '+WHERE,
 'repeat_total': 'WITH customers_in_period AS (SELECT c.customer_unique_id,COUNT(o.order_id) AS orders_in_period'+BASE+'WHERE '+DELIVERED+' GROUP BY c.customer_unique_id) SELECT COUNT(*) AS eligible,SUM(CASE WHEN orders_in_period>=2 THEN 1 ELSE 0 END) AS repeat_customers FROM customers_in_period',
 'review_total': 'SELECT COUNT(*) AS population_count,COUNT(review.review_score) AS eligible,SUM(review.review_score) AS score_sum,COUNT(*)-COUNT(review.review_score) AS missing'+BASE+'LEFT JOIN v_order_review review ON review.order_id=o.order_id WHERE '+DELIVERED,
 'late_total': 'SELECT COUNT(*) AS population_count,COUNT(o.order_delivered_customer_date)-SUM(CASE WHEN o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NULL THEN 1 ELSE 0 END) AS eligible,SUM(CASE WHEN o.order_delivered_customer_date>o.order_estimated_delivery_date THEN 1 ELSE 0 END) AS late,SUM(CASE WHEN o.order_delivered_customer_date IS NULL OR o.order_estimated_delivery_date IS NULL THEN 1 ELSE 0 END) AS missing'+BASE+'WHERE '+DELIVERED,
}
GROUPED={
 'sales_category':("COALESCE(tr.product_category_name_english,p.product_category_name,'unknown')",'category',' LEFT JOIN products p ON p.product_id=item.product_id LEFT JOIN product_category_name_translation tr ON tr.product_category_name=p.product_category_name'),
 'sales_state':('c.customer_state','state',''),
 'sales_seller':('item.seller_id','seller',''),
 'sales_order':('o.order_id','order',''),
}
# Fixed fixture variants, selected by mock intent. No SQL from a real provider is
# ever passed through these constructors or corrected by them.
for fixture,(expression,alias,joins) in GROUPED.items():
    item_join='LEFT JOIN' if alias in ('state','order') else 'JOIN'
    body='SELECT '+expression+' AS `'+alias+'`,COUNT(DISTINCT o.order_id) AS population_count,SUM(item.price+item.freight_value) AS gmv,SUM(item.price) AS merchandise_value,COUNT(DISTINCT o.order_id) AS delivered_order_count'+BASE+item_join+' order_items item ON item.order_id=o.order_id'+joins+' WHERE '+DELIVERED+' GROUP BY '+expression
    DIRECT_SQL[fixture]=body+' ORDER BY '+expression+' LIMIT 201'
    DIRECT_SQL[fixture+'_gmv_rank']=body+' ORDER BY gmv DESC,'+expression+' LIMIT 201'
