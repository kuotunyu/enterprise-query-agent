from datetime import date
import pytest
from enterprise_query.contracts import QuestionRequest
from enterprise_query.planner import MockPlanner, DEVELOPMENT_QUESTIONS

def req(q, clarification=None):
    return QuestionRequest(question=q, request_id='r', session_id='s', as_of_date=date(2026,9,14), clarification=clarification)

def test_development_questions():
    p = MockPlanner()
    assert len(DEVELOPMENT_QUESTIONS) >= 24
    for q in DEVELOPMENT_QUESTIONS:
        assert p.decide(req(q), {}).action in {'query','clarify','unsupported'}

def test_clarification_and_relative_time():
    p = MockPlanner()
    assert p.decide(req('2018年7月營收多少'), {}).action == 'clarify'
    d=p.decide(req('2018年7月營收多少','含運 GMV'), {})
    assert d.plan.metric_ids == ['gmv']
    assert p.decide(req('上個月GMV'),{}).plan.time_range.start == date(2026,8,1)
    assert p.decide(req('GMV'),{}).action == 'clarify'
    assert p.decide(req('2018年7月毛利'),{}).action == 'unsupported'

import asyncio
import time
from enterprise_query.service import Service
from enterprise_query.contracts import PlannerDecision, QueryResult

class FakeExecutor:
    executions=0
    def check_identity(self): return {'dataset_id':'synthetic-v1','schema_version':'schema-v1'}
    def cancel(self): pass
    def execute(self,sql,parameters=(),timeout_ms=5000):
        type(self).executions+=1
        return QueryResult(query_id='q',sql=sql,parameters=list(parameters),columns=['gmv','delivered_order_count','population_count'],column_types=['DECIMAL','LONG','LONG'],rows=[{'gmv':242,'delivered_order_count':3,'population_count':3}],row_count=1,truncated=False,elapsed_ms=1,dataset_id='synthetic-v1',result_hash='hash')

def test_concurrent_deduplication_and_id_conflict():
    async def run():
        FakeExecutor.executions=0
        s=Service(FakeExecutor)
        r=req('2018年7月GMV')
        a,b=await asyncio.gather(s.ask(r),s.ask(r))
        # Each waiter is stamped on return; wall time is not a cached result.
        exclude={'usage': {'wall_seconds'}}
        assert a.model_dump(exclude=exclude)==b.model_dump(exclude=exclude)
        assert a.status=='answered'
        assert a.usage['wall_seconds']>=0 and b.usage['wall_seconds']>=0
        assert FakeExecutor.executions==1 and a.usage['model_calls']==1
        r.question='2018年6月GMV'
        assert (await s.ask(r)).status=='rejected'
    asyncio.run(run())

def test_cumulative_budget_and_session_memory():
    async def run():
        s=Service(FakeExecutor)
        r=req('營收')
        assert (await s.ask(r)).status=='needs_clarification'
        r.request_id='r2';r.revision=2;r.clarification='含運 GMV'
        assert (await s.ask(r)).status=='needs_clarification'
        # Waiting is wall time only, not active processing.
        await asyncio.sleep(.05)
        r.request_id='r3';r.revision=3;r.clarification='2018年7月'
        a=await s.ask(r)
        assert a.status=='answered' and a.usage['model_calls']==3
        assert a.usage['wall_seconds']-a.usage['active_seconds']>=.04
        r.request_id='r4';r.revision=4
        assert (await s.ask(r)).status=='rejected'
        fresh=req('2018年7月營收');fresh.session_id='new';fresh.request_id='new'
        assert (await s.ask(fresh)).status=='needs_clarification'
    asyncio.run(run())

def test_provider_failure_is_bounded():
    class Broken:
        mode='mock'
        def decide(self,*args): raise ValueError('secret-provider-trace')
    async def run():
        s=Service(FakeExecutor,Broken())
        for n in range(4):
            r=req('2018年7月GMV');r.request_id=str(n);r.revision=n+1
            a=await s.ask(r)
            assert a.status==('provider_error' if n<3 else 'rejected')
            assert 'secret' not in a.model_dump_json()
    asyncio.run(run())

def test_cancel_stops_executor_and_stale_revision():
    class Blocking(FakeExecutor):
        instance=None
        def __init__(self): self.stopped=False; Blocking.instance=self
        def cancel(self): self.stopped=True
        def execute(self,*args,**kwargs):
            while not self.stopped: time.sleep(.005)
            raise TimeoutError('cancelled')
    async def run():
        s=Service(Blocking)
        work=asyncio.create_task(s.ask(req('2018年7月GMV')))
        while Blocking.instance is None: await asyncio.sleep(.005)
        s.cancel('s')
        assert (await work).status=='rejected'
        assert Blocking.instance.stopped
        t=s.tasks['s'];t.revision=3
        r=req('2018年7月GMV');r.request_id='old'
        assert (await s.ask(r)).status=='rejected'
    asyncio.run(run())

