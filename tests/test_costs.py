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


def test_lock_access_denial_blocks_transport_without_mutation(tmp_path, monkeypatch):
    import httpx
    from pathlib import Path
    from openai import OpenAI
    from enterprise_query import costs
    from enterprise_query.comparison import OpenAIMethod
    from tests.test_service import req
    path = tmp_path/'cost.json'
    costs.CostLedger.initialize(path, {'development': '1', 'research': '1'})
    ledger = costs.CostLedger(path, 'development')
    before = path.read_bytes()
    lock = path.with_suffix('.lock')
    lock.write_bytes(b'another owner')
    denied = PermissionError(13, 'Access denied', str(lock))
    original_open = costs.os.open

    def deny_lock(target, flags, *args, **kwargs):
        if Path(target) == lock:
            raise denied
        return original_open(target, flags, *args, **kwargs)

    def transport(request):
        pytest.fail('request sent without acquiring the budget lock')

    client = OpenAI(api_key='offline', http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    provider = OpenAIMethod('C', client, ledger)
    monkeypatch.setattr(costs.os, 'open', deny_lock)
    with pytest.raises(costs.BudgetError, match='lock unavailable') as failure:
        provider.decide(req('GMV'), {})
    assert failure.value.__cause__ is denied
    assert path.read_bytes() == before
    assert lock.read_bytes() == b'another owner'
    assert not path.with_suffix('.pending').exists()
    assert 'ledger_ticket' not in provider.last_usage


def test_settlement_lock_access_denial_preserves_sent_reservation(tmp_path, monkeypatch):
    from pathlib import Path
    from enterprise_query import costs
    path = tmp_path/'cost.json'
    costs.CostLedger.initialize(path, {'development': '1', 'research': '1'})
    ledger = costs.CostLedger(path, 'development')
    ticket = ledger.reserve('already-sent-request')
    before = path.read_bytes()
    lock = path.with_suffix('.lock')
    lock.write_bytes(b'another owner')
    denied = PermissionError(13, 'Access denied', str(lock))
    original_open = costs.os.open

    def deny_lock(target, flags, *args, **kwargs):
        if Path(target) == lock:
            raise denied
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(costs.os, 'open', deny_lock)
    with pytest.raises(costs.BudgetError, match='lock unavailable') as failure:
        ledger.settle(ticket, {'input_tokens': 100, 'output_tokens': 10})
    assert failure.value.__cause__ is denied
    assert 'no request sent' not in str(failure.value)
    assert path.read_bytes() == before
    assert lock.read_bytes() == b'another owner'
    assert not path.with_suffix('.pending').exists()
    assert json.loads(path.read_text())['data']['calls'][ticket]['state'] == 'reserved'


@pytest.mark.parametrize('operation', ['construct', 'total', 'records'])
def test_open_reader_excludes_reservation_until_handle_closes(tmp_path, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Event, current_thread
    from enterprise_query.costs import CostLedger, BudgetError, RESERVATION
    path = tmp_path/'ledger.json'
    CostLedger.initialize(path, {'development': '0.60', 'research': '1'})
    ledger = CostLedger(path, 'development')
    opened, release = Event(), Event()
    original_read = Path.read_bytes

    def paused_read(target):
        if target == path and current_thread().name.startswith('ledger-reader'):
            # Hold a real OS handle: on Windows an uncoordinated replace fails.
            with target.open('rb') as handle:
                opened.set()
                assert release.wait(5), 'reader was not released'
                return handle.read()
        return original_read(target)

    def read():
        if operation == 'construct':
            return CostLedger(path, 'research')
        return ledger.total() if operation == 'total' else ledger.records({'one'})

    monkeypatch.setattr(Path, 'read_bytes', paused_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ledger-reader') as pool:
        reader = pool.submit(read)
        try:
            assert opened.wait(5), 'reader never opened the ledger'
            with pytest.raises(BudgetError, match='locked'):
                ledger.reserve('blocked')
            assert not path.with_suffix('.pending').exists()
        finally:
            release.set()
        reader.result(timeout=5)
    ticket = ledger.reserve('one')
    assert ledger.total() == RESERVATION
    assert set(ledger.records({'blocked', 'one'})) == {ticket}
    assert CostLedger(path, 'research').total() == 0


@pytest.mark.parametrize('operation', ['construct', 'total', 'records'])
def test_writer_excludes_readers_until_atomic_commit(tmp_path, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from enterprise_query import costs
    path = tmp_path/'ledger.json'
    costs.CostLedger.initialize(path, {'development': '0.60', 'research': '1'})
    ledger = costs.CostLedger(path, 'development')
    pending, release = Event(), Event()
    original_replace = costs.os.replace

    def paused_replace(source, destination):
        pending.set()
        assert release.wait(5), 'writer was not released'
        return original_replace(source, destination)

    monkeypatch.setattr(costs.os, 'replace', paused_replace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(ledger.reserve, 'one')
        try:
            assert pending.wait(5), 'writer never reached commit'
            with pytest.raises(costs.BudgetError, match='locked'):
                if operation == 'construct':
                    costs.CostLedger(path, 'research')
                elif operation == 'total':
                    ledger.total()
                else:
                    ledger.records({'one'})
        finally:
            release.set()
        ticket = writer.result(timeout=5)
    assert ledger.total() == costs.RESERVATION
    assert set(ledger.records({'one'})) == {ticket}


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
