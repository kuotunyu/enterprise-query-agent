import asyncio,json
import httpx,pytest
from openai import OpenAI
from enterprise_query.comparison import OpenAIMethod
from enterprise_query.comparison_runner import run_development
from enterprise_query.costs import CostLedger
from tests.test_service import FakeExecutor


def test_paid_runner_retains_wire_usage_and_stops_on_adapter_failure(tmp_path):
    path=tmp_path/'cost.json';CostLedger.initialize(path,{'development':'25','research':'20'})
    ledger=CostLedger(path,'development')
    def transport(request):
        return httpx.Response(500,json={'error':{'message':'private failure'}})
    client=OpenAI(api_key='offline',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    directory=tmp_path/'run'
    summary=asyncio.run(run_development(directory,FakeExecutor,questions=['2018年7月GMV'],provider_factory=lambda m:OpenAIMethod(m,client,ledger),paid_ledger=ledger))
    assert summary['event']=='run_stopped'
    assert summary['terminal_count']==1 and summary['unstarted_count']==2
    assert summary['api_attempts']==1 and summary['api_cost_usd'] is None
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in events]==['started','turn_started','terminal','run_stopped']
    terminal=events[2]
    assert terminal['api_attempts']==1 and terminal['charged_upper_usd']=='0.5323728'
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['mode']=='paid_development' and manifest['actual_api_calls'] is None
    assert manifest['budget']['stage']=='development'
    assert b'private failure' not in (directory/'events.jsonl').read_bytes()


def test_paid_development_cannot_use_research_budget(tmp_path):
    path=tmp_path/'cost.json';CostLedger.initialize(path,{'development':'25','research':'20'})
    ledger=CostLedger(path,'research')
    with pytest.raises(ValueError,match='development'):
        asyncio.run(run_development(tmp_path/'run',FakeExecutor,questions=[],paid_ledger=ledger))
    assert not (tmp_path/'run').exists()


def test_paid_runner_completes_all_methods_and_records_usage(tmp_path):
    path=tmp_path/'cost.json';CostLedger.initialize(path,{'development':'25','research':'20'})
    ledger=CostLedger(path,'development');seen=[]
    def transport(request):
        sent=json.loads(request.content);seen.append(sent['text']['format']['name'])
        data={'action':'unsupported','message':'outside scope','options':[]}
        data.update({'FixedDecision':{'slots':None},'DirectDecision':{'metadata':None,'statements':[]},'PlannerDecision':{'plan':None}}[seen[-1]])
        return httpx.Response(200,json={'id':'resp_offline','object':'response','created_at':0,'model':'gpt-5.6-luna','status':'completed','output':[{'id':'msg_offline','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(data),'annotations':[]}]}],'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}},'parallel_tool_calls':False,'tool_choice':'auto','tools':[]})
    client=OpenAI(api_key='offline',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    directory=tmp_path/'run'
    result=asyncio.run(run_development(directory,FakeExecutor,questions=['2018年7月毛利'],provider_factory=lambda m:OpenAIMethod(m,client,ledger),paid_ledger=ledger))
    assert result['event']=='run_completed' and result['terminal_count']==3
    assert result['api_attempts']==3 and result['charged_upper_usd']=='0.00111'
    assert seen==['FixedDecision','DirectDecision','PlannerDecision']
    assert len(list((directory/'provider-traces').glob('*.json')))==3
    assert result['status_counts']=={'A:unsupported':1,'B:unsupported':1,'C:unsupported':1}


def test_model_contract_failure_is_retained_without_stopping_other_tasks(tmp_path):
    path=tmp_path/'cost.json';CostLedger.initialize(path,{'development':'25','research':'20'})
    ledger=CostLedger(path,'development')
    def transport(request):
        return httpx.Response(200,json={'id':'resp_offline','object':'response','created_at':0,'model':'gpt-5.6-luna','status':'completed','output':[{'id':'msg_offline','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{','annotations':[]}]}],'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100},'parallel_tool_calls':False,'tool_choice':'auto','tools':[]})
    client=OpenAI(api_key='offline',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    result=asyncio.run(run_development(tmp_path/'run',FakeExecutor,questions=['2018年7月GMV'],provider_factory=lambda m:OpenAIMethod(m,client,ledger),paid_ledger=ledger))
    assert result['event']=='run_completed' and result['terminal_count']==3
    assert result['status_counts']=={'A:provider_error':1,'B:provider_error':1,'C:provider_error':1}
    assert result['api_attempts']==3
