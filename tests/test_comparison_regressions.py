from datetime import date
from decimal import Decimal as D
import os

import pytest
import sqlglot
from sqlglot import exp

from enterprise_query.compiler import compile_plan, previous_range
from enterprise_query.contracts import QueryPlan, QueryResult, TimeRange
from enterprise_query.facts import build_answer


def evidence(query_id, rows, truncated=False):
    return QueryResult(query_id=query_id, sql='SELECT ...', parameters=[],
                       columns=list(rows[0]) if rows else [], column_types=[], rows=rows,
                       row_count=len(rows), truncated=truncated, elapsed_ms=1,
                       dataset_id='synthetic-v1', result_hash='a' * 64)


def plan(**kwargs):
    return QueryPlan(time_range={'start': '2018-07-01', 'end': '2018-08-01'}, **kwargs)


def test_month_and_state_comparison_aligns_previous_month():
    p = plan(dimensions=['month', 'state'], comparison='previous_period')
    a = build_answer(p, [
        evidence('current', [{'month': '2018-07', 'state': 'SP', 'gmv': D(209), 'population_count': 2}]),
        evidence('previous', [{'month': '2018-06', 'state': 'SP', 'gmv': D(110), 'population_count': 1}]),
    ])
    assert len(a.table) == 1
    assert a.table[0]['month'] == '2018-07'
    assert a.table[0]['gmv_change'] == D(99)
    assert a.table[0]['gmv_growth'] == D('0.9')


def test_multimonth_comparison_uses_relative_months_and_exact_evidence():
    p = QueryPlan(dimensions=['month'], comparison='previous_period',
                  time_range={'start': '2018-07-01', 'end': '2018-09-01'})
    a = build_answer(p, [
        evidence('current', [
            {'month': '2018-07', 'gmv': D(20), 'population_count': 2},
            {'month': '2018-08', 'gmv': D(50), 'population_count': 2},
        ]),
        evidence('previous', [
            {'month': '2018-05', 'gmv': D(10), 'population_count': 1},
            {'month': '2018-06', 'gmv': D(30), 'population_count': 1},
        ]),
    ])
    assert [r['gmv_change'] for r in a.table] == [D(10), D(20)]
    assert [r['gmv_growth'] for r in a.table] == [None, None]
    assert [f.result_reference for f in a.facts if f.metric_id == 'gmv'] == [
        ['current:rows[0]', 'previous:rows[0]'], ['current:rows[1]', 'previous:rows[1]']]


def test_multimonth_previous_period_preserves_calendar_boundaries():
    previous = previous_range(TimeRange(start=date(2018, 7, 1), end=date(2018, 9, 1)))
    assert previous.start == date(2018, 5, 1)
    assert previous.end == date(2018, 7, 1)


@pytest.mark.parametrize('side', [0, 1])
def test_truncated_comparison_rejects_unreliable_group_changes(side):
    p = plan(dimensions=['category'], comparison='previous_period')
    results = [evidence('current', [{'category': 'A', 'gmv': D(20), 'population_count': 1}]),
               evidence('previous', [{'category': 'B', 'gmv': D(10), 'population_count': 1}])]
    results[side].truncated = True
    a = build_answer(p, results)
    assert a.status == 'rejected'
    assert not a.facts and not a.table
    assert len(a.evidence) == 2


@pytest.mark.parametrize('metric', ['gmv', 'aov', 'late_rate', 'average_review_score'])
def test_rank_sort_happens_before_result_cap(metric):
    query = compile_plan(plan(metric_ids=[metric], dimensions=['state'], sort='value_desc', top_k=1))[0]
    ast = sqlglot.parse_one(query.sql.replace('%s', '?').replace('%%', '%'), read='mysql')
    first_sort = ast.args['order'].expressions[0]
    assert first_sort.args['desc'] is True
    assert ast.args['limit'].expression == exp.Literal.number(201)


def test_full_multimonth_aggregate_suppresses_monthly_growth():
    p = QueryPlan(comparison='previous_period', time_range={'start': '2018-05-01', 'end': '2018-07-01'})
    a = build_answer(p, [
        evidence('current', [{'gmv': D(209), 'population_count': 2}]),
        evidence('previous', [{'gmv': D(110), 'population_count': 1}]),
    ])
    assert a.table[0]['gmv_change'] == D(99)
    assert a.table[0]['gmv_growth'] is None


@pytest.mark.integration
@pytest.mark.parametrize('metric', ['gmv', 'merchandise_value', 'payment_value', 'delivered_order_count',
                                   'aov', 'late_rate', 'average_review_score'])
def test_real_mysql_ranking_preserves_aggregate_semantics(metric):
    if os.getenv('EQA_INTEGRATION') != '1':
        pytest.skip('requires EQA_INTEGRATION=1 and isolated MySQL')
    from enterprise_query.executor import Executor
    p = plan(metric_ids=[metric], dimensions=['state'], sort='value_desc', top_k=1)
    q = compile_plan(p)[0]
    result = Executor().execute(q.sql, q.parameters)
    answer = build_answer(p, [result])
    assert answer.table[0]['state'] == 'SP'


@pytest.mark.integration
def test_order_dimension_is_legal_mysql_identifier():
    if os.getenv('EQA_INTEGRATION') != '1':
        pytest.skip('requires EQA_INTEGRATION=1 and isolated MySQL')
    from enterprise_query.executor import Executor
    p = plan(dimensions=['order'])
    q = compile_plan(p)[0]
    answer = build_answer(p, [Executor().execute(q.sql, q.parameters)])
    assert len(answer.table) == 3
    assert sum(row['gmv'] for row in answer.table) == D(242)


@pytest.mark.parametrize('previous, expected_growth', [(D(110), D('1.2')), (D(0), None)])
def test_comparison_derivatives_are_explicit_traceable_facts(previous, expected_growth):
    p = plan(comparison='previous_period')
    a = build_answer(p, [
        evidence('current', [{'gmv': D(242), 'population_count': 3}]),
        evidence('previous', [{'gmv': previous, 'population_count': 1}]),
    ])
    facts = {fact.metric_id: fact for fact in a.facts}
    assert facts['gmv_previous'].value == previous
    assert facts['gmv_change'].value == D(242) - previous
    assert facts['gmv_growth'].value == expected_growth
    assert facts['gmv_growth'].unit == 'ratio'
    assert 'current - previous' in facts['gmv_change'].calculation
    assert 'change / previous' in facts['gmv_growth'].calculation
    for fact in facts.values():
        assert fact.result_reference == ['current:rows[0]', 'previous:rows[0]']
