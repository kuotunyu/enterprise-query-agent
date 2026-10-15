"""Lexical guard: check_sql returns the caller's bytes and executor.execute
sends them, so the policy has to read the statement the way MySQL's lexer will,
not only the way SQLGlot parsed it.

Two demonstrated divergences are covered. SQLGlot discards /*! and /*+ bodies
in several positions, so the AST comment check at sql_policy never sees them;
and SQLGlot opens a comment at '--' unconditionally, while MySQL needs
whitespace or a control character after the second dash.

This guard closes those two token families. It does not claim to close every
SQLGlot/MySQL parser differential, and it is not free: NARROWED_BY_ESCAPE_MODES
below records valid SQL that was accepted before the guard and is refused now.

Nothing here was executed against MySQL. The lexer rules relied on are the
documented ones; no live validation is claimed.
"""
import pytest
from enterprise_query.sql_policy import check_sql, check_lexical, _scan, ESCAPE_MODES, PolicyError
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
# inside a literal, an identifier or another comment is just characters. Every
# row here was verified accepted by the GUARD before and after; two of them -
# the AST_STRICTER_THAN_MYSQL pair below - are refused by the pre-existing AST
# comment check, at baseline and now, so they are excluded from the
# full-policy corpus. For quoted SQL the guard does NOT preserve, see
# NARROWED_BY_ESCAPE_MODES.
MUST_KEEP = [
    ('bound parameter', 'SELECT order_id FROM orders WHERE order_status = %s'),
    ('two parameters', 'SELECT order_id FROM orders WHERE order_purchase_timestamp >= %s AND order_purchase_timestamp < %s'),
    ('percent literal as execute normalises it', "SELECT DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m') AS `month` FROM orders o"),
    ('single-quoted string holding --', "SELECT order_id FROM orders WHERE order_status = 'a--b'"),
    ('single-quoted string holding /*!', "SELECT order_id FROM orders WHERE order_status = '/*!80400 x */'"),
    ('single-quoted string holding /*+', "SELECT order_id FROM orders WHERE order_status = '/*+ BKA() */'"),
    ('doubled single quote then --', "SELECT order_id FROM orders WHERE order_status = 'it''s --x'"),
    ('double-quoted text holding --', 'SELECT order_id FROM orders WHERE order_status = "a--b"'),
    ('double-quoted text holding /*!', 'SELECT order_id FROM orders WHERE order_status = "/*!80400 x */"'),
    ('double-quoted text holding /*+', 'SELECT order_id FROM orders WHERE order_status = "/*+ BKA() */"'),
    ('doubled double quote', 'SELECT order_id FROM orders WHERE order_status = "a""b"'),
    ('doubled double quote then --', 'SELECT order_id FROM orders WHERE order_status = "a""b--c"'),
    ('double-quoted alias holding --', 'SELECT order_id AS "a--b" FROM orders'),
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


# Recorded, not changed: the AST comment check predates this guard and is
# stricter than MySQL. MySQL does not nest block comments, so a /*! sitting
# inside an already-open comment is inert text, but the AST check sees '!' in
# the attached comment and rejects. The guard accepts these; the policy as a
# whole still refuses them. Left as is - no change to existing AST checks.
AST_STRICTER_THAN_MYSQL = [
    'SELECT order_id FROM orders /* outer /*! inner */',
    'SELECT order_id FROM orders -- /*! inner */',
]

FULL_POLICY_KEEP = [(label, sql) for label, sql in MUST_KEEP if sql not in AST_STRICTER_THAN_MYSQL]


@pytest.mark.parametrize('label,sql', MUST_KEEP, ids=[label for label, _ in MUST_KEEP])
def test_guard_accepts_every_must_keep_row(label, sql):
    """The guard in isolation, including the two AST_STRICTER_THAN_MYSQL rows."""
    assert check_lexical(sql) is None


@pytest.mark.parametrize('label,sql', FULL_POLICY_KEEP, ids=[label for label, _ in FULL_POLICY_KEEP])
def test_full_policy_accepts_and_returns_identical_bytes(label, sql):
    """The whole policy, not just the guard, and the statement comes back as is.

    check_sql's return value is what executor.execute concatenates, so byte
    preservation is the property that matters here. The two rows excluded from
    this corpus are refused by the pre-existing AST comment check, documented
    in the next test; no other baseline rejection was found in this corpus.
    """
    assert check_sql(sql) == sql


@pytest.mark.parametrize('sql', AST_STRICTER_THAN_MYSQL)
def test_inert_comment_text_is_allowed_by_guard_but_refused_by_ast_check(sql):
    assert check_lexical(sql) is None
    with pytest.raises(PolicyError):
        check_sql(sql)


# The guard is conservative, and that costs real SQL. sql_mode is not verified
# by executor._identity, so check_lexical scans every reachable escaping mode
# and keeps the strictest verdict. These statements are valid under default
# sql_mode and WERE accepted before the guard; they are refused now because a
# no-backslash-escape reading sees the string as unterminated. Recorded rather
# than claimed away: the guard does not preserve all previously accepted
# quoted SQL.
#
# Repair is NOT doubling the backslash: 'a\\b' is the string a\b, a different
# value from 'a\'b' (which is a'b). Use a bound parameter, or double the quote
# delimiter being embedded - 'a''b' and "a""b" are the same values and are
# accepted (see MUST_KEEP).
NARROWED_BY_ESCAPE_MODES = [
    ('backslash-escaped single quote', "SELECT order_id FROM orders WHERE order_status = 'a\\'b'"),
    ('backslash-escaped double quote', 'SELECT order_id FROM orders WHERE order_status = "a\\"b"'),
]


@pytest.mark.parametrize('label,sql', NARROWED_BY_ESCAPE_MODES,
                         ids=[label for label, _ in NARROWED_BY_ESCAPE_MODES])
def test_backslash_escaped_quotes_are_refused_by_the_escape_mode_scan(label, sql):
    with pytest.raises(PolicyError, match='unterminated'):
        check_lexical(sql)
    with pytest.raises(PolicyError, match='unterminated'):
        check_sql(sql)


@pytest.mark.parametrize('label,repaired', [
    ('bound parameter', 'SELECT order_id FROM orders WHERE order_status = %s'),
    ('doubled single quote delimiter', "SELECT order_id FROM orders WHERE order_status = 'a''b'"),
    ('doubled double quote delimiter', 'SELECT order_id FROM orders WHERE order_status = "a""b"'),
])
def test_recommended_repairs_for_an_embedded_quote_are_accepted(label, repaired):
    assert check_sql(repaired) == repaired


# ANSI_QUOTES and NO_BACKSLASH_ESCAPES move quoted-region ENDINGS independently
# per quote character, so the guard scans each reachable combination. This
# statement proves the mixed combination is not dead code: under ANSI_QUOTES
# with backslash escapes still active for single quotes the final region really
# is unterminated, and that reading is the ONLY one that sees it.
#
# Scope of what this proves, stated precisely: the mixed pass is live at the
# scanner level. It is NOT an end-to-end bypass that the other two passes would
# have let through - at 9059fe7 the AST stage already refused this statement,
# with 'function not allowed: CONCAT', because SQLGlot reads the adjacent
# literals as implicit concatenation. The mixed pass is here because sql_mode
# is unpinned and the guard must fail closed under every reachable reading,
# not because a reachable end-to-end payload has been demonstrated for it.
MIXED_MODE_ONLY = 'SELECT order_id FROM orders WHERE order_status = ' + '\'\\\'\'"\'\\""'


def test_mixed_escaping_mode_is_the_only_pass_that_catches_this():
    assert ESCAPE_MODES == ((True, True), (True, False), (False, False)), \
        '(False, True) is not a reachable sql_mode; the other three must all be scanned'
    _scan(MIXED_MODE_ONLY, True, True)      # default mode: terminated
    _scan(MIXED_MODE_ONLY, False, False)    # NO_BACKSLASH_ESCAPES: terminated
    with pytest.raises(PolicyError, match='unterminated'):
        _scan(MIXED_MODE_ONLY, True, False)  # ANSI_QUOTES + backslash escapes
    with pytest.raises(PolicyError, match='unterminated'):
        check_lexical(MIXED_MODE_ONLY)


def test_backticks_never_take_backslash_escapes_in_any_mode():
    sql = 'SELECT order_id AS `a\\` FROM orders'
    for sq_escapes, dq_escapes in ESCAPE_MODES:
        # The backtick closes after the backslash in every mode, leaving the
        # rest of the statement outside quotes, so no mode hides anything here.
        _scan(sql, sq_escapes, dq_escapes)


def test_compiler_shapes_still_pass_the_whole_policy():
    for plan in PLANS:
        for query in compile_plan(plan):
            normalised = query.sql.replace('%%', '%')   # as executor.execute does
            assert check_sql(normalised) == normalised


def test_guard_does_not_rewrite_the_statement():
    sql = "SELECT order_id FROM orders WHERE order_status = %s -- note"
    assert check_sql(sql) is sql
