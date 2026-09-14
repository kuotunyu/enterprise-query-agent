from decimal import Decimal as D
import importlib.util
import os
import pytest
from enterprise_query.contracts import QueryPlan


def test_engine_exists():
    assert importlib.util.find_spec('enterprise_query.compiler'), 'compiler not implemented'


def test_filter_injection_is_bound():
    from enterprise_query.compiler import compile_plan
    value = "SP' OR 1=1 --"
    plan = QueryPlan(time_range={'start':'2018-07-01','end':'2018-08-01'}, filters=[{'field':'state','value':value}])
    q = compile_plan(plan)[0]
    assert value not in q.sql and value in q.parameters


@pytest.fixture
def engine():
    if os.getenv('EQA_INTEGRATION') != '1':
        pytest.skip('requires EQA_INTEGRATION=1 and isolated MySQL')
    from enterprise_query.executor import Executor
    return Executor()


def answer(engine, metrics, dimensions=(), comparison='none', filters=(), start='2018-07-01', end='2018-08-01'):
    from enterprise_query.compiler import compile_plan
    from enterprise_query.facts import build_answer
    p = QueryPlan(metric_ids=metrics, dimensions=list(dimensions), comparison=comparison, filters=list(filters), time_range={'start':start,'end':end})
    return build_answer(p, [engine.execute(q.sql, q.parameters) for q in compile_plan(p)])


@pytest.mark.integration
def test_D01_D12_real_mysql(engine):
    result = answer(engine, ['gmv','delivered_order_count','aov'])
    assert [f.value for f in result.facts] == [D('242'), 3, D('242')/3]
    assert result.facts[2].display == '80.67 BRL'
    assert answer(engine,['merchandise_value']).facts[0].value == D('220')
    assert [r['gmv'] for r in answer(engine,['gmv'],['category']).table] == [D('154'),D('88')]
    assert answer(engine,['payment_value']).facts[0].value == D('459')
    repeat = answer(engine,['repeat_customer_rate'])
    assert repeat.facts[0].value == D('0.5')
    assert repeat.table[0]['eligible'] == 2 and repeat.table[0]['repeat_customers'] == 1
    review = answer(engine,['average_review_score'])
    assert review.facts[0].value == D('4')
    assert review.table[0]['eligible'] == 2 and review.table[0]['missing'] == 1
    late = answer(engine,['late_rate'])
    assert late.facts[0].value == D('0.5')
    assert late.table[0]['eligible'] == 2 and late.table[0]['late'] == 1 and late.table[0]['missing'] == 1
    comparison = answer(engine,['gmv'],comparison='previous_period')
    assert comparison.table[0]['gmv_change'] == D('132')
    assert comparison.table[0]['gmv_growth'] == D('1.2')
    categories = answer(engine,['gmv'],['category'],'previous_period').table
    assert [r['gmv_change'] for r in categories] == [D('44'),D('88')]
    assert categories[1]['gmv_growth'] is None
    assert [r['aov'] for r in answer(engine,['aov'],['category']).table] == [D('77'), D('44')]
    assert answer(engine,['gmv'],filters=[{'field':'state','value':'SP'}]).facts[0].value == D('209')


@pytest.mark.integration
def test_empty_partial_and_evidence(engine):
    assert answer(engine,['gmv'],start='2026-08-01',end='2026-09-01').status == 'empty'
    a = answer(engine,['gmv'],comparison='previous_period',start='2018-07-10',end='2018-08-01')
    assert a.table[0]['gmv_growth'] is None and a.coverage == 'partial/unknown'
    e = a.evidence[0]
    assert e.sql and e.parameters and e.column_types and len(e.result_hash)==64


@pytest.mark.integration
def test_runtime_permissions_and_identity(engine):
    import pymysql
    assert engine.check_identity()['dataset_id'] == 'synthetic-v1'
    with engine.connect() as conn:
        for sql in ['DELETE FROM orders', 'CREATE TABLE forbidden(x INT)', "SELECT * FROM orders INTO OUTFILE '/tmp/eqa-forbidden'"]:
            with pytest.raises(pymysql.MySQLError):
                conn.cursor().execute(sql)


@pytest.mark.integration
def test_real_timeout_stops_query(engine):
    import time
    from enterprise_query.executor import QueryTimeout
    sql = 'SELECT SUM(a.price*b.price*c.price*d.price*e.price*f.price*g.price*h.price*i.price*j.price*k.price) AS total FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c CROSS JOIN order_items d CROSS JOIN order_items e CROSS JOIN order_items f CROSS JOIN order_items g CROSS JOIN order_items h CROSS JOIN order_items i CROSS JOIN order_items j CROSS JOIN order_items k'
    started = time.monotonic()
    with pytest.raises(QueryTimeout):
        engine.execute(sql, timeout_ms=100)
    assert time.monotonic()-started < 3
    with engine.connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SHOW PROCESSLIST')
            assert not any('SUM(a.price' in str(row.get('Info') or '') for row in cur.fetchall())


@pytest.mark.integration
def test_truncation_and_injected_filter(engine):
    r = engine.execute('SELECT a.order_id FROM orders a CROSS JOIN orders b CROSS JOIN orders c CROSS JOIN orders d')
    assert len(r.rows)==200 and r.truncated
    assert answer(engine,['gmv'],filters=[{'field':'state','value':"SP' OR 1=1 --"}]).status == 'empty'
