"""Independent HTTP measurements for one receipt-scoped synthetic operations lab.

Raw JSONL is append-only, flushed per event. No HTTP ask is retried. Full-duration
experiments require a reviewed stable runtime and runner; --diagnostic labels
abbreviated experiments. Run this process separately from the application.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import ops_deploy as deploy

QUESTION = '2018年7月GMV、訂單數與AOV'
RATES = [.25, .5, 1, 2]
FAULTS = ['db', 'provider-slow', 'provider-error', 'kill', 'rollback']


def utc():
    return datetime.now(timezone.utc).isoformat()


def oracle(body):
    """Hand arithmetic on the public synthetic July fixture, no planner imports."""
    answer = body.get('answer', {})
    facts = answer.get('facts', [])
    gold = {'gmv': Decimal(242), 'delivered_order_count': Decimal(3), 'aov': Decimal(242) / 3}
    try:
        values = {f['metric_id']: Decimal(str(f['value'])) for f in facts}
        return (answer.get('status') == 'answered' and len(facts) == 3
                and answer.get('population') == 'delivered'
                and answer.get('time_range') == {'start': '2018-07-01', 'end': '2018-08-01'}
                and values.keys() == gold.keys()
                and all(abs(values[k] - v) < Decimal('0.000001') for k, v in gold.items()))
    except (KeyError, TypeError, InvalidOperation):
        return False


def no_answer(body):
    answer = body.get('answer')
    return bool(answer and answer.get('status') != 'answered'
                and not answer.get('facts') and not answer.get('table'))


def arrivals(rate, duration):
    if not math.isfinite(rate) or not math.isfinite(duration) or rate <= 0 or duration <= 0:
        raise ValueError('Positive finite rate and duration required')
    count = math.ceil(rate * duration)
    if count > 600:
        raise ValueError('At most 600 offered asks per fresh epoch')
    sessions = min(300, count)
    return [(i, i / rate, i % sessions, 1 + i // sessions) for i in range(count)]


def percentiles(values):
    values = sorted(values)
    def at(q):
        if not values:
            return None
        index = (len(values) - 1) * q
        lo, hi = math.floor(index), math.ceil(index)
        return values[lo] + (values[hi] - values[lo]) * (index - lo)
    return {'count': len(values), 'p50': at(.5), 'p95': at(.95)}


def summarize_rows(rows):
    groups = {k: [r for r in rows if r['kind'] == k] for k in
              ('offered', 'sent', 'terminal', 'generator_failure', 'resource')}
    ids = lambda key: {(r.get('cell'), r['id']) for r in groups[key]}
    terminals = groups['terminal']
    output = {k: len(groups[k]) for k in ('offered', 'sent', 'terminal')}
    output.update(generator_failures=len(groups['generator_failure']),
        sent_without_terminal=len(ids('sent') - ids('terminal')),
        offered_without_dispatch=len(ids('offered') - ids('sent') - ids('generator_failure')),
        http=dict(Counter(str(r.get('http')) if r.get('http') is not None else 'missing' for r in terminals)),
        outcomes=dict(Counter(r.get('outcome', 'unknown') for r in terminals)),
        correct=sum(r.get('correct', False) for r in terminals),
        generator_reasons=dict(Counter(r['reason'] for r in groups['generator_failure'])),
        schedule_lag=percentiles([r['lag'] for r in groups['sent']]),
        resources={'samples': len(groups['resource']),
            'missing_cpu': sum(r.get('cpu_percent') is None for r in groups['resource']),
            'missing_rss': sum(r.get('rss_bytes') is None for r in groups['resource']),
            'missing_db': sum(r.get('db_connections') is None for r in groups['resource'])},
        gaps=[r for r in rows if r['kind'] == 'scheduling_gap'])
    for group, predicate in [('all', lambda r: True), ('correct', lambda r: r.get('correct')),
                             ('failed', lambda r: not r.get('correct'))]:
        output['latency_' + group] = percentiles([r['latency'] for r in terminals if predicate(r)])
    output['timings'] = {key: percentiles([r['timings'][key] for r in terminals
        if isinstance(r.get('timings', {}).get(key), (float, int))]) for key in
        ('queue_seconds', 'provider_seconds', 'sql_seconds', 'total_seconds')}
    output['resource_values'] = {key: percentiles([r[key] for r in groups['resource']
        if r.get(key) is not None]) for key in ('cpu_percent', 'rss_bytes', 'db_connections')}
    output['epochs'] = list(dict.fromkeys(r['metrics']['epoch'] for r in groups['resource']
                                         if r.get('metrics', {}).get('epoch')))
    output['faults'] = [r for r in rows if r['kind'] in ('fault_start', 'fault_end', 'recovery_ready')]
    output['oracle_probes'] = dict(Counter('correct' if r['result']['correct'] else
        'expected_no_answer' if r.get('expected_answer') is False and no_answer(r['result']['body']) else 'unexpected_failure'
        for r in rows if r['kind'] == 'oracle_probe'))
    return output


class Run:
    def __init__(self, root, action, manifest):
        self.path = Path(root) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + action + '-' + uuid4().hex)
        self.path.mkdir(parents=True, exist_ok=False)
        deploy.write_json(self.path / 'manifest.json', dict(manifest, action=action, started_utc=utc()))
        print(json.dumps({'run': str(self.path)}), flush=True)

    def emit(self, kind, **values):
        with (self.path / 'events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(kind=kind, utc=utc(), monotonic=time.perf_counter(), **values),
                                    ensure_ascii=False) + '\n')
            handle.flush()


def read_rows(path):
    rows, truncated = [], 0
    logfile = Path(path) / 'events.jsonl'
    if logfile.exists():
        # A killed append can cut a multibyte character. Decode each record
        # strictly so only the tail can be discarded, never interior corruption.
        lines = logfile.read_bytes().split(b'\n')
        if lines[-1] == b'':
            lines.pop()
        for index, line in enumerate(lines):
            try:
                rows.append(json.loads(line.decode('utf-8')))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if index != len(lines) - 1:
                    raise ValueError('Corrupt non-tail JSONL record')
                truncated += 1
    return rows, truncated


def summary(path):
    rows, truncated = read_rows(path)
    result = summarize_rows(rows)
    ends = [r for r in rows if r['kind'] == 'run_end']
    result.update(status=ends[-1]['status'] if ends else 'interrupted_or_running', truncated_tail=truncated,
                  cells={cell: summarize_rows([r for r in rows if r.get('cell') == cell])
                         for cell in sorted({r['cell'] for r in rows if 'cell' in r})})
    destination = Path(path) / ('summary-' + uuid4().hex + '.json')
    deploy.write_json(destination, result)
    print(json.dumps({'summary': str(destination), **{k: v for k, v in result.items() if k != 'cells'}}, ensure_ascii=False))
    return result


async def sleep_until(deadline):
    # Windows timers can wake early; a single sleep is not proof of a full window.
    while (remaining := deadline - time.perf_counter()) > 0:
        await asyncio.sleep(remaining)


async def schedule(events, ask, emit, *, window=None, prepare=None):
    """Absolute deadlines; outstanding response work never controls arrivals."""
    start, busy, tasks = time.perf_counter(), set(), []
    async def send(index, session, revision, ident, offset):
        if prepare:
            setup = time.perf_counter()
            try:
                await prepare(index, session, revision)
            except Exception as exc:
                emit('generator_failure', id=ident, reason='session_setup_failed', error=type(exc).__name__)
                busy.remove(session)
                return
            emit('session_setup', id=ident, seconds=time.perf_counter()-setup)
        begin = time.perf_counter()
        emit('sent', id=ident, lag=max(0, begin-start-offset))
        try:
            result = await ask(index, session, revision)
        except Exception as exc:
            result = dict(http=None, outcome='generator_exception', error=type(exc).__name__,
                          correct=False, work_pending=True)
        emit('terminal', id=ident, latency=time.perf_counter() - begin, **result)
        if not result.get('work_pending', True):
            busy.remove(session)
    for index, offset, session, revision in events:
        await sleep_until(start + offset)
        lag = max(0, time.perf_counter() - start - offset)
        ident = str(index)
        emit('offered', id=ident, offset=offset, lag=lag, session_index=session, revision=revision)
        if lag > 1:
            emit('scheduling_gap', offset=offset, lag=lag)
            emit('generator_failure', id=ident, reason='arrival_over_one_second_late', lag=lag)
        elif session in busy:
            emit('generator_failure', id=ident, reason='session_still_active', lag=lag)
        else:
            busy.add(session)
            tasks.append(asyncio.create_task(send(index, session, revision, ident, offset)))
    if window is not None:
        await sleep_until(start + window)
        emit('window_end', elapsed_seconds=time.perf_counter() - start, intended_seconds=window)
    await asyncio.gather(*tasks)
    emit('client_tail_end', elapsed_seconds=time.perf_counter() - start)


def cpu_percent(sample, previous):
    if not previous or sample.get('cpu_seconds') is None or previous.get('cpu_seconds') is None:
        return None
    if sample.get('process_identity') != previous.get('process_identity'):
        return None
    elapsed = sample['monotonic'] - previous['monotonic']
    delta = sample['cpu_seconds'] - previous['cpu_seconds']
    return 100 * delta / elapsed if elapsed > 0 and delta >= 0 else None


# Executed inside the app container. No credentials/SQL text leave that process.
RESOURCE_CODE = '''
import json, os
from pathlib import Path
from enterprise_query.executor import Executor
out = dict(cpu_seconds=None, rss_bytes=None, process_identity=None, db_connections=None, errors=[])
try:
    cmd = Path('/proc/1/cmdline').read_bytes()
    if b'uvicorn' not in cmd: raise ValueError('PID1 is not uvicorn')
    stat = Path('/proc/1/stat').read_text().split(') ', 1)[1].split()
    out['cpu_seconds'] = (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK')
    out['process_identity'] = Path('/etc/hostname').read_text().strip() + ':' + stat[19]
    out['rss_bytes'] = next(int(s.split()[1])*1024 for s in Path('/proc/1/status').read_text().splitlines() if s.startswith('VmRSS:'))
except Exception as exc: out['errors'].append('process:' + type(exc).__name__)
try:
    connection = Executor().connect(read_timeout=2)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            row = cursor.fetchone()
            out['db_connections'] = int(row['Value'] if isinstance(row, dict) else row[1])
    finally: connection.close()
except Exception as exc: out['errors'].append('db:' + type(exc).__name__)
print(json.dumps(out))
'''


class Lab:
    def __init__(self, stack, runtime_receipt):
        self.path, self.config = deploy.load_stack(stack)
        self.receipt_path = Path(runtime_receipt).resolve()
        self.receipt = json.loads(self.receipt_path.read_text(encoding='utf-8'))
        self.image = deploy.validate_image(self.receipt)
        if self.receipt.get('status') != 'passed' or self.receipt.get('target') != 'runtime':
            raise ValueError('Successful runtime receipt required')
        self.check()

    def check(self):
        records = deploy.resources(self.path, self.config)
        apps = [r for r in records if r['name'].endswith('-app-1')]
        if len(apps) != 1 or apps[0]['image'] != self.image:
            raise ValueError('Stack app does not match runtime receipt')
        return records

    def restart(self, delay=1, error=False):
        with deploy.operation(self.path / 'receipts', 'measurement-restart') as record:
            record['before'] = self.check()
            record['drain'] = deploy.drain(self.config)
            args = []
            if delay != 1 or error:
                override = self.path / ('provider-' + uuid4().hex + '.yaml')
                with override.open('x', encoding='utf-8') as handle:
                    handle.write(f'services:\n  app:\n    environment:\n      EQA_OPS_PROVIDER_DELAY_SECONDS: "{delay}"\n      EQA_OPS_PROVIDER_ERROR: "{int(error)}"\n')
                args = ['-f', str(override)]
                record.update(override=str(override), override_sha256=deploy.digest(override))
            record['image'] = self.image
            record['replace'] = deploy.compose(self.path, self.config,
                args + ['up', '-d', '--no-deps', '--force-recreate', 'app'], image=self.image)
            record['readiness'] = deploy.wait_ready(self.config)
            record['meta'] = deploy.http(self.config, '/ops/meta')[1]
            if (record['meta'].get('provider_delay_seconds') != delay or
                    record['meta'].get('provider_error') != error):
                raise ValueError('Provider configuration mismatch')
            if record['meta']['epoch'] == record['drain']['drain']['epoch']:
                raise ValueError('Replacement did not create a fresh epoch')
            record['after'] = self.check()
            return record['meta']

    def sample(self):
        try:
            return json.loads(deploy.compose(self.path, self.config,
                ['exec', '-T', 'app', 'python', '-c', RESOURCE_CODE], timeout=12))
        except Exception as exc:
            return dict(cpu_seconds=None, rss_bytes=None, process_identity=None,
                        db_connections=None, errors=[type(exc).__name__])


async def request(client, method, endpoint, body=None):
    try:
        response = await client.request(method, endpoint, json=body)
        return response.status_code, response.json()
    except httpx.TimeoutException:
        return None, {'error': 'transport_timeout'}
    except (httpx.HTTPError, ValueError) as exc:
        return None, {'error': 'transport_error', 'error_type': type(exc).__name__}


async def session(client):
    status, body = await request(client, 'POST', '/ops/sessions', {})
    if status != 200:
        raise RuntimeError('Session setup failed: ' + str(status))
    return body


async def ask(client, state, revision=1):
    begin = time.perf_counter()
    payload = dict(state, question=QUESTION, request_id=uuid4().hex, revision=revision, as_of_date='2018-08-01')
    status, body = await request(client, 'POST', '/ops/ask', payload)
    return dict(http=status, body=body, correct=status == 200 and oracle(body),
        http_seconds=time.perf_counter()-begin,
        outcome=body.get('answer', {}).get('status', body.get('error', 'unknown')),
        timings=body.get('timings', {}), work_pending=body.get('work_pending', status is None))


async def probe(client, run, label, *, expect=True):
    state = await session(client)
    result = await ask(client, state)
    run.emit('oracle_probe', label=label, expected_answer=expect, result=result)
    if expect and not result['correct']:
        raise AssertionError('Hand oracle failed: ' + label)
    return state, result


async def sample_loop(lab, client, emit, stop, interval=5):
    previous, last, last_wall = None, time.perf_counter(), time.time()
    while True:
        begin, wall = time.perf_counter(), time.time()
        if begin - last > interval * 2 or abs(wall - last_wall) > interval * 2:
            emit('scheduling_gap', source='sampler', gap_seconds=begin-last,
                 wall_gap_seconds=wall-last_wall, expected_seconds=interval)
        sample = await asyncio.to_thread(lab.sample)
        sample['monotonic'] = begin
        sample['cpu_percent'] = cpu_percent(sample, previous)
        sample['cpu_missing_reason'] = ('process_read_failed' if sample.get('cpu_seconds') is None
            else 'initial_or_changed_process_baseline') if sample['cpu_percent'] is None else None
        previous = sample.copy()
        sample.pop('monotonic')
        status, metrics = await request(client, 'GET', '/ops/metrics')
        ready_status, ready = await request(client, 'GET', '/health/ready')
        emit('resource', **sample, metrics_http=status, metrics=metrics, ready_http=ready_status,
             ready=ready, sample_seconds=time.perf_counter()-begin)
        last, last_wall = begin, wall
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(.01, interval - (time.perf_counter()-begin)))
        except TimeoutError:
            pass


async def cell(lab, client, run, rate, duration, repeat):
    label = f'{rate}rps-{repeat}'
    emit = lambda kind, **kw: run.emit(kind, cell=label, **kw)
    setup = time.perf_counter()
    meta = await asyncio.to_thread(lab.restart)
    events = arrivals(rate, duration)
    states = [await session(client) for _ in range(min(300, len(events)))]
    emit('setup', seconds=time.perf_counter()-setup, meta=meta, sessions=len(states), rate=rate, duration=duration)
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_loop(lab, client, emit, stop))
    try:
        await schedule(events, lambda i, s, r: ask(client, states[s], r), emit, window=duration)
        drained = await asyncio.to_thread(deploy.drain, lab.config)
        emit('server_tail_end', drain=drained)
    finally:
        stop.set()
        await sampler


async def stale(client, state, run):
    result = await ask(client, state, 2)
    run.emit('old_epoch_probe', result=result)
    if result['http'] != 409 or result['outcome'] != 'stale_epoch':
        raise AssertionError('Old epoch was not rejected')


async def fault(lab, client, run, name, repeat, bad_receipt):
    start = time.perf_counter()
    run.emit('fault_start', scenario=name, repeat=repeat)
    await asyncio.to_thread(lab.restart)
    old, _ = await probe(client, run, name + ':before')
    run.emit('fault_injection_start', scenario=name, repeat=repeat)
    recovery = None
    try:
        if name == 'db':
            await asyncio.to_thread(deploy.compose, lab.path, lab.config, ['stop', '-t', '10', 'mysql'])
            ready = await request(client, 'GET', '/health/ready')
            _, result = await probe(client, run, 'db:down', expect=False)
            run.emit('fault_observed', scenario=name, ready=ready, result=result)
            if ready[0] != 503 or not no_answer(result['body']):
                raise AssertionError('DB failure not visible or invented answer')
        elif name in ('provider-slow', 'provider-error'):
            await asyncio.to_thread(lab.restart, 5 if name == 'provider-slow' else 1, name == 'provider-error')
            state = await session(client)
            task = asyncio.create_task(ask(client, state))
            if name == 'provider-slow':
                await asyncio.sleep(.3)
                cancel = await request(client, 'POST', '/ops/cancel/' + state['session_id'], {'epoch': state['epoch']})
                metrics = await request(client, 'GET', '/ops/metrics')
                run.emit('http_cancel', result=cancel, metrics=metrics)
                if cancel[0] != 200 or metrics[1].get('active_jobs', 0) < 1:
                    raise AssertionError('Delayed provider capacity released too early')
            result = await task
            run.emit('fault_observed', scenario=name, result=result)
            expected = 'provider_error' if name == 'provider-error' else 'rejected'
            if result['outcome'] != expected or not no_answer(result['body']):
                raise AssertionError('Provider fault outcome mismatch')
        elif name == 'kill':
            state = await session(client)
            task = asyncio.create_task(ask(client, state))
            deadline = time.perf_counter() + 3
            while time.perf_counter() < deadline:
                _, metrics = await request(client, 'GET', '/ops/metrics')
                if metrics.get('active_jobs', 0):
                    break
                await asyncio.sleep(.02)
            else:
                raise AssertionError('No in-flight job observed')
            await asyncio.to_thread(deploy.compose, lab.path, lab.config, ['kill', '-s', 'KILL', 'app'])
            result = await task
            run.emit('fault_observed', scenario=name, result=result, server_outcome='unknown; no replay')
            if result['http'] is not None and not no_answer(result['body']):
                raise AssertionError('Kill did not interrupt observed request')
        else:
            try:
                await asyncio.to_thread(deploy.manage, lab.config['stack'], 'update', bad_receipt)
            except RuntimeError as exc:
                run.emit('candidate_update_failed', error_type=type(exc).__name__)
            else:
                raise AssertionError('Unready candidate unexpectedly ready')
            live = await request(client, 'GET', '/health/live')
            ready = await request(client, 'GET', '/health/ready')
            run.emit('fault_observed', scenario=name, live=live, ready=ready)
            if live[0] != 200 or ready[0] != 503:
                raise AssertionError('Candidate did not exhibit live/unready split')
            _, result = await probe(client, run, 'rollback:unready', expect=False)
            if not no_answer(result['body']):
                raise AssertionError('Unready candidate fabricated an answer')
    except BaseException as exc:
        run.emit('fault_error', scenario=name, error_type=type(exc).__name__)
        raise
    finally:
        recovery = time.perf_counter()
        # Restoration runs even if an assertion/request failed. Never remove evidence/volumes.
        if name == 'db':
            await asyncio.to_thread(deploy.compose, lab.path, lab.config,
                ['up', '-d', '--no-deps', '--wait', '--wait-timeout', '90', 'mysql'])
        elif name == 'rollback':
            await asyncio.to_thread(deploy.manage, lab.config['stack'], 'rollback', str(lab.receipt_path))
        elif name == 'kill':
            # A successful generic Compose up did not restart one killed app.
            # Replace explicitly, retain the command output, and verify lifecycle.
            with deploy.operation(lab.path / 'receipts', 'measurement-kill-recovery') as receipt:
                receipt['before'] = await asyncio.to_thread(lab.check)
                receipt['image'] = lab.image
                receipt['replace'] = await asyncio.to_thread(deploy.compose, lab.path, lab.config,
                    ['up', '-d', '--no-deps', '--force-recreate', 'app'], image=lab.image, combined=True)
                receipt['after'] = await asyncio.to_thread(lab.check)
                prior = next(r for r in receipt['before'] if r['name'].endswith('-app-1'))
                current = next(r for r in receipt['after'] if r['name'].endswith('-app-1'))
                if current['id'] == prior['id'] or current['state'] != 'running':
                    raise RuntimeError('Killed app recovery did not create a new running container')
                receipt['readiness'] = await asyncio.to_thread(deploy.wait_ready, lab.config)
                if not receipt['readiness'].get('epoch') or receipt['readiness']['epoch'] == old['epoch']:
                    raise RuntimeError('Killed app recovery did not create a new epoch')
            run.emit('kill_recovery', **receipt)
        else:
            await asyncio.to_thread(lab.restart)
        if name != 'kill':
            await asyncio.to_thread(deploy.wait_ready, lab.config)
        run.emit('recovery_ready', scenario=name, seconds=time.perf_counter()-recovery)
    await probe(client, run, name + ':after')
    if name != 'db':
        await stale(client, old, run)
    else:
        run.emit('old_epoch_probe', applicable=False, reason='DB restart preserves app epoch')
    run.emit('fault_end', scenario=name, repeat=repeat, status='passed', total_seconds=time.perf_counter()-start,
             recovery_seconds=time.perf_counter()-recovery)


async def soak(lab, client, run, duration, interval):
    meta = await asyncio.to_thread(lab.restart)
    run.emit('setup', meta=meta, duration=duration, interval=interval)
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_loop(lab, client, run.emit, stop, interval=min(30, interval)))
    states = {}
    async def prepare(index, unused, revision):
        states[index] = await session(client)
    async def one(index, unused, revision):
        state = states.pop(index)
        result = await ask(client, state)
        result['epoch'] = state['epoch']
        return result
    try:
        count = math.ceil(duration / interval)
        await schedule([(i, i*interval, i, 1) for i in range(count)], one, run.emit,
                       window=duration, prepare=prepare)
        run.emit('server_tail_end', drain=await asyncio.to_thread(deploy.drain, lab.config))
    finally:
        stop.set()
        await sampler


async def execute(args, lab, run):
    # Independent HTTP transport; no Service, compiler or model imported here.
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{lab.config['http_port']}",
                               timeout=65, limits=httpx.Limits(max_connections=64), trust_env=False) as client:
        if args.action == 'smoke':
            await cell(lab, client, run, .5, 6, 1)
        elif args.action == 'load':
            for repeat in range(1, args.repeats + 1):
                for rate in args.rates:
                    await cell(lab, client, run, rate, args.duration, repeat)
        elif args.action == 'faults':
            for repeat in range(1, args.repeats + 1):
                for name in args.scenarios:
                    await fault(lab, client, run, name, repeat, args.bad_receipt)
        elif args.action == 'soak':
            await soak(lab, client, run, args.duration, args.interval)
        if args.action in ('smoke', 'load', 'soak'):
            # All measurements/final cache snapshots precede this explicit new epoch.
            run.emit('restore_ready', meta=await asyncio.to_thread(lab.restart))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    entry = sub.add_parser('summarize')
    entry.add_argument('path', type=Path)
    for action in ('smoke', 'load', 'faults', 'soak'):
        entry = sub.add_parser(action)
        entry.add_argument('stack')
        entry.add_argument('--runtime-receipt', required=True)
        entry.add_argument('--diagnostic', action='store_true')
        if action == 'load':
            entry.add_argument('--rates', type=float, nargs='+', default=RATES)
            entry.add_argument('--duration', type=float, default=300)
            entry.add_argument('--repeats', type=int, default=3)
        if action == 'faults':
            entry.add_argument('--bad-receipt', required=True)
            entry.add_argument('--scenarios', nargs='+', choices=FAULTS, default=FAULTS)
            entry.add_argument('--repeats', type=int, default=3)
        if action == 'soak':
            entry.add_argument('--duration', type=float, default=86400)
            entry.add_argument('--interval', type=float, default=60)
    args = parser.parse_args(argv)
    if args.action == 'load':
        for rate in args.rates:
            arrivals(rate, args.duration)
        if not args.diagnostic and (args.rates != RATES or args.duration != 300 or args.repeats != 3):
            parser.error('Nonstandard load requires --diagnostic')
    if args.action == 'faults' and not args.diagnostic and (args.scenarios != FAULTS or args.repeats != 3):
        parser.error('Nonstandard faults require --diagnostic')
    if args.action == 'soak':
        if args.duration <= 0 or args.interval <= 0 or not math.isfinite(args.duration + args.interval):
            parser.error('Positive finite duration and interval required')
        if not args.diagnostic and (args.duration != 86400 or args.interval != 60):
            parser.error('Nonstandard soak requires --diagnostic')
    if getattr(args, 'repeats', 1) < 1:
        parser.error('At least one repeat required')
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.action == 'summarize':
        summary(args.path)
        return
    lab = Lab(args.stack, args.runtime_receipt)
    manifest = dict(arguments=vars(args), diagnostic=args.diagnostic or args.action == 'smoke',
        runtime_receipt=str(lab.receipt_path), runtime_image=lab.image, runtime_source=lab.receipt['source_sha'],
        runner_head=deploy.command(['git', 'rev-parse', 'HEAD']),
        runner_dirty=bool(deploy.command(['git', 'status', '--porcelain'])),
        runner_sha256=deploy.digest(__file__), stack=lab.config, resources_before=lab.check(),
        db_connections_include_sampler=True, process_cpu_percent='one core = 100%; PID1 uvicorn',
        request_timeout_seconds=65, late_arrival_policy='over 1s late: generator failure, no catch-up burst',
        sql_cancel='separate real-MySQL executor integration tests; HTTP provider cancel is not SQL cancel')
    run = Run(deploy.OPS / 'runs', args.action, manifest)
    # Preserve the exact host harness even for explicitly labeled dirty diagnostics.
    for name in ('ops_measure.py', 'ops_deploy.py'):
        with (run.path / name).open('xb') as handle:
            handle.write((deploy.ROOT / 'scripts' / name).read_bytes())
    try:
        asyncio.run(execute(args, lab, run))
    except BaseException as exc:
        run.emit('run_end', status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error_type=type(exc).__name__)
        raise
    else:
        run.emit('run_end', status='completed')
    finally:
        summary(run.path)


if __name__ == '__main__':
    main()
