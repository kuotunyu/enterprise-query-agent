"""Operations contracts exercise the real Service; only external work is replaced."""
import asyncio
import threading
import time

import httpx
import pytest

from enterprise_query.contracts import QuestionRequest
from enterprise_query.planner import MockPlanner
from tests.test_service import FakeExecutor


def runtime(**kwargs):
    from enterprise_query.ops import OpsRuntime
    return OpsRuntime(executor_factory=FakeExecutor, provider=MockPlanner(), **kwargs)


def question(session, key='one', revision=1):
    return QuestionRequest(question='2018年7月GMV', request_id=key,
                           session_id=session['session_id'], revision=revision)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.002)


class BlockingProvider(MockPlanner):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def decide(self, request, memory):
        self.entered.set()
        assert self.release.wait(2), 'test must release provider'
        return super().decide(request, memory)


def test_admission_coalesces_duplicates_and_preserves_provider_serialization():
    async def run():
        from enterprise_query.ops import OpsError, OpsRuntime
        provider = BlockingProvider()
        ops = OpsRuntime(executor_factory=FakeExecutor, provider=provider)
        sessions = [ops.create_session() for _ in range(5)]
        jobs = [asyncio.create_task(ops.ask(ops.epoch, question(s, str(i))))
                for i, s in enumerate(sessions[:4])]
        await until(lambda: provider.entered.is_set() and ops.active == 4)
        duplicate = asyncio.create_task(ops.ask(ops.epoch, question(sessions[0], '0')))
        with pytest.raises(OpsError, match='overloaded'):
            await ops.ask(ops.epoch, question(sessions[4], '4'))
        conflict = question(sessions[0], '0').model_copy(update={'question': 'another'})
        with pytest.raises(OpsError, match='request_conflict'):
            await ops.ask(ops.epoch, conflict)
        assert ops.active == 4
        provider.release.set()
        answers = await asyncio.gather(*jobs, duplicate)
        await until(lambda: ops.active == 0)
        assert [a['answer']['status'] for a in answers] == ['answered'] * 5
        assert answers[0]['answer'] == answers[-1]['answer']
        metrics = ops.metrics()
        assert metrics['admitted'] == 4 and metrics['coalesced'] == 1
        assert metrics['outcomes'] == {'answered': 4}
        assert metrics['timings']['provider_seconds']['count'] == 4
        assert metrics['timings']['sql_seconds']['count'] == 4
    asyncio.run(run())


def test_retention_capacity_epoch_and_request_cache_are_bounded():
    async def run():
        from enterprise_query.ops import OpsError
        clock = [10.0]
        ops = runtime(max_sessions=1, max_requests_per_session=1, retention_seconds=5,
                      clock=lambda: clock[0])
        session = ops.create_session()
        with pytest.raises(OpsError, match='session_capacity'):
            ops.create_session()
        with pytest.raises(OpsError, match='stale_epoch'):
            await ops.ask('old', question(session))
        result = await ops.ask(ops.epoch, question(session))
        await until(lambda: ops.active == 0)
        with pytest.raises(OpsError, match='request_capacity'):
            await ops.ask(ops.epoch, question(session, 'two', 2))
        assert (await ops.ask(ops.epoch, question(session)))['answer'] == result['answer']
        clock[0] += 6
        replacement = ops.create_session()
        assert replacement['session_id'] != session['session_id']
        assert not ops.service.requests and not ops.service.fingerprints
        with pytest.raises(OpsError, match='unknown_session'):
            await ops.ask(ops.epoch, question(session))
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['provider', 'sql', 'identity'])
def test_timeout_retains_slot_and_session_until_real_work_finishes(phase):
    async def run():
        from enterprise_query.ops import OpsError, OpsRuntime
        entered, release = threading.Event(), threading.Event()
        class BlockingExecutor(FakeExecutor):
            def check_identity(self):
                if phase == 'identity': entered.set(); release.wait(2)
                return super().check_identity()
            def execute(self, *args, **kwargs):
                if phase == 'sql': entered.set(); release.wait(2)
                return super().execute(*args, **kwargs)
        provider = BlockingProvider() if phase == 'provider' else MockPlanner()
        if phase == 'provider': entered, release = provider.entered, provider.release
        clock = [1.0]
        ops = OpsRuntime(executor_factory=BlockingExecutor, provider=provider,
                         max_active=1, max_sessions=1, processing_timeout=.04,
                         retention_seconds=1, clock=lambda: clock[0])
        session = ops.create_session()
        result = await ops.ask(ops.epoch, question(session))
        assert entered.is_set() and result['answer']['status'] == 'timeout'
        assert result['work_pending'] and ops.active == 1
        clock[0] += 10
        with pytest.raises(OpsError, match='session_capacity'): ops.create_session()
        assert ops.drain()['outstanding_jobs'] == 1
        release.set()
        await until(lambda: ops.active == 0)
        assert ops.metrics()['outcomes'] == {'timeout': 1}
        assert ops.metrics()['timings'][('provider' if phase == 'provider' else 'sql') + '_seconds']['sum'] > 0
        # Completion starts retention, rather than the much earlier timeout.
        ops.cleanup()
        assert session['session_id'] in ops.sessions
        clock[0] += 2
        ops.cleanup()
        assert not ops.sessions and not ops.service.requests and not ops.service.fingerprints
    asyncio.run(run())


