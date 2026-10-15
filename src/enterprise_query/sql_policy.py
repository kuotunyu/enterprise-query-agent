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


# MySQL ends the '--' opener on whitespace or a control character; anything else
# makes the two dashes arithmetic. SQLGlot opens a comment either way.
AFTER_DASHES = ' \t\n\r\f\v\0'


def _skip_line(sql, i):
    ends = [p for p in (sql.find('\n', i), sql.find('\r', i)) if p >= 0]
    return min(ends) if ends else len(sql)


def _skip_quoted(sql, i, escapes):
    quote = sql[i]
    j = i+1
    while j < len(sql):
        if escapes and sql[j] == '\\':
            j += 2
            continue
        if sql[j] == quote:
            # A doubled delimiter is an escaped delimiter, not the end.
            if sql[j+1:j+2] == quote:
                j += 2
                continue
            return j+1
        j += 1
    raise PolicyError('unterminated quoted string or identifier')


def _scan(sql, sq_escapes, dq_escapes):
    """Reject executable comments and ambiguous '--' found outside quoted text.

    This reads the statement with MySQL's lexer rules rather than SQLGlot's,
    because check_sql returns the caller's bytes and executor.execute sends
    them: anything SQLGlot drops or re-reads differently still reaches the
    server. SQLGlot discards /*! and /*+ bodies in several positions, so the
    AST comment check alone cannot see them.
    """
    i = 0
    while i < len(sql):
        c = sql[i]
        if sql.startswith('/*', i):
            if sql.startswith('/*!', i) or sql.startswith('/*+', i):
                raise PolicyError('executable comment or optimizer hint not allowed')
            end = sql.find('*/', i+2)
            if end < 0:
                raise PolicyError('unterminated block comment')
            i = end+2
        elif sql.startswith('--', i):
            after = sql[i+2:i+3]
            # Empty means end of statement, where execute appends a newline.
            if after and after not in AFTER_DASHES:
                raise PolicyError("'--' is only a comment when followed by whitespace")
            i = _skip_line(sql, i+2)
        elif c == '#':
            i = _skip_line(sql, i+1)
        elif c in '\'"`':
            # Escaping is per quote character: backslash is never an escape inside
            # a backtick identifier, and under ANSI_QUOTES a double-quoted region
            # is an identifier too. check_lexical supplies each combination.
            i = _skip_quoted(sql, i, {'\'': sq_escapes, '"': dq_escapes, '`': False}[c])
        else:
            i += 1


# (single-quote escapes, double-quote escapes) per reachable sql_mode. Backticks
# never take backslash escapes, in any mode.
#   (True,  True )  default: backslash escapes, '"' is a string
#   (True,  False)  ANSI_QUOTES: backslash escapes, '"' is an identifier
#   (False, False)  NO_BACKSLASH_ESCAPES, with or without ANSI_QUOTES
# (False, True) is omitted: no mode disables backslash escapes for "'" while
# leaving them active for '"'.
ESCAPE_MODES = ((True, True), (True, False), (False, False))


def check_lexical(sql: str) -> None:
    """Fail closed under every sql_mode the server could be running.

    sql_mode is not part of the verified identity contract (executor._identity
    checks database, user, version, grants and data version only), so neither
    NO_BACKSLASH_ESCAPES nor ANSI_QUOTES can be assumed off. Both change where
    a quoted region ENDS, not merely what it means: with backslash escapes,
    'a\\'b' is one string, and without them it is the string 'a\\' followed by
    b'. ANSI_QUOTES makes a double-quoted region an identifier, where backslash
    is not an escape, so its ending moves the same way - and independently of
    the single-quote reading, which is why escaping is modelled per quote
    character rather than with one flag for both.

    Scanning every reachable combination and keeping the strictest verdict is
    the protection; there is no invariance to appeal to. The cost is that a
    backslash-escaped quote, valid under default sql_mode, is refused because
    another reading sees the string as unterminated. Repair such a statement
    with a bound parameter, or by doubling the quote delimiter it embeds -
    tests/test_sql_lexical_guard.py records those rows.
    """
    for sq_escapes, dq_escapes in ESCAPE_MODES:
        _scan(sql, sq_escapes, dq_escapes)


def check_sql(sql: str) -> str:
    try:
        check_lexical(sql)
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
