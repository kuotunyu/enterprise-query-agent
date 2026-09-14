import asyncio
import pytest
from enterprise_query.comparison import MockMethod, FixedDecision, DirectDecision, MODEL_PROFILE
from enterprise_query.service import Service
from tests.test_service import FakeExecutor,req

@pytest.mark.parametrize('method',['A','B','C'])
def test_method_runs_through_shared_service(method):
    async def run():
        answer=await Service(FakeExecutor,MockMethod(method)).ask(req('2018年7月GMV'))
        assert answer.status=='answered'
        assert answer.usage['model_calls']==1 and answer.usage['sql_selects']==1
    asyncio.run(run())

def test_baselines_do_not_call_semantic_compiler(monkeypatch):
    import enterprise_query.compiler as compiler
    monkeypatch.setattr(compiler,'compile_plan',lambda _:pytest.fail('baseline called C compiler'))
    async def run():
        for method in ['A','B']:
            answer=await Service(FakeExecutor,MockMethod(method)).ask(req('2018年7月GMV'))
            assert answer.status=='answered'
    asyncio.run(run())

def test_distinct_schema_and_explicit_model_profile():
    assert FixedDecision.model_json_schema()!=DirectDecision.model_json_schema()
    assert MODEL_PROFILE['model']=='gpt-5.6-luna'
    assert MODEL_PROFILE['reasoning']=={'effort':'low'}

import os

@pytest.mark.integration
@pytest.mark.skipif(os.getenv('EQA_INTEGRATION')!='1',reason='requires isolated runtime MySQL')
def test_fixed_baseline_preserves_delivered_order_without_items():
    from enterprise_query.executor import Executor
    fixture="""orders AS (SELECT 'one' AS order_id,'c' AS customer_id,'delivered' AS order_status,CAST('2018-07-10' AS DATETIME) AS order_purchase_timestamp UNION ALL SELECT 'two','c','delivered',CAST('2018-07-11' AS DATETIME)), customers AS (SELECT 'c' AS customer_id,'SP' AS customer_state), order_items AS (SELECT 'one' AS order_id,100 AS price,10 AS freight_value)"""
    for method in ['A','B','C']:
        provider=MockMethod(method)
        decision=provider.decide(req('2018年7月GMV、訂單數與AOV'),{})
        plan,queries=provider.prepare(decision)
        query=queries[0]
        sql=('WITH '+fixture+', '+query.sql[5:]) if query.sql.startswith('WITH ') else 'WITH '+fixture+' '+query.sql
        result=Executor().execute(sql,parameters=query.parameters)
        from enterprise_query.facts import build_answer
        facts={f.metric_id:f.value for f in build_answer(plan,[result]).facts}
        assert facts['delivered_order_count']==2
        assert facts['aov']==55

@pytest.mark.parametrize('method',['A','B','C'])
def test_methods_share_clarification_and_model_call_budgets(method):
    async def run():
        service=Service(FakeExecutor,MockMethod(method))
        request=req('營收')
        assert (await service.ask(request)).status=='needs_clarification'
        request=request.model_copy(update={'request_id':'2','revision':2,'clarification':'含運 GMV'})
        assert (await service.ask(request)).status=='needs_clarification'
        request=request.model_copy(update={'request_id':'3','revision':3,'clarification':'2018年7月'})
        answer=await service.ask(request)
        assert answer.status=='answered' and answer.usage['clarification_rounds']==2
        request=request.model_copy(update={'request_id':'4','revision':4})
        assert (await service.ask(request)).status=='rejected'
    asyncio.run(run())

def test_a_unsupported_remains_unsupported():
    assert MockMethod('A').decide(req('2018年7月GMV前5名品類'),{}).action=='unsupported'

@pytest.mark.integration
@pytest.mark.skipif(os.getenv('EQA_INTEGRATION')!='1',reason='requires isolated runtime MySQL')
def test_direct_b_rejected_write_and_wrong_number_are_not_rewritten():
    from enterprise_query.executor import Executor
    from enterprise_query.comparison import DirectStatement
    class Direct(MockMethod):
        def __init__(self,sql): super().__init__('B');self.sql=sql
        def decide(self,request,memory):
            decision=super().decide(request,memory)
            decision.statements=[DirectStatement(sql=self.sql,parameters=[],label='current')]
            return decision
    async def run():
        answer=await Service(Executor,Direct('DROP TABLE orders')).ask(req('2018年7月GMV'))
        assert answer.status=='rejected'
        assert answer.usage['attempts'][0]['decision']['statements'][0]['sql']=='DROP TABLE orders'
        answer=await Service(Executor,Direct('SELECT 1 AS population_count,999 AS gmv')).ask(req('2018年7月GMV'))
        assert answer.status=='answered' and answer.facts[0].value==999
        assert answer.evidence[0].sql=='SELECT 1 AS population_count,999 AS gmv'
    asyncio.run(run())

@pytest.mark.integration
@pytest.mark.skipif(os.getenv('EQA_INTEGRATION')!='1',reason='requires isolated runtime MySQL')
@pytest.mark.parametrize('method',['A','B','C'])
def test_methods_synthetic_hand_calculated_answers(method):
    from decimal import Decimal
    from enterprise_query.executor import Executor
    # Public development oracle, never included in a method prompt or runner.
    cases=[('2018年7月GMV','gmv',Decimal('242')),('2018年7月不含運商品金額','merchandise_value',Decimal('220')),('2018年7月全部狀態付款總額','payment_value',Decimal('459')),('2018年7月回購率','repeat_customer_rate',Decimal('.5')),('2018年7月最新評論平均','average_review_score',Decimal('4')),('2018年7月延遲率','late_rate',Decimal('.5')),('2018年7月SP州GMV','gmv',Decimal('209')),('2018年7月GMV比上月','gmv_change',Decimal('132'))]
    async def run():
        assert Executor().check_identity()['dataset_id']=='synthetic-v1'
        for question,metric,expected in cases:
            answer=await Service(Executor,MockMethod(method)).ask(req(question))
            assert answer.status=='answered'
            assert next(f.value for f in answer.facts if f.metric_id==metric)==expected
        answer=await Service(Executor,MockMethod(method)).ask(req('2018年7月AOV按品類'))
        assert {row['category']:row['aov'] for row in answer.table}=={'A':Decimal(77),'B':Decimal(44)}
    asyncio.run(run())
