from decimal import Decimal
from enterprise_query.contracts import QueryPlan, QueryResult


def test_decimal_zero_null_and_provenance():
    from enterprise_query.facts import build_answer
    p = QueryPlan(metric_ids=['aov'],time_range={'start':'2018-07-01','end':'2018-08-01'})
    r = QueryResult(query_id='q1',sql='SELECT ...',parameters=[],columns=['gmv'],column_types=['246'],rows=[{'gmv':Decimal('242'),'delivered_order_count':3,'population_count':3}],row_count=1,truncated=False,elapsed_ms=1,dataset_id='synthetic-v1',result_hash='a'*64)
    a = build_answer(p,[r])
    assert a.facts[0].value == Decimal('242')/3
    assert a.facts[0].display == '80.67 BRL'
    assert 'q1' in a.facts[0].result_reference[0]
    r.rows=[{'gmv':None,'delivered_order_count':0,'population_count':0}]
    assert build_answer(p,[r]).status == 'empty'