def test_provider_config_defaults_to_no_paid_calls(monkeypatch):
    from enterprise_query.providers import configured_provider
    monkeypatch.delenv('EQA_ENABLE_PAID_API',raising=False)
    monkeypatch.setenv('OPENAI_API_KEY','accidental-key')
    assert configured_provider().mode=='mock'
    monkeypatch.setenv('EQA_ENABLE_PAID_API','1')
    monkeypatch.delenv('EQA_MODEL',raising=False)
    with pytest.raises(RuntimeError): configured_provider()

def test_sql_budget_revision_and_timeout_memory():
    async def run():
        s=Service(FakeExecutor)
        a=await s.ask(req('2018年7月GMV比上月'))
        assert a.usage['sql_selects']==2
        r=req('2018年7月GMV比上月');r.request_id='other'
        assert (await s.ask(r)).status=='rejected'  # same revision, different request
        r.revision=2;r.request_id='revision2'
        assert (await s.ask(r)).status=='rejected'  # two more SELECTs exceed 3
        assert s.tasks['s'].sql==2
        class Slow(MockPlanner):
            def decide(self,r,memory):
                time.sleep(.06);memory['late']='must not persist'
                return PlannerDecision(action='unsupported',message='late')
        slow=Service(FakeExecutor,Slow())
        from enterprise_query.service import Task
        slow.tasks['s']=Task(active=59.99)
        assert (await slow.ask(req('GMV'))).status=='timeout'
        await asyncio.sleep(.08)
        assert 'late' not in slow.tasks['s'].memory
    asyncio.run(run())

@pytest.mark.parametrize('reason',['timeout','cancel','stale'])
def test_queued_provider_work_does_not_start_after_invalidation(reason):
    import threading
    from enterprise_query.service import Task
    class Blocking(MockPlanner):
        def __init__(self): self.entered=threading.Event();self.release=threading.Event();self.seen=[]
        def decide(self,r,memory):
            self.seen.append((r.session_id,r.revision))
            if r.session_id=='first':
                self.entered.set();self.release.wait(2)
            self.last_usage={'tokens':{'total_tokens':19}}
            return PlannerDecision(action='unsupported',message='mock')
    async def run():
        provider=Blocking();s=Service(FakeExecutor,provider)
        first=req('GMV');first.session_id='first';first.request_id='first'
        first_work=asyncio.create_task(s.ask(first))
        while not provider.entered.is_set(): await asyncio.sleep(.001)
        second=req('GMV');second.session_id='second';second.request_id='second'
        if reason=='timeout': s.tasks['second']=Task(active=59.99)
        second_work=asyncio.create_task(s.ask(second))
        await asyncio.sleep(.02)
        if reason=='cancel': s.cancel('second')
        if reason=='stale': s.tasks['second'].revision=2
        provider.release.set()
        await asyncio.gather(first_work,second_work)
        await asyncio.sleep(.03)
        assert provider.seen==[('first',1)]
        assert s.tasks['second'].calls==0
    asyncio.run(run())

def test_inflight_timeout_preserves_late_usage_and_provider_isolation():
    import threading
    from enterprise_query.service import Task
    class Blocking(MockPlanner):
        def __init__(self): self.entered=threading.Event();self.release=threading.Event();self.seen=[]
        def decide(self,r,memory):
            self.seen.append(r.session_id)
            if r.session_id=='s': self.entered.set();self.release.wait(2)
            self.last_usage={'tokens':{'total_tokens':19 if r.session_id=='s' else 7}}
            return PlannerDecision(action='unsupported',message='mock')
    async def run():
        provider=Blocking();s=Service(FakeExecutor,provider);s.tasks['s']=Task(active=59.98)
        original=req('GMV')
        assert (await s.ask(original)).status=='timeout'
        following=req('GMV');following.session_id='following';following.request_id='following'
        next_work=asyncio.create_task(s.ask(following))
        await asyncio.sleep(.01)
        assert provider.seen==['s']  # lock must remain held by in-flight work
        provider.release.set()
        next_answer=await next_work
        replay=await s.ask(original)
        assert replay.status=='timeout'
        assert replay.usage['attempts'][0]['provider_usage']['tokens']['total_tokens']==19
        assert next_answer.usage['attempts'][0]['provider_usage']['tokens']['total_tokens']==7
        assert provider.seen==['s','following']
    asyncio.run(run())

def test_mock_does_not_drop_explicit_day_ranges_or_states():
    planner=MockPlanner()
    for question in ['2018年7月10日至20日GMV','2018年6月至7月GMV','2018年6月到2018年7月GMV']:
        assert planner.decide(req(question),{}).action in {'unsupported','clarify'}
    decision=planner.decide(req('2018年7月MG州GMV'),{})
    assert decision.plan.filters[0].value=='MG'
