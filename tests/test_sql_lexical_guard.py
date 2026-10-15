"""Lexical guard: check_sql returns the caller's bytes and executor.execute
sends them, so the policy has to read the statement the way MySQL's lexer will,
not only the way SQLGlot parsed it.

Two demonstrated divergences are covered. SQLGlot discards /*! and /*+ bodies
in several positions, so the AST comment check at sql_policy never sees them;
and SQLGlot opens a comment at '--' unconditionally, while MySQL needs
whitespace or a control character after the second dash.

This guard closes those two token families. It does not claim to close every
SQLGlot/MySQL parser differential.
"""
import pytest
from enterprise_query.sql_policy import check_sql, check_lexical, PolicyError
from enterprise_query.compiler import compile_plan
from enterprise_query.contracts import QueryPlan

# Accepted before the guard, and executed by MySQL 8.4 as something the AST
# allow-list never saw. These are the red cases.
BYPASSES = [
    # /*!NNNNN ... */ bodies run when the server version is at least NNNNN.
    'SELECT o.order_id FROM orders o JOIN customers c ON /*!80400 SLEEP(10) OR */ c.customer_id = o.customer_id',
    'WITH t AS (/*!80400 SELECT user AS order_id FROM mysql.user UNION */ SELECT order_id FROM orders) SELECT COUNT(*) AS n FROM t',
    'SELECT COUNT(/*!80400 DISTINCT */ order_id) AS n FROM orders',
    'SELECT order_id FROM orders /*!80400 UNION SELECT 1 */',
    # Optimizer hints in the same discarded positions; only the canonical
    # position after SELECT reaches exp.Hint.
    'SELECT o.order_id FROM orders o JOIN customers c ON /*+ BKA(c) */ c.customer_id = o.customer_id',
    'WITH t AS (/*+ QB_NAME(x) */ SELECT order_id FROM orders) SELECT COUNT(*) AS n FROM t',
    'SELECT COUNT(/*+ BKA() */ order_id) AS n FROM orders',
    # MySQL reads '--' with no following whitespace as two minus signs.
    'SELECT order_id FROM orders WHERE order_id = 1 --SLEEP(10)',
    'SELECT SUM(price) AS s FROM order_items WHERE price > 0 --(SELECT COUNT(*) FROM eqa_metadata)',
    'SELECT COUNT(*) AS n FROM orders WHERE 1 = 1 --BENCHMARK(5000000,SHA1(%s))',
]

# Unterminated input must fail closed rather than be guessed at.
MALFORMED = [
    "SELECT order_id FROM orders WHERE order_status = 'abc",
    'SELECT order_id FROM orders /* never closed',
    'SELECT order_id AS `abc FROM orders',
    "SELECT order_id FROM orders WHERE order_status = 'a\\'",   # ambiguous under NO_BACKSLASH_ESCAPES
]

