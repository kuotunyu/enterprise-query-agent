"""Fail-closed MySQL AST validation. Bound values never become SQL syntax."""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

SCHEMA = {
    'orders': 'order_id customer_id order_status order_purchase_timestamp order_approved_at order_delivered_carrier_date order_delivered_customer_date order_estimated_delivery_date',
    'customers': 'customer_id customer_unique_id customer_zip_code_prefix customer_city customer_state',
    'order_items': 'order_id order_item_id product_id seller_id shipping_limit_date price freight_value',
    'order_payments': 'order_id payment_sequential payment_type payment_installments payment_value',
    'products': 'product_id product_category_name',
    'sellers': 'seller_id seller_zip_code_prefix seller_city seller_state',
    'product_category_name_translation': 'product_category_name product_category_name_english',
    'geolocation_zip': 'zip_code_prefix lat lng city state n_points',
    'v_order_gmv': 'order_id n_items gmv',
    'v_order_review': 'order_id review_id review_score review_creation_date review_answer_timestamp',
}
FUNCTIONS = {'SUM', 'COUNT', 'AVG', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'DATE_FORMAT', 'TIME_TO_STR', 'CAST', 'IF', 'CASE', 'AND', 'OR', 'ROW_NUMBER', 'ROUND', 'ABS'}


class PolicyError(ValueError):
    pass


def check_sql(sql: str) -> str:
    try:
        # Compiler/driver use %s, parser accepts placeholders as '?'. Values are never interpolated here.
        parsed = sqlglot.parse(sql.replace('%s', '?'), read='mysql')
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
            raise PolicyError('single SELECT or non-recursive WITH SELECT required')
        tree = parsed[0]
        forbidden = ('Insert', 'Update', 'Delete', 'Create', 'Drop', 'Command', 'Into', 'Lock', 'Parameter', 'SessionParameter', 'PropertyEQ', 'Hint')
        for node in tree.walk():
            if type(node).__name__ in forbidden:
                raise PolicyError(f'forbidden SQL structure: {type(node).__name__}')
            if isinstance(node, exp.With) and node.args.get('recursive'):
                raise PolicyError('recursive CTE not allowed')
            if isinstance(node, exp.Func):
                # MySQL DATE_FORMAT is normalized to TimeToStr with this
                # implicit timestamp conversion by SQLGlot. Its children are
                # still visited; arbitrary anonymous functions stay forbidden.
                if isinstance(node, exp.TsOrDsToTimestamp) and isinstance(node.parent, exp.TimeToStr):
                    continue
                name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
                if name not in FUNCTIONS:
                    raise PolicyError(f'function not allowed: {name}')
            if isinstance(node, exp.Table) and (node.db or node.catalog):
                raise PolicyError('qualified schemas not allowed')
            if node.comments and any('!' in c or '+' in c for c in node.comments):
                raise PolicyError('executable comments/hints not allowed')
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if isinstance(source, exp.Table) and source.name not in SCHEMA:
                    raise PolicyError(f'table not allowed: {source.name}')
        qualify(tree.copy(), dialect='mysql', schema={k:{c:'UNKNOWN' for c in v.split()} for k,v in SCHEMA.items()}, validate_qualify_columns=True)
        return sql
    except PolicyError:
        raise
    except Exception as exc:
        raise PolicyError('SQL parse or column validation failed') from exc
