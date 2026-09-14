from dataclasses import dataclass
from datetime import date, timedelta
import calendar
from enterprise_query.contracts import QueryPlan, TimeRange


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: tuple
    label: str


def previous_range(t: TimeRange) -> TimeRange:
    if t.start.day == 1 and t.end.day == 1:
        end = t.start
        months = (t.end.year-t.start.year)*12+t.end.month-t.start.month
        year, month = divmod(t.start.year*12+t.start.month-1-months, 12)
        start = date(year, month+1, 1)
    else:
        end = t.start
        start = end-(t.end-t.start)
    return TimeRange(start=start, end=end)


DIMENSIONS = {
    'month': "DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m')",
    'state': 'c.customer_state', 'order':'o.order_id', 'seller':'i.seller_id',
    'category': "COALESCE(t.product_category_name_english, p.product_category_name, 'unknown')",
}


def compile_plan(plan: QueryPlan) -> list[CompiledQuery]:
    ranges = [('current', plan.time_range)]
    if plan.comparison == 'previous_period':
        ranges.append(('previous', previous_range(plan.time_range)))
    return [_compile(plan, t, label) for label,t in ranges]


def _compile(plan, period, label):
    metric = plan.metric_ids[0]
    item_scope = bool({'category','seller'} & (set(plan.dimensions)|{f.field for f in plan.filters}))
    joins = 'FROM orders o JOIN customers c ON c.customer_id=o.customer_id '
    if item_scope:
        joins += 'JOIN order_items i ON i.order_id=o.order_id LEFT JOIN products p ON p.product_id=i.product_id LEFT JOIN product_category_name_translation t ON t.product_category_name=p.product_category_name '
        gmv = 'SUM(i.price+i.freight_value)'
        merchandise = 'SUM(i.price)'
    else:
        # Each aggregate is reduced to one row/order before combining with orders.
        joins += 'LEFT JOIN (SELECT order_id, SUM(price+freight_value) AS gmv, SUM(price) AS merchandise FROM order_items GROUP BY order_id) v ON v.order_id=o.order_id '
        gmv = 'SUM(v.gmv)'
        merchandise = 'SUM(v.merchandise)'
    where = ['o.order_purchase_timestamp >= %s', 'o.order_purchase_timestamp < %s']
    params = [period.start, period.end]
    if metric != 'payment_value':
        where.append("o.order_status = 'delivered'")
    for f in plan.filters:
        expr = DIMENSIONS[f.field]
        if f.operator == 'eq':
            where.append(expr+' = %s')
            params.append(f.value)
        else:
            where.append(expr+' IN ('+','.join(['%s']*len(f.value))+')')
            params.extend(f.value)
    selection = [f'{DIMENSIONS[d]} AS `{d}`' for d in plan.dimensions]
    if metric == 'repeat_customer_rate':
        inner = f'SELECT c.customer_unique_id, COUNT(DISTINCT o.order_id) AS n {joins} WHERE '+ ' AND '.join(where)+' GROUP BY c.customer_unique_id'
        sql = f'SELECT COUNT(*) AS eligible, SUM(CASE WHEN n>=2 THEN 1 ELSE 0 END) AS repeat_customers FROM ({inner}) r'
        return CompiledQuery(sql, tuple(params), label)
    selection.append('COUNT(DISTINCT o.order_id) AS population_count')
    if metric == 'payment_value':
        joins += 'JOIN (SELECT order_id,SUM(payment_value) AS payment_value FROM order_payments GROUP BY order_id) pay ON pay.order_id=o.order_id '
        selection.append('SUM(pay.payment_value) AS payment_value')
    elif metric == 'late_rate':
        eligible = 'o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL'
        selection += [f'SUM(CASE WHEN {eligible} THEN 1 ELSE 0 END) AS eligible',
                      'SUM(CASE WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1 ELSE 0 END) AS late',
                      f'SUM(CASE WHEN {eligible} THEN 0 ELSE 1 END) AS missing']
    elif metric == 'average_review_score':
        joins += 'LEFT JOIN v_order_review rev ON rev.order_id=o.order_id '
        selection += ['SUM(rev.review_score) AS score_sum','COUNT(rev.review_score) AS eligible','SUM(CASE WHEN rev.review_score IS NULL THEN 1 ELSE 0 END) AS missing']
    else:
        selection += [f'{gmv} AS gmv',f'{merchandise} AS merchandise_value','COUNT(DISTINCT o.order_id) AS delivered_order_count']
    sql = 'SELECT '+', '.join(selection)+' '+joins+' WHERE '+' AND '.join(where)
    if plan.dimensions:
        sql += ' GROUP BY '+', '.join(DIMENSIONS[d] for d in plan.dimensions)
        order = []
        if plan.sort == 'value_desc':
            # Rank the complete grouped result before the executor's transfer cap.
            # Cast ratios at high precision; MySQL's default integer division scale
            # can otherwise turn distinct rates into ties at the cutoff.
            ranking = {
                'aov': f'CAST({gmv} AS DECIMAL(65,30))/NULLIF(COUNT(DISTINCT o.order_id),0)',
                'late_rate': 'CAST(late AS DECIMAL(65,30))/NULLIF(eligible,0)',
                'average_review_score': 'CAST(score_sum AS DECIMAL(65,30))/NULLIF(eligible,0)',
            }.get(metric, metric)
            order.append(ranking+' DESC')
        order.extend(DIMENSIONS[d] for d in plan.dimensions)
        sql += ' ORDER BY '+', '.join(order)+' LIMIT 201'
    # Escape format-literal % for the DB-API parameter protocol.
    sql = sql.replace('%Y', '%%Y').replace('%m', '%%m')
    return CompiledQuery(sql, tuple(params), label)