def test_disconnect_cancel_and_drain_do_not_release_lingering_provider():
    async def run():
        from enterprise_query.ops import OpsError, OpsRuntime
        provider = BlockingProvider()
        ops = OpsRuntime(executor_factory=FakeExecutor, provider=provider)
        session = ops.create_session()
        waiter = asyncio.create_task(ops.ask(ops.epoch, question(session)))
        await until(provider.entered.is_set)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
        await ops.cancel(ops.epoch, session['session_id'])
        assert ops.active == 1
        assert ops.drain()['outstanding_jobs'] == 1
        with pytest.raises(OpsError, match='draining'): ops.create_session()
        with pytest.raises(OpsError, match='draining'):
            await ops.ask(ops.epoch, question(session, 'next', 2))
        provider.release.set()
        replay = await ops.ask(ops.epoch, question(session))
        assert replay['answer']['status'] == 'rejected'
        await until(lambda: ops.active == 0)
    asyncio.run(run())


def test_live_readiness_rechecks_and_coalesces_lingering_checks():
    async def run():
        from enterprise_query.ops import OpsRuntime, create_ops_app
        state = {'calls': 0, 'blocked': False, 'broken': False}
        release = threading.Event()
        class Dependency(FakeExecutor):
            def check_identity(self):
                state['calls'] += 1
                if state['blocked']: release.wait(2)
                if state['broken']: raise RuntimeError('secret SQL value')
                return super().check_identity()
        ops = OpsRuntime(executor_factory=Dependency, provider=MockPlanner(), readiness_timeout=.1)
        app = create_ops_app(ops)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.get('/health/ready')).status_code == 200
            state['broken'] = True
            assert (await client.get('/health/ready')).status_code == 503
            state['blocked'] = True
            checks = await asyncio.gather(*[client.get('/health/ready') for _ in range(8)])
            assert all(r.status_code == 503 for r in checks)
            assert state['calls'] == 3
            assert (await client.get('/health/live')).status_code == 200
            assert 'secret' not in ''.join(r.text for r in checks)
            assert (await client.post('/ops/drain')).json()['outstanding_jobs'] == 0
            assert (await client.get('/health/ready')).status_code == 503
            release.set()
            await until(lambda: ops.readiness_job.done())
    asyncio.run(run())


