import asyncio
import json
from decimal import Decimal

import pytest

from scripts import ops_measure as m


def good_answer():
    return {'answer': {'status': 'answered', 'population': 'delivered',
        'time_range': {'start': '2018-07-01', 'end': '2018-08-01'},
        'facts': [{'metric_id': k, 'value': str(v)} for k, v in
                  [('gmv', 242), ('delivered_order_count', 3), ('aov', Decimal(242)/3)]]}}


def test_independent_oracle_rejects_missing_duplicate_wrong_period_and_invented_facts():
    body = good_answer()
    assert m.oracle(body)
    body['answer']['facts'][0]['value'] = '243'
    assert not m.oracle(body)
    body = good_answer()
    body['answer']['facts'].append(body['answer']['facts'][0])
    assert not m.oracle(body)
    body = good_answer()
    body['answer']['time_range']['end'] = '2018-09-01'
    assert not m.oracle(body)
    assert not m.oracle({'answer': {'status': 'answered', 'facts': []}})
    assert m.no_answer({'answer': {'status': 'provider_error', 'facts': [], 'table': []}})
    assert not m.no_answer({'answer': {'status': 'provider_error', 'facts': [1]}})
    assert not m.no_answer({'answer': {'status': 'answered', 'facts': []}})


def test_default_cell_plan_is_open_loop_and_bounded():
    events = m.arrivals(2, 300)
    assert len(events) == 600
    assert events[299] == (299, 149.5, 299, 1)
    assert events[300] == (300, 150, 0, 2)
    assert events[-1] == (599, 299.5, 299, 2)
    assert len(m.arrivals(.25, 300)) == 75
    with pytest.raises(ValueError):
        m.arrivals(3, 300)


def test_summary_keeps_unsent_unknown_and_failed_outcomes_separate():
    rows = [{'kind': 'offered', 'id': str(i)} for i in range(5)]
    rows += [{'kind': 'sent', 'id': str(i), 'lag': i / 10} for i in range(4)]
    rows += [dict(kind='terminal', id='0', latency=1, http=200, correct=True, outcome='answered'),
             dict(kind='terminal', id='1', latency=2, http=429, correct=False, outcome='overloaded'),
             dict(kind='terminal', id='2', latency=3, http=None, correct=False, outcome='transport_timeout'),
             dict(kind='generator_failure', id='4', reason='session_still_active')]
    s = m.summarize_rows(rows)
    assert (s['offered'], s['sent'], s['terminal'], s['generator_failures']) == (5, 4, 3, 1)
    assert s['sent_without_terminal'] == 1
    assert s['offered_without_dispatch'] == 0
    assert s['http'] == {'200': 1, '429': 1, 'missing': 1}
    assert s['latency_all']['p50'] == 2
    assert s['latency_correct']['p95'] == 1
    assert s['latency_failed']['p50'] == 2.5
    assert m.percentiles([]) == {'count': 0, 'p50': None, 'p95': None}


def test_run_paths_exclusive_and_interrupted_log_recoverable(tmp_path):
    first = m.Run(tmp_path, 'load', {'diagnostic': True})
    second = m.Run(tmp_path, 'load', {})
    assert first.path != second.path
    first.emit('offered', id='x')
    with (first.path / 'events.jsonl').open('ab') as handle:
        handle.write(b'{"kind":')
    rows, truncated = m.read_rows(first.path)
    assert rows[0]['id'] == 'x'
    assert truncated == 1
    assert json.loads((first.path / 'manifest.json').read_text())['diagnostic']


def test_scheduler_does_not_wait_for_response_and_marks_busy_reuse():
    async def exercise():
        rows, release, started = [], asyncio.Event(), asyncio.Event()
        async def ask(index, session, revision):
            started.set()
            await release.wait()
            return {'http': 200, 'correct': True, 'work_pending': False, 'outcome': 'answered'}
        def emit(kind, **values):
            rows.append({'kind': kind, **values})
            if kind == 'generator_failure':
                release.set()
        await asyncio.wait_for(m.schedule([(0, 0, 0, 1), (1, .01, 0, 2)], ask, emit), 1)
        assert started.is_set()
        assert [r['kind'] for r in rows].count('offered') == 2
        assert [r['kind'] for r in rows].count('sent') == 1
        assert any(r.get('reason') == 'session_still_active' for r in rows)
    asyncio.run(exercise())


