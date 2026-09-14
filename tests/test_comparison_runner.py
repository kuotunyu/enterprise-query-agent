import asyncio
import json
import pytest
from enterprise_query.comparison_runner import run_development,digest
from enterprise_query.comparison import MockMethod
from tests.test_service import FakeExecutor

def test_append_only_started_terminal_failure_and_rotation(tmp_path):
    class Failing(MockMethod):
        def decide(self,*args): raise ValueError('private error text is not logged')
    directory=tmp_path/'immutable'
    result=asyncio.run(run_development(directory,FakeExecutor,questions=['2018年7月GMV','2018年7月毛利'],provider_factory=Failing))
    assert result['terminal_count']==6
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['method'] for e in events if e['event']=='started']==list('ABC')+list('BCA')
    assert all(e['outcome']['status']=='provider_error' for e in events if e['event']=='terminal')
    previous=None
    for event in events:
        assert event['previous_event_sha256']==previous
        previous=event.pop('event_sha256');assert digest(event)==previous
    original=(directory/'events.jsonl').read_bytes()
    manifest=(directory/'manifest.json').read_bytes()
    with pytest.raises(FileExistsError): asyncio.run(run_development(directory,FakeExecutor,questions=[]))
    assert original==(directory/'events.jsonl').read_bytes()
    assert manifest==(directory/'manifest.json').read_bytes()
    assert b'private error text' not in original

def test_runner_rejects_paid_provider_and_mismatched_manifest(tmp_path):
    class Paid(MockMethod):
        mode='openai'
        def decide(self,*args): pytest.fail('paid provider must not be called')
    with pytest.raises(ValueError,match='offline mock'):
        asyncio.run(run_development(tmp_path/'paid',FakeExecutor,questions=[],provider_factory=Paid))
    assert not (tmp_path/'paid').exists()
    bad=tmp_path/'data.json';bad.write_text('{"dataset_id":"wrong-snapshot"}')
    with pytest.raises(ValueError,match='manifest'):
        asyncio.run(run_development(tmp_path/'bad',FakeExecutor,questions=[],dataset_manifest=bad))
    assert not (tmp_path/'bad').exists()

def test_runner_records_clarification_turns_without_gold(tmp_path):
    directory=tmp_path/'clarify'
    summary=asyncio.run(run_development(directory,FakeExecutor,questions=['2018年7月營收多少']))
    assert summary['terminal_count']==3
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    for row in [e for e in events if e['event']=='terminal']:
        assert len(row['turns'])==2
        assert row['turns'][1]['request']['clarification']=='含運 GMV'
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['actual_api_calls']==0
    assert manifest['task_count']==3
    assert 'expected_facts' not in manifest['corpus'][0]
