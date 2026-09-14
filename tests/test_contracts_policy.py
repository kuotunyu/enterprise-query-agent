import importlib.util
import pytest


def test_core_available():
    assert importlib.util.find_spec('enterprise_query.contracts'), 'typed contracts not implemented'


@pytest.mark.parametrize('sql', [
    'DELETE FROM orders', 'SELECT * FROM orders; DROP TABLE orders',
    'SELECT * FROM mysql.user', 'WITH x AS (SELECT * FROM mysql.user) SELECT * FROM x',
    "SELECT * FROM orders INTO OUTFILE '/tmp/x'", 'SELECT SLEEP(1)',
    'SELECT BENCHMARK(10,1)', "SELECT LOAD_FILE('/etc/passwd')", 'SELECT * FROM orders FOR UPDATE',
    'SELECT @x', 'SELECT @@version', 'SELECT mystery(order_id) FROM orders',
    'SELECT review_comment_message FROM reviews', 'SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM orders',
    'WITH RECURSIVE t AS (SELECT 1 UNION ALL SELECT 1 FROM t) SELECT * FROM t',
    'SELECT no_such_column FROM orders',
])
def test_policy_rejects(sql):
    from enterprise_query.sql_policy import check_sql, PolicyError
    with pytest.raises(PolicyError):
        check_sql(sql)


def test_cte_and_parameter_are_legal():
    from enterprise_query.sql_policy import check_sql
    check_sql('WITH t AS (SELECT order_id FROM orders) SELECT COUNT(*) AS n FROM t')
    check_sql('SELECT order_id FROM orders WHERE order_status = %s')
    check_sql('SELECT SUM(CASE WHEN order_status = %s AND customer_id IS NOT NULL THEN 1 ELSE 0 END) AS n FROM orders')


def test_plan_rejects_unsupported_payment_allocation_and_raw_sql():
    from enterprise_query.contracts import QueryPlan
    from pydantic import ValidationError
    for extra in [{'metric_ids':['payment_value'], 'dimensions':['category']}, {'raw_sql':'SELECT 1'}, {'metric_ids':['profit']}]:
        with pytest.raises(ValidationError):
            QueryPlan.model_validate({'time_range':{'start':'2018-07-01','end':'2018-08-01'}, **extra})
