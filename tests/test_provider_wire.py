"""Official SDK wire parsing through local MockTransport, with no network calls."""
import json
import httpx
import pytest
from openai import OpenAI
from enterprise_query.providers import OpenAIPlanner
from tests.test_service import req

@pytest.mark.parametrize('kind',['valid','invalid','unknown','refusal'])
def test_structured_output_wire(kind):
    decision={'action':'clarify','message':'請指定月份','options':['2018年7月'],'plan':None}
    if kind=='unknown': decision={'action':'query','message':'plan','options':[],'plan':{'metric_ids':['unknown_metric'],'time_range':{'start':'2018-07-01','end':'2018-08-01'}}}
    content={'type':'output_text','text':'{' if kind=='invalid' else json.dumps(decision),'annotations':[]}
    if kind=='refusal': content={'type':'refusal','refusal':'Cannot answer'}
    seen=[]
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'resp_mock','object':'response','created_at':0,'model':'explicit-test-model','status':'completed','output':[{'id':'msg_mock','type':'message','role':'assistant','status':'completed','content':[content]}],'usage':{'input_tokens':12,'output_tokens':7,'total_tokens':19,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}},'parallel_tool_calls':False,'tool_choice':'auto','tools':[]})
    planner=OpenAIPlanner('explicit-test-model','offline-test-key')
    planner.client=OpenAI(api_key='offline-test-key',http_client=httpx.Client(transport=httpx.MockTransport(transport)),max_retries=0)
    if kind=='valid':
        assert planner.decide(req('GMV'),{}).action=='clarify'
        assert planner.last_usage['tokens']['total_tokens']==19
        assert planner.last_usage['usd'] is None
    else:
        with pytest.raises(ValueError): planner.decide(req('GMV'),{})
        assert planner.last_usage['tokens']['total_tokens']==19
    assert len(seen)==1 and seen[0]['model']=='explicit-test-model'
    assert seen[0]['text']['format']['type']=='json_schema'
    assert 'review_comment_message' not in json.dumps(seen)

def test_clarification_reply_is_retained_across_structured_clarify_turns():
    seen=[]
    def transport(request):
        seen.append(json.loads(request.content))
        decision={'action':'clarify','message':'請指定月份','options':['2018年7月'],'plan':None}
        return httpx.Response(200,json={'id':'resp_mock','object':'response','created_at':0,'model':'explicit-test-model','status':'completed','output':[{'id':'msg_mock','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(decision),'annotations':[]}]}],'usage':{'input_tokens':12,'output_tokens':7,'total_tokens':19,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}},'parallel_tool_calls':False,'tool_choice':'auto','tools':[]})
    planner=OpenAIPlanner('explicit-test-model','offline-test-key')
    planner.client=OpenAI(api_key='offline-test-key',http_client=httpx.Client(transport=httpx.MockTransport(transport)),max_retries=0)
    memory={}
    first=req('營收','含運 GMV')
    assert planner.decide(first,memory).action=='clarify'
    second=req('營收','2018年7月')
    planner.decide(second,memory)
    sent=json.loads(seen[1]['input'][1]['content'])
    assert '含運 GMV' in json.dumps(sent['confirmed_context'],ensure_ascii=False)
    assert all(set(turn)=={'question','clarification'} for turn in memory['user_turns'])
    assert memory['user_turns']==[{'question':'營收','clarification':'含運 GMV'},{'question':'營收','clarification':'2018年7月'}]
