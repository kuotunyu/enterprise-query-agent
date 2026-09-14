"""Baseline A: finite published SQL templates; typed slots only, no semantic compiler."""
from datetime import date, timedelta
from dataclasses import dataclass

@dataclass(frozen=True)
class Statement:
    sql: str
    parameters: tuple
    label: str

TIME_STATE = 'o.order_purchase_timestamp >= %s AND o.order_purchase_timestamp < %s AND (%s IS NULL OR c.customer_state = %s)'
DELIVERED = TIME_STATE + " AND o.order_status = 'delivered'"
ITEMS = ' FROM orders o JOIN customers c ON c.customer_id=o.customer_id JOIN order_items i ON i.order_id=o.order_id '
ORDER_ITEMS = ' FROM orders o JOIN customers c ON c.customer_id=o.customer_id LEFT JOIN order_items i ON i.order_id=o.order_id '
SALES = 'COUNT(DISTINCT o.order_id) AS population_count, SUM(i.price+i.freight_value) AS gmv, SUM(i.price) AS merchandise_value, COUNT(DISTINCT o.order_id) AS delivered_order_count'
CATEGORY = "COALESCE(t.product_category_name_english,p.product_category_name,'unknown')"
PRODUCTS = ' LEFT JOIN products p ON p.product_id=i.product_id LEFT JOIN product_category_name_translation t ON t.product_category_name=p.product_category_name '
ORDERS = ' FROM orders o JOIN customers c ON c.customer_id=o.customer_id '

# These are fixed statement bodies. No model-provided expression is interpolated.
TEMPLATES = {
 'sales_total': 'SELECT '+SALES+ORDER_ITEMS+' WHERE '+DELIVERED,
 'sales_category': 'SELECT '+CATEGORY+' AS category, '+SALES+ITEMS+PRODUCTS+' WHERE '+DELIVERED+' GROUP BY '+CATEGORY+' ORDER BY '+CATEGORY+' LIMIT 201',
 'sales_state': 'SELECT c.customer_state AS state, '+SALES+ORDER_ITEMS+' WHERE '+DELIVERED+' GROUP BY c.customer_state ORDER BY c.customer_state LIMIT 201',
 'sales_seller': 'SELECT i.seller_id AS seller, '+SALES+ITEMS+' WHERE '+DELIVERED+' GROUP BY i.seller_id ORDER BY i.seller_id LIMIT 201',
 'sales_order': 'SELECT o.order_id AS `order`, '+SALES+ORDER_ITEMS+' WHERE '+DELIVERED+' GROUP BY o.order_id ORDER BY o.order_id LIMIT 201',
 'payment_total': 'SELECT COUNT(DISTINCT o.order_id) AS population_count, SUM(pay.payment_value) AS payment_value'+ORDERS+'JOIN order_payments pay ON pay.order_id=o.order_id WHERE '+TIME_STATE,
 'repeat_total': 'SELECT COUNT(*) AS eligible, SUM(CASE WHEN x.n>=2 THEN 1 ELSE 0 END) AS repeat_customers FROM (SELECT c.customer_unique_id,COUNT(*) AS n'+ORDERS+'WHERE '+DELIVERED+' GROUP BY c.customer_unique_id) x',
 'review_total': 'SELECT COUNT(o.order_id) AS population_count, SUM(r.review_score) AS score_sum,COUNT(r.review_score) AS eligible,SUM(CASE WHEN r.review_score IS NULL THEN 1 ELSE 0 END) AS missing'+ORDERS+'LEFT JOIN v_order_review r ON r.order_id=o.order_id WHERE '+DELIVERED,
 'late_total': 'SELECT COUNT(o.order_id) AS population_count,SUM(CASE WHEN o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL THEN 1 ELSE 0 END) AS eligible,SUM(CASE WHEN o.order_delivered_customer_date>o.order_estimated_delivery_date THEN 1 ELSE 0 END) AS late,SUM(CASE WHEN o.order_delivered_customer_date IS NULL OR o.order_estimated_delivery_date IS NULL THEN 1 ELSE 0 END) AS missing'+ORDERS+'WHERE '+DELIVERED,
}
DIMENSION = {'sales_category':['category'],'sales_state':['state'],'sales_seller':['seller'],'sales_order':['order']}
SPECIAL = {'payment_total':'payment_value','repeat_total':'repeat_customer_rate','review_total':'average_review_score','late_total':'late_rate'}

def prior(period):
    """Shared period arithmetic, independent of C's SQL compiler."""
    if period.start.day==period.end.day==1:
        months=(period.end.year-period.start.year)*12+period.end.month-period.start.month
        year,month=divmod(period.start.year*12+period.start.month-1-months,12)
        return date(year,month+1,1),period.start
    return period.start-(period.end-period.start),period.start

def bind_template(slots):
    parameters=(slots.period.start,slots.period.end,slots.state,slots.state)
    statements=[Statement(TEMPLATES[slots.template_id],parameters,'current')]
    if slots.comparison=='previous_period':
        start,end=prior(slots.period)
        statements.append(Statement(TEMPLATES[slots.template_id],(start,end,slots.state,slots.state),'previous'))
    return statements