def test_pending_work_and_transport_uncertainty_prevent_session_reuse():
    async def exercise():
        rows = []
        async def ask(*args):
            return {'http': None, 'outcome': 'transport_timeout', 'correct': False, 'work_pending': True}
        await m.schedule([(0, 0, 0, 1), (1, .01, 0, 2)], ask,
                         lambda kind, **kw: rows.append(dict(kind=kind, **kw)))
        assert m.summarize_rows(rows)['generator_failures'] == 1
    asyncio.run(exercise())


def test_resource_failures_are_null_and_cpu_uses_process_delta():
    previous = {'cpu_seconds': 1, 'monotonic': 10, 'process_identity': 'a'}
    sample = {'cpu_seconds': 2, 'monotonic': 12, 'process_identity': 'a'}
    assert m.cpu_percent(sample, previous) == 50
    assert m.cpu_percent(dict(sample, process_identity='b'), previous) is None
    assert m.cpu_percent({'cpu_seconds': None}, previous) is None


def test_summary_does_not_merge_ids_from_different_cells():
    rows = [dict(kind='offered', cell='a', id='0'), dict(kind='sent', cell='a', id='0', lag=0),
            dict(kind='offered', cell='b', id='0'), dict(kind='sent', cell='b', id='0', lag=0),
            dict(kind='terminal', cell='b', id='0', latency=1, correct=True)]
    assert m.summarize_rows(rows)['sent_without_terminal'] == 1


def test_schedule_waits_through_full_window_even_when_responses_finish_early():
    async def exercise():
        rows = []
        await m.schedule([], None, lambda kind, **kw: rows.append(dict(kind=kind, **kw)), window=.04)
        assert rows[0]['elapsed_seconds'] >= .04
    asyncio.run(exercise())


def test_cli_requires_explicit_diagnostic_for_short_runs():
    with pytest.raises(SystemExit):
        m.parse_args(['load', 'eqaops-a', '--runtime-receipt', 'r.json', '--duration', '1'])
    args = m.parse_args(['soak', 'eqaops-a', '--runtime-receipt', 'r.json'])
    assert (args.duration, args.interval) == (86400, 60)


def test_db_fault_restores_even_when_failure_probe_assertion_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace
    calls = []
    lab = SimpleNamespace(path=tmp_path, config={}, restart=lambda: {})
    async def probe(*args, **kwargs):
        return {'epoch': 'old', 'session_id': 'one'}, {'body': good_answer()}
    async def request(*args, **kwargs):
        return 200, {'ready': True}
    monkeypatch.setattr(m, 'probe', probe)
    monkeypatch.setattr(m, 'request', request)
    monkeypatch.setattr(m.deploy, 'compose', lambda path, config, args: calls.append(args))
    monkeypatch.setattr(m.deploy, 'wait_ready', lambda config: {})
    run = SimpleNamespace(emit=lambda *args, **kwargs: None)
    with pytest.raises(AssertionError):
        asyncio.run(m.fault(lab, None, run, 'db', 1, None))
    assert calls[0][0] == 'stop'
    assert calls[-1][0] == 'up'


def test_session_setup_failure_is_not_counted_as_sent_ask():
    async def exercise():
        rows = []
        async def prepare(*args):
            raise RuntimeError('session capacity')
        async def never_ask(*args):
            pytest.fail('Must not ask when setup fails')
        await m.schedule([(0, 0, 0, 1)], never_ask,
                         lambda kind, **kw: rows.append(dict(kind=kind, **kw)), prepare=prepare)
        summary = m.summarize_rows(rows)
        assert (summary['offered'], summary['sent'], summary['generator_failures']) == (1, 0, 1)
    asyncio.run(exercise())