# Must keep being accepted. Quoted text is data, not syntax: a comment marker
# inside a literal, an identifier or another comment is just characters.
MUST_KEEP = [
    ('bound parameter', 'SELECT order_id FROM orders WHERE order_status = %s'),
    ('two parameters', 'SELECT order_id FROM orders WHERE order_purchase_timestamp >= %s AND order_purchase_timestamp < %s'),
    ('percent literal as execute normalises it', "SELECT DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m') AS `month` FROM orders o"),
    ('single-quoted string holding --', "SELECT order_id FROM orders WHERE order_status = 'a--b'"),
    ('single-quoted string holding /*!', "SELECT order_id FROM orders WHERE order_status = '/*!80400 x */'"),
    ('single-quoted string holding /*+', "SELECT order_id FROM orders WHERE order_status = '/*+ BKA() */'"),
    ('doubled single quote then --', "SELECT order_id FROM orders WHERE order_status = 'it''s --x'"),
    ('backtick identifier holding --', 'SELECT order_id AS `a--b` FROM orders'),
    ('doubled backtick identifier', 'SELECT order_id AS `a``b` FROM orders'),
    ('escaped backslash before quote', "SELECT order_id FROM orders WHERE order_status = 'a\\\\b'"),
    ('backslash not before a quote', "SELECT order_id FROM orders WHERE order_status = 'a\\nb'"),
    ('valid line comment', 'SELECT order_id FROM orders -- trailing note'),
    ('valid hash comment', 'SELECT order_id FROM orders # trailing note'),
    ('valid block comment', 'SELECT order_id FROM orders /* plain note */'),
    ('comment marker nested as comment text', 'SELECT order_id FROM orders /* outer /*! inner */'),
    ('exec marker inside a line comment', 'SELECT order_id FROM orders -- /*! inner */'),
    ('ambiguous dashes inside a line comment', 'SELECT order_id FROM orders -- --SLEEP(10)'),
    ('quote inside a line comment', "SELECT order_id FROM orders -- it's fine"),
    ('line comment then real sql', 'SELECT order_id\n-- note\nFROM orders'),
    ('dashes at end of statement', 'SELECT order_id FROM orders --'),
    ('genuine double negation', 'SELECT 1 - -2 AS n FROM orders'),
    ('statement terminator', 'SELECT order_id FROM orders;'),
]

PLANS = [
    QueryPlan(metric_ids=['gmv'], time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['merchandise_value'], dimensions=['month'], time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['gmv'], dimensions=['category'], sort='value_desc', time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['late_rate'], dimensions=['state'], sort='value_desc', time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['repeat_customer_rate'], time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['payment_value'], comparison='previous_period', time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
    QueryPlan(metric_ids=['average_review_score'], time_range={'start': '2018-07-01', 'end': '2018-08-01'}),
]


@pytest.mark.parametrize('sql', BYPASSES)
def test_guard_rejects_statements_whose_mysql_meaning_escaped_the_ast(sql):
    with pytest.raises(PolicyError):
        check_sql(sql)


@pytest.mark.parametrize('sql', MALFORMED)
def test_guard_fails_closed_on_unterminated_input(sql):
    with pytest.raises(PolicyError):
        check_sql(sql)


def test_ambiguous_dashes_and_their_mysql_equivalent_agree():
    """MySQL tokenises these two identically, so the policy must too."""
    for sql in ('SELECT order_id FROM orders WHERE order_id = 1 --SLEEP(10)',
                'SELECT 1 - -SLEEP(10) AS n FROM orders'):
        with pytest.raises(PolicyError):
            check_sql(sql)


@pytest.mark.parametrize('label,sql', MUST_KEEP, ids=[label for label, _ in MUST_KEEP])
def test_guard_keeps_accepted_sql_byte_for_byte(label, sql):
    # The guard alone. Two rows below are rejected by the pre-existing AST
    # comment check rather than by the guard; see the next test.
    assert check_lexical(sql) is None


# Recorded, not changed: the AST comment check predates this guard and is
# stricter than MySQL. MySQL does not nest block comments, so a /*! sitting
# inside an already-open comment is inert text, but the AST check sees '!' in
# the attached comment and rejects. The guard accepts these; the policy as a
# whole still refuses them. Left as is - no change to existing AST checks.
AST_STRICTER_THAN_MYSQL = [
    'SELECT order_id FROM orders /* outer /*! inner */',
    'SELECT order_id FROM orders -- /*! inner */',
]


@pytest.mark.parametrize('sql', AST_STRICTER_THAN_MYSQL)
def test_inert_comment_text_is_allowed_by_guard_but_refused_by_ast_check(sql):
    assert check_lexical(sql) is None
    with pytest.raises(PolicyError):
        check_sql(sql)


def test_compiler_shapes_still_pass_the_whole_policy():
    for plan in PLANS:
        for query in compile_plan(plan):
            normalised = query.sql.replace('%%', '%')   # as executor.execute does
            assert check_sql(normalised) == normalised


def test_guard_does_not_rewrite_the_statement():
    sql = "SELECT order_id FROM orders WHERE order_status = %s -- note"
    assert check_sql(sql) is sql