def test_http_epoch_error_injection_and_safe_metrics(monkeypatch):
    async def run():
        from enterprise_query.ops import create_ops_app
        monkeypatch.setenv('EQA_OPS_PROVIDER_DELAY_SECONDS', '0.01')
        monkeypatch.setenv('EQA_OPS_PROVIDER_ERROR', '1')
        app = create_ops_app(executor_factory=FakeExecutor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            session = (await client.post('/ops/sessions')).json()
            payload = {'epoch': session['epoch'], **question(session).model_dump(mode='json')}
            response = await client.post('/ops/ask', json=payload)
            assert response.json()['answer']['status'] == 'provider_error'
            await until(lambda: app.state.ops.active == 0)
            meta = (await client.get('/ops/meta')).json()
            assert meta['provider_delay_seconds'] == .01 and meta['provider_error'] is True
            payload['epoch'] = 'old'
            assert (await client.post('/ops/ask', json=payload)).status_code == 409
            report = (await client.get('/ops/metrics')).json()
            assert report['outcomes'] == {'provider_error': 1}
            assert report['timings']['provider_seconds']['sum'] >= .01
            assert 'GMV' not in str(report) and 'SELECT' not in str(report)
    asyncio.run(run())
    monkeypatch.setenv('EQA_ENABLE_PAID_API', '1')
    from enterprise_query.ops import create_ops_app
    with pytest.raises(RuntimeError, match='paid'): create_ops_app()


def test_timeout_cancel_io_does_not_block_liveness_or_release_capacity():
    async def run():
        from enterprise_query.ops import OpsRuntime, create_ops_app
        sql_entered, sql_release = threading.Event(), threading.Event()
        cancel_entered, cancel_release = threading.Event(), threading.Event()
        class BlockingSQL(FakeExecutor):
            def execute(self, *args, **kwargs):
                sql_entered.set()
                assert sql_release.wait(2)
                return super().execute(*args, **kwargs)
            def cancel(self):
                cancel_entered.set()
                assert cancel_release.wait(2)
        ops = OpsRuntime(executor_factory=BlockingSQL, provider=MockPlanner(), processing_timeout=.05)
        session = ops.create_session()
        work = asyncio.create_task(ops.ask(ops.epoch, question(session)))
        # Independent thread releases the fake network operation even if a
        # regression blocks the event loop; elapsed time then reveals the block.
        timer = threading.Timer(.4, cancel_release.set)
        timer.start()
        try:
            started = time.perf_counter()
            await until(cancel_entered.is_set)
            app = create_ops_app(ops)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                assert (await client.get('/health/live')).status_code == 200
            assert time.perf_counter() - started < .3
            assert sql_entered.is_set() and ops.active == 1
            cancel_release.set()
            response = await work
            assert response['answer']['status'] == 'timeout'
            assert response['timings']['total_seconds'] > 0 and response['work_pending']
            assert ops.active == 1
        finally:
            sql_release.set()
            cancel_release.set()
            timer.cancel()
            await work
            await until(lambda: ops.active == 0)
    asyncio.run(run())


def test_drain_wins_over_readiness_check_started_before_drain():
    async def run():
        entered, release = threading.Event(), threading.Event()
        class Dependency(FakeExecutor):
            def check_identity(self):
                entered.set()
                assert release.wait(2)
                return super().check_identity()
        from enterprise_query.ops import OpsRuntime
        ops = OpsRuntime(executor_factory=Dependency, provider=MockPlanner())
        check = asyncio.create_task(ops.ready())
        await until(entered.is_set)
        ops.drain()
        release.set()
        assert await check is False
    asyncio.run(run())


def test_serialized_provider_queue_timeout_records_wait_without_dispatch():
    async def run():
        from enterprise_query.ops import OpsRuntime
        provider = BlockingProvider()
        ops = OpsRuntime(executor_factory=FakeExecutor, provider=provider, processing_timeout=.06)
        first, second = ops.create_session(), ops.create_session()
        one = asyncio.create_task(ops.ask(ops.epoch, question(first)))
        await until(provider.entered.is_set)
        two = await ops.ask(ops.epoch, question(second, 'two'))
        assert two['answer']['status'] == 'timeout'
        assert two['answer']['usage']['model_calls'] == 0
        assert two['timings']['queue_seconds'] > .02
        assert two['timings']['provider_seconds'] == 0
        assert ops.active == 1
        provider.release.set()
        await one
        await until(lambda: ops.active == 0)
    asyncio.run(run())


def test_failed_cancel_keeps_slot_until_sql_finishes_then_releases_it():
    async def run():
        from enterprise_query.ops import OpsError, OpsRuntime
        entered, release = threading.Event(), threading.Event()
        class SQL(FakeExecutor):
            def execute(self, *args, **kwargs):
                entered.set()
                assert release.wait(2)
                return super().execute(*args, **kwargs)
            def cancel(self):
                raise RuntimeError('secret connection details')
        ops = OpsRuntime(executor_factory=SQL, provider=MockPlanner())
        session = ops.create_session()
        work = asyncio.create_task(ops.ask(ops.epoch, question(session)))
        await until(entered.is_set)
        try:
            with pytest.raises(OpsError, match='cancel_failed'):
                await ops.cancel(ops.epoch, session['session_id'])
            assert ops.active == 1
        finally:
            release.set()
            assert (await work)['answer']['status'] == 'rejected'
        await until(lambda: ops.active == 0)
        assert 'secret' not in str(ops.metrics())
    asyncio.run(run())
