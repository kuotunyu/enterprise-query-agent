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


@pytest.mark.parametrize('partial_character', [b'\xe4', b'\xe4\xb8'])
def test_partial_utf8_tail_recovers_summary_without_changing_raw(tmp_path, partial_character):
    complete = json.dumps({'kind': 'offered', 'id': '中文字'}, ensure_ascii=False).encode('utf-8')
    raw = complete + b'\n{"kind":"terminal","message":"' + partial_character
    logfile = tmp_path / 'events.jsonl'
    logfile.write_bytes(raw)
    rows, truncated = m.read_rows(tmp_path)
    assert rows == [{'kind': 'offered', 'id': '中文字'}]
    assert truncated == 1
    result = m.summary(tmp_path)
    assert (result['offered'], result['terminal'], result['truncated_tail']) == (1, 0, 1)
    assert result['status'] == 'interrupted_or_running'
    assert logfile.read_bytes() == raw


@pytest.mark.parametrize('corrupt_record', [b'{"kind":"\xff"}', b'{invalid-json}'])
def test_interior_utf8_or_json_corruption_is_rejected(tmp_path, corrupt_record):
    valid = b'{"kind":"offered","id":"one"}'
    raw = valid + b'\n' + corrupt_record + b'\n' + valid + b'\n'
    logfile = tmp_path / 'events.jsonl'
    logfile.write_bytes(raw)
    with pytest.raises(ValueError):
        m.summary(tmp_path)
    assert logfile.read_bytes() == raw
    assert not list(tmp_path.glob('summary-*.json'))


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


@pytest.mark.parametrize('outcome', ['interrupted', 'answered', 'timeout', 'refused', 'early_transport', 'prekill_transport', 'late', 'command_failure'])
def test_bounded_kill_rejects_completion_race_and_noninterruption(monkeypatch, outcome):
    from types import SimpleNamespace
    rows = []
    async def exercise():
        loop = asyncio.get_running_loop()
        released = asyncio.Event()
        async def ask(*args):
            if outcome != 'early_transport':
                await released.wait()
            if outcome == 'answered':
                return dict(http=200, outcome='answered', body=good_answer(), correct=True)
            if outcome == 'refused':
                return dict(http=200, outcome='timeout', body={'answer': {'status': 'timeout', 'facts': []}})
            return dict(http=None, outcome='transport_timeout' if outcome == 'timeout' else 'transport_error', body={})
        async def request(*args):
            await asyncio.sleep(0)
            return 200, {'active_jobs': 1}
        def kill_container(*args):
            import time
            if outcome == 'command_failure':
                raise RuntimeError('No signal sent')
            if outcome == 'prekill_transport':
                loop.call_soon_threadsafe(released.set)
                time.sleep(.01)  # HTTP completes after active observation, before signal starts.
            started = time.perf_counter()
            loop.call_soon_threadsafe(released.set)
            return dict(command_started_monotonic=started, command_finished_monotonic=time.perf_counter() + (6 if outcome == 'late' else 0),
                        state_after={'Status': 'exited', 'ExitCode': 137, 'OOMKilled': False})
        monkeypatch.setattr(m, 'ask', ask)
        monkeypatch.setattr(m, 'request', request)
        run = SimpleNamespace(emit=lambda kind, **kw: rows.append(dict(kind=kind, **kw)))
        lab = SimpleNamespace(kill_container=kill_container)
        call = m.interrupt_request(lab, None, run, {'epoch': 'e', 'session_id': 's'}, {'id': 'pinned'}, 3)
        if outcome == 'interrupted':
            await call
        else:
            with pytest.raises(RuntimeError if outcome == 'command_failure' else AssertionError):
                await call
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(exercise())
    observed = next(r for r in rows if r['kind'] == 'fault_observed')
    assert observed['valid_interruption'] == (outcome == 'interrupted')
    assert observed['repeat'] == 3 and observed['kill_protocol'] == 'pinned-kill-v1'
    if outcome == 'answered':
        assert observed['server_outcome'] == 'answered'


