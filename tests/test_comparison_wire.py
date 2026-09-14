from enterprise_query.costs import CostLedger
import asyncio
import json
import httpx
from openai import OpenAI
import pytest
from enterprise_query.comparison import MockMethod,OpenAIMethod,MODEL_PROFILE,COMMON_CONTEXT
from enterprise_query.service import Service
from tests.test_service import req,FakeExecutor

@pytest.mark.parametrize('method',['A','B','C'])
@pytest.mark.parametrize('kind',['valid','invalid_json','unknown_metric','http_error'])
def test_three_sdk_schemas_parameters_errors_and_usage(method,kind,tmp_path):
    seen=[]
    data=MockMethod(method).decide(req('2018年7月GMV'),{}).model_dump(mode='json')
    if kind=='unknown_metric':
        if method=='A': data['slots']['metrics']=['undefined_metric']
        elif method=='B': data['metadata']['metric_ids']=['undefined_metric']
        else: data['plan']['metric_ids']=['undefined_metric']
    def transport(request):
        seen.append(json.loads(request.content))
        if kind=='http_error': return httpx.Response(500,json={'error':{'message':'private provider trace','type':'server_error'}})
        return httpx.Response(200,json={'id':'resp_mock','object':'response','created_at':0,'model':MODEL_PROFILE['model'],'status':'completed','output':[{'id':'msg_mock','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{' if kind=='invalid_json' else json.dumps(data),'annotations':[]}]}],'usage':{'input_tokens':12,'output_tokens':7,'total_tokens':19,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}},'parallel_tool_calls':False,'tool_choice':'auto','tools':[]})
    client=OpenAI(api_key='offline-test-key',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    CostLedger.initialize(tmp_path/'cost.json',{'development':'1','research':'1'})
    provider=OpenAIMethod(method,client,CostLedger(tmp_path/'cost.json','development'))
    answer=asyncio.run(Service(FakeExecutor,provider).ask(req('2018年7月GMV')))
    assert answer.status==('answered' if kind=='valid' else 'provider_error')
    assert len(seen)==1
    assert seen[0]['model']==MODEL_PROFILE['model']
    assert seen[0]['reasoning']=={'effort':'low'} and seen[0]['max_output_tokens']==4096
    assert seen[0]['service_tier']=='default'
    assert seen[0]['text']['format']['name']==provider.decision_schema.__name__
    assert json.dumps(COMMON_CONTEXT,ensure_ascii=False,sort_keys=True) in seen[0]['input'][0]['content']
    if kind!='http_error': assert answer.usage['attempts'][0]['provider_usage']['tokens']['total_tokens']==19
    assert 'private provider trace' not in answer.model_dump_json()
