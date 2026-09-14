from decimal import Decimal
import json
import pytest
from enterprise_query import providers


def test_paid_start_requires_existing_approved_ledger(monkeypatch):
    monkeypatch.setenv('EQA_ENABLE_PAID_API','1')
    monkeypatch.setenv('EQA_PROVIDER','openai')
    monkeypatch.setenv('EQA_MODEL','gpt-5.6-luna')
    monkeypatch.setenv('OPENAI_API_KEY','offline-key')
    monkeypatch.delenv('EQA_COST_STAGE',raising=False)
    with pytest.raises(RuntimeError,match='budget|ledger|stage'):
        providers.configured_provider()


def test_reservations_survive_restart_and_stages_do_not_borrow(tmp_path):
    from enterprise_query.costs import CostLedger, BudgetError
    path=tmp_path/'ledger.json'
    CostLedger.initialize(path,{'development':'0.60','research':'1'})
    ledger=CostLedger(path,'development')
    ticket=ledger.reserve('request-one')
    assert ledger.total()==Decimal('0.5323728')
    with pytest.raises(BudgetError): CostLedger(path,'development').reserve('request-two')
    assert CostLedger(path,'research').total()==0
    assert ticket in json.loads(path.read_text())['data']['calls']


def test_usage_reconciles_conservatively_and_cannot_settle_twice(tmp_path):
    from enterprise_query.costs import CostLedger, BudgetError
    path=tmp_path/'ledger.json'
    CostLedger.initialize(path,{'development':'1','research':'1'})
    ledger=CostLedger(path,'development')
    ticket=ledger.reserve('one')
    assert ledger.settle(ticket,{'input_tokens':1000,'output_tokens':100})==Decimal('0.00037')
    assert CostLedger(path,'development').total()==Decimal('0.00037')
    with pytest.raises(BudgetError): ledger.settle(ticket,{'input_tokens':0,'output_tokens':0})
    with pytest.raises(BudgetError): CostLedger.initialize(path,{'development':'25','research':'20'})


@pytest.mark.parametrize('usage',[None,{}, {'input_tokens':-1,'output_tokens':2}, {'input_tokens':12,'output_tokens':True}])
def test_unknown_or_invalid_usage_keeps_reservation(tmp_path,usage):
    from enterprise_query.costs import CostLedger
    path=tmp_path/'ledger.json'
    CostLedger.initialize(path,{'development':'1','research':'1'})
    ledger=CostLedger(path,'development')
    ticket=ledger.reserve('one')
    assert ledger.settle(ticket,usage) is None
    assert ledger.total()==Decimal('0.5323728')


def test_corrupt_or_locked_ledger_fails_closed(tmp_path):
    from enterprise_query.costs import CostLedger, BudgetError
    path=tmp_path/'ledger.json'
    CostLedger.initialize(path,{'development':'1','research':'1'})
    path.with_suffix('.lock').write_text('crashed writer')
    with pytest.raises(BudgetError): CostLedger(path,'development').reserve('one')
    path.with_suffix('.lock').unlink()
    path.write_text('{')
    with pytest.raises(BudgetError): CostLedger(path,'development').reserve('two')


def test_budget_blocks_transport_and_unknown_failure_survives_restart(tmp_path):
    import httpx
    from openai import OpenAI, APIStatusError
    from enterprise_query.costs import CostLedger, BudgetError
    from enterprise_query.comparison import OpenAIMethod
    from tests.test_service import req
    path=tmp_path/'cost.json'
    CostLedger.initialize(path,{'development':'0.60','research':'1'})
    seen=[]
    def transport(request):
        seen.append(request)
        assert CostLedger(path,'development').total()==Decimal('0.5323728')
        return httpx.Response(500,json={'error':{'message':'offline failure'}})
    client=OpenAI(api_key='offline',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    provider=OpenAIMethod('C',client,CostLedger(path,'development'))
    with pytest.raises(APIStatusError): provider.decide(req('GMV'),{})
    restarted=OpenAIMethod('A',client,CostLedger(path,'development'))
    with pytest.raises(BudgetError): restarted.decide(req('GMV'),{})
    assert len(seen)==1
    assert provider.last_usage['usd'] is None


def test_concurrent_reservations_cannot_overdraw(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from enterprise_query.costs import CostLedger, BudgetError
    path=tmp_path/'cost.json'
    CostLedger.initialize(path,{'development':'0.60','research':'1'})
    def reserve(i):
        try: return CostLedger(path,'development').reserve(str(i))
        except BudgetError: return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(reserve,range(12)))
    assert sum(item is not None for item in results)==1
    assert CostLedger(path,'development').total()==Decimal('0.5323728')


def test_oversize_input_never_reaches_transport(tmp_path):
    import httpx
    from openai import OpenAI
    from enterprise_query.costs import CostLedger, BudgetError
    from enterprise_query.comparison import OpenAIMethod
    from tests.test_service import req
    path=tmp_path/'cost.json'
    CostLedger.initialize(path,{'development':'1','research':'1'})
    def transport(request): pytest.fail('oversized input was sent')
    client=OpenAI(api_key='offline',http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    provider=OpenAIMethod('A',client,CostLedger(path,'development'))
    with pytest.raises(BudgetError): provider.decide(req('GMV'),{'unbounded':'x'*64000})
    assert provider.ledger.total()==0