@pytest.mark.parametrize('scenario', ['valid', 'foreign', 'image', 'oom', 'exit_zero', 'expired', 'command_failure', 'late_finished'])
def test_pinned_kill_command_checks_scope_process_and_deadline(monkeypatch, tmp_path, scenario):
    import time
    from datetime import datetime, timezone, timedelta
    cid, image = 'a' * 64, 'sha256:runtime'
    started = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    state = dict(Status='running', Running=True, ExitCode=0, OOMKilled=False, StartedAt=started)
    bindings = {'8011/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18012'}]}
    info = dict(Id=cid, Image='other' if scenario == 'image' else image, State=state,
        Config={'Labels': {'com.docker.compose.project': 'foreign' if scenario == 'foreign' else 'eqaops-test',
                          'com.docker.compose.service': 'app'}, 'Env': ['SECRET=must-not-be-recorded']},
        HostConfig={'PortBindings': bindings}, NetworkSettings={'Ports': bindings}, Mounts=[])
    commands = []
    def command(args, **kwargs):
        commands.append(args)
        assert 0 < kwargs['timeout'] <= 3
        if args[:2] == ['docker', 'kill']:
            assert args[-1] == cid
            if scenario == 'command_failure':
                raise RuntimeError('kill failed')
            state.update(Status='exited', Running=False, ExitCode=0 if scenario == 'exit_zero' else 137,
                         OOMKilled=scenario == 'oom', FinishedAt=(datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat() if scenario == 'late_finished' else m.utc())
            return cid
        return json.dumps(state if '--format' in args else [info])
    monkeypatch.setattr(m.deploy, 'command', command)
    lab = object.__new__(m.Lab)
    lab.path, lab.config, lab.image = tmp_path, dict(stack='eqaops-test', http_port=18012), image
    deadline = time.perf_counter() + (-1 if scenario == 'expired' else 5)
    if scenario == 'valid':
        receipt = lab.kill_container({'id': cid[:12]}, m.utc(), deadline)
        assert receipt['status'] == 'passed' and receipt['state_after']['ExitCode'] == 137
    else:
        with pytest.raises((RuntimeError, ValueError, TimeoutError)):
            lab.kill_container({'id': cid[:12]}, m.utc(), deadline)
    saved = next((tmp_path / 'receipts').glob('*.json')).read_text()
    assert 'must-not-be-recorded' not in saved
    assert json.loads(saved)['status'] == ('passed' if scenario == 'valid' else 'failed')
    if scenario in ('foreign', 'image', 'expired'):
        assert not any(args[:2] == ['docker', 'kill'] for args in commands)


@pytest.mark.parametrize('replacement_starts,readiness_fails', [(True, False), (False, False), (True, True)])
def test_kill_recovery_replaces_zero_exit_stopped_container_and_preserves_checks(monkeypatch, tmp_path, replacement_starts, readiness_fails):
    """Model the observed boundary: normal Compose up succeeds without a start.

    This does not claim to reproduce the unidentified Docker/Compose trigger.
    It checks orchestration state/oracle/epoch outcomes, not just CLI flags.
    """
    from types import SimpleNamespace
    state = dict(running=True, active=False, epoch='before', container='original')
    commands, rows, probes, requests = [], [], [], []
    killed = None
    image = 'sha256:fixed-runtime'
    succeeds = replacement_starts and not readiness_fails
    def resources():
        return [dict(name='/eqaops-test-app-1', id=state['container'], image=image,
                     state='running' if state['running'] else 'exited')]
    lab = SimpleNamespace(path=tmp_path, config={}, image=image,
                          restart=lambda delay=1: {'provider_delay_seconds': delay, 'provider_error': False}, check=resources)
    def compose(path, config, args, **kwargs):
        commands.append(args)
        if args[0] == 'kill':
            state.update(running=False, active=False)
            loop.call_soon_threadsafe(killed.set)
            return 'Killed'
        if args[0] == 'up':
            assert kwargs['image'] == image
            if '--force-recreate' in args:
                state.update(container='replacement', epoch='after', running=replacement_starts)
            # Generic up intentionally returns zero output without changing state.
            return 'Recreated; Started' if state['running'] else ''
        raise AssertionError(args)
    def ready(config):
        if not state['running']:
            raise RuntimeError('App remains exited after successful Compose command')
        if readiness_fails:
            raise RuntimeError('New container running but readiness failed')
        return {'epoch': state['epoch'], 'ready': True}
    async def probe(client, run, label, **kwargs):
        assert state['running']
        probes.append(label)
        return {'epoch': state['epoch'], 'session_id': label}, {'correct': True}
    async def session(client):
        return {'epoch': state['epoch'], 'session_id': 'inflight'}
    async def ask(client, identity, revision=1):
        requests.append((identity['session_id'], revision))
        if identity['epoch'] != state['epoch']:
            return dict(http=409, outcome='stale_epoch')
        state['active'] = True
        await killed.wait()
        return dict(http=None, outcome='transport_error', body={}, correct=False)
    async def request(*args, **kwargs):
        if args[-1] == '/ops/meta':
            return 200, {'provider_delay_seconds': 1, 'provider_error': False}
        return 200, {'active_jobs': int(state['active'])}
    def kill_container(*args):
        import time
        started = time.perf_counter()
        compose(tmp_path, {}, ['kill'])
        return dict(command_started_monotonic=started, command_finished_monotonic=time.perf_counter(),
                    state_after={'Status': 'exited', 'ExitCode': 137, 'OOMKilled': False})
    lab.kill_container = kill_container
    monkeypatch.setattr(m.deploy, 'compose', compose)
    monkeypatch.setattr(m.deploy, 'wait_ready', ready)
    monkeypatch.setattr(m, 'probe', probe)
    monkeypatch.setattr(m, 'session', session)
    monkeypatch.setattr(m, 'ask', ask)
    monkeypatch.setattr(m, 'request', request)
    run = SimpleNamespace(emit=lambda kind, **kw: rows.append(dict(kind=kind, **kw)))
    async def exercise():
        nonlocal loop, killed
        loop, killed = asyncio.get_running_loop(), asyncio.Event()
        if succeeds:
            await m.fault(lab, None, run, 'kill', 1, None)
        else:
            with pytest.raises(RuntimeError):
                await m.fault(lab, None, run, 'kill', 1, None)
    loop = None
    asyncio.run(exercise())
    assert len([args for args in commands if args[0] == 'up']) == 1  # No retry.
    if succeeds:
        assert state['container'] == 'replacement'
        assert probes == ['kill:before', 'kill:after']
        assert requests == [('inflight', 1), ('inflight', 2)]  # Second is the explicit stale probe.
        assert any(r['kind'] == 'old_epoch_probe' and r['result']['http'] == 409 for r in rows)
        assert any(r['kind'] == 'fault_end' and r['status'] == 'passed' for r in rows)
    else:
        assert not any(r['kind'] == 'fault_end' for r in rows)
    receipt = json.loads(next((tmp_path / 'receipts').glob('*.json')).read_text())
    assert receipt['status'] == ('passed' if succeeds else 'failed')
    if succeeds:
        assert receipt['readiness']['epoch'] == 'after'
        assert next(r for r in rows if r['kind'] == 'kill_recovery')['status'] == 'passed'
