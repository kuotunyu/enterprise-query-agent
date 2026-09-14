"""Destructive development mutations, only on explicitly opted-in port 3308 repro DB."""
import os
from decimal import Decimal as D
import pytest
from scripts.bootstrap_data import bootstrap,identifier,verify_destination
from tests.test_engine import answer

pytestmark=pytest.mark.integration

@pytest.fixture
def mutation_db():
    if os.getenv('EQA_MUTATION')!='1': pytest.skip('requires EQA_MUTATION=1 and dedicated repro DB on port 3308')
    if os.getenv('EQA_DB_HOST','127.0.0.1') not in ('127.0.0.1','localhost') or os.getenv('EQA_DB_PORT')!='3308':
        pytest.fail('Mutation tests refuse any destination except loopback:3308')
    import pymysql
    from enterprise_query.executor import Executor
    root=pymysql.connect(host='127.0.0.1',port=3308,user='root',password=os.environ['EQA_BOOTSTRAP_PASSWORD'],database='eqa_v1',autocommit=False)
    with root.cursor() as cur: verify_destination(cur)
    runtime=Executor(port=3308)
    assert runtime.check_identity()['dataset_id']=='synthetic-v1'
    try:
        yield root,runtime
    finally:
        root.rollback()
        root.close()
        bootstrap()  # Full baseline restore even when an assertion fails.

def mutate(root,sql,params=()):
    with root.cursor() as cur: cur.execute(sql,params)
    root.commit()  # Runtime uses a separate SELECT-only connection.


def test_latest_review_creation_answer_and_id_tie_breaks(mutation_db):
    root,runtime=mutation_db
    # Same creation date: answer timestamp wins despite larger older ID.
    mutate(root,"INSERT INTO order_reviews(review_id,order_id,review_score,review_creation_date,review_answer_timestamp) VALUES (%s,%s,2,'2018-07-18 12:00:00','2018-07-18 14:00:00')",(identifier(510),identifier(1)))
    assert answer(runtime,['average_review_score']).facts[0].value==D('2.5')
    # Exact time tie: lexically greatest ID wins.
    mutate(root,"INSERT INTO order_reviews(review_id,order_id,review_score,review_creation_date,review_answer_timestamp) VALUES (%s,%s,4,'2018-07-18 12:00:00','2018-07-18 14:00:00')",(identifier(511),identifier(1)))
    assert answer(runtime,['average_review_score']).facts[0].value==D('3.5')
    # Creation timestamp dominates even a much later answer timestamp.
    mutate(root,"INSERT INTO order_reviews(review_id,order_id,review_score,review_creation_date,review_answer_timestamp) VALUES (%s,%s,1,'2018-07-17 12:00:00','2018-09-18 14:00:00')",(identifier(512),identifier(1)))
    assert answer(runtime,['average_review_score']).facts[0].value==D('3.5')


def test_D18_missing_translation_and_null_category_are_retained(mutation_db):
    root,runtime=mutation_db
    mutate(root,"DELETE FROM product_category_name_translation WHERE product_category_name='A'")
    mutate(root,'UPDATE products SET product_category_name=NULL WHERE product_id=%s',(identifier(402),))
    result=answer(runtime,['gmv'],['category'])
    assert {r['category']:r['gmv'] for r in result.table}=={'A':D('154'),'unknown':D('88')}
    assert sum(r['gmv'] for r in result.table)==D('242')


def test_half_open_purchase_timestamp_boundary(mutation_db):
    root,runtime=mutation_db
    mutate(root,"UPDATE orders SET order_purchase_timestamp='2018-07-01 00:00:00' WHERE order_id=%s",(identifier(1),))
    mutate(root,"UPDATE orders SET order_purchase_timestamp='2018-08-01 00:00:00' WHERE order_id=%s",(identifier(2),))
    july=answer(runtime,['gmv','delivered_order_count'])
    assert [f.value for f in july.facts]==[D('198'),2]
    august=answer(runtime,['gmv'],start='2018-08-01',end='2018-09-01')
    assert august.facts[0].value==D('44')


def test_all_missing_review_and_delivery_have_undefined_denominators(mutation_db):
    root,runtime=mutation_db
    mutate(root,'DELETE FROM order_reviews')
    mutate(root,'UPDATE orders SET order_delivered_customer_date=NULL')
    for metric in ['average_review_score','late_rate']:
        result=answer(runtime,[metric])
        assert result.status=='answered'
        assert result.table[0]['eligible']==0 and result.table[0]['missing']==3
        assert result.facts[0].value is None
        assert 'undefined' in result.facts[0].display


def test_D19_zero_previous_gmv_preserves_absolute_change(mutation_db):
    root,runtime=mutation_db
    mutate(root,'UPDATE order_items SET price=0,freight_value=0 WHERE order_id=%s',(identifier(5),))
    result=answer(runtime,['gmv'],comparison='previous_period')
    assert result.table[0]['gmv_previous']==D('0')
    assert result.table[0]['gmv_change']==D('242')
    assert result.table[0]['gmv_growth'] is None
    assert 'Infinity' not in result.model_dump_json()


def test_D20_partial_month_has_no_normal_month_growth(mutation_db):
    _,runtime=mutation_db
    result=answer(runtime,['gmv'],comparison='previous_period',start='2018-07-10',end='2018-08-01')
    assert result.coverage=='partial/unknown'
    assert result.table[0]['gmv_growth'] is None
    assert any('完整單月' in limitation for limitation in result.limitations)

def test_ranking_happens_before_200_row_transfer_and_comparison_rejects_truncation(mutation_db):
    root,runtime=mutation_db
    from enterprise_query.compiler import compile_plan
    from enterprise_query.contracts import QueryPlan
    from enterprise_query.facts import build_answer
    with root.cursor() as cur:
        for index in range(201):
            oid=identifier(1000+index)
            cur.execute("INSERT INTO orders SELECT %s,customer_id,order_status,order_purchase_timestamp,order_approved_at,order_delivered_carrier_date,order_delivered_customer_date,order_estimated_delivery_date FROM orders WHERE order_id=%s",(oid,identifier(1)))
            cur.execute("INSERT INTO order_items SELECT %s,1,product_id,seller_id,shipping_limit_date,%s,0 FROM order_items WHERE order_id=%s AND order_item_id=1",(oid,'999' if index==200 else '1',identifier(1)))
    root.commit()
    plan=QueryPlan(metric_ids=['gmv'],dimensions=['order'],time_range={'start':'2018-07-01','end':'2018-08-01'},sort='value_desc',top_k=1)
    result=build_answer(plan,[runtime.execute(q.sql,q.parameters) for q in compile_plan(plan)])
    assert result.table[0]['order']==identifier(1200)
    assert result.facts[0].value==D('999')
    assert result.evidence[0].truncated is True
    comparison=plan.model_copy(update={'comparison':'previous_period'})
    result=build_answer(comparison,[runtime.execute(q.sql,q.parameters) for q in compile_plan(comparison)])
    assert result.status=='rejected' and result.facts==[] and result.table==[]
