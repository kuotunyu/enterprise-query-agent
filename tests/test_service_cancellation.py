"""Cancellation must not run DB socket work on the event loop.

Executor.cancel opens a connection, issues KILL CONNECTION and polls
SHOW PROCESSLIST, so it blocks for as long as the database takes to answer.
The shipped app builds Service() without an observer (api.py), so these tests
pin the no-observer configuration specifically.

Gates are threading.Events, not sleeps: a test releases cancel only after it
has observed the loop still running, so a regression fails on the gate rather
than on a timing threshold.
"""
from datetime import date
import asyncio
import threading
import pytest
from enterprise_query.contracts import QuestionRequest, QueryResult
from enterprise_query.service import Service, Task

GATE = 3  # seconds a gated fake waits before giving up, so a regression fails fast


def req(request_id, session_id='s', revision=1):
    return QuestionRequest(question='2018年7月GMV', request_id=request_id,
                           session_id=session_id, revision=revision, as_of_date=date(2026, 9, 14))


class GatedCancel:
    """Stands in for an Executor whose cancel() is waiting on the database."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread = None
        self.calls = 0

    def cancel(self):
        self.calls += 1
        self.thread = threading.current_thread()
        self.entered.set()
        if not self.release.wait(GATE):
            raise AssertionError('cancel ran on a caller that never released the gate')

    def check_identity(self): return {'dataset_id': 'synthetic-v1'}

    def execute(self, sql, parameters=(), timeout_ms=5000):
        return QueryResult(query_id='q', sql=sql, parameters=list(parameters),
                           columns=['gmv', 'delivered_order_count', 'population_count'],
                           column_types=['DECIMAL', 'LONG', 'LONG'],
                           rows=[{'gmv': 242, 'delivered_order_count': 3, 'population_count': 3}],
                           row_count=1, truncated=False, elapsed_ms=1,
                           dataset_id='synthetic-v1', result_hash='hash')


async def spin(predicate, timeout=GATE):
    """Yield to the loop until predicate holds. Raises if the loop is blocked."""
    async def wait():
        while not predicate(): await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), timeout)


async def loop_is_live():
    """A loop turn count that cannot advance while the loop is blocked."""
    turns = 0
    async def tick():
        nonlocal turns
        while True:
            turns += 1
            await asyncio.sleep(.001)
    job = asyncio.create_task(tick())
    await asyncio.sleep(0)
    return job, lambda: turns


def test_revision_cancellation_keeps_the_event_loop_live():
    async def run():
        gate = GatedCancel()
        service = Service(GatedCancel)
        service.tasks['s'] = Task(revision=1)
        service.tasks['s'].executor = gate
        ticker, turns = await loop_is_live()
        bump = asyncio.create_task(service.ask(req('bump', revision=2)))
        try:
            # Reaching this at all means the loop kept scheduling during cancel.
            await spin(gate.entered.is_set)
            before = turns()
            await spin(lambda: turns() > before)
            assert gate.thread is not threading.main_thread(), 'cancel ran on the loop thread'
        finally:
            gate.release.set()
            ticker.cancel()
        answer = await asyncio.wait_for(bump, GATE)
        assert gate.calls == 1
        assert service.tasks['s'].revision == 2
        assert answer.status == 'answered'
    asyncio.run(run())


def test_timeout_cancellation_keeps_the_event_loop_live():
    created = []

    class SlowIdentity(GatedCancel):
        def __init__(self):
            super().__init__()
            self.identity = threading.Event()
            created.append(self)

        def check_identity(self):
            # Outlives the processing budget, so asyncio.timeout fires here.
            self.identity.wait(GATE)
            return {'dataset_id': 'synthetic-v1'}

        def cancel(self):
            self.identity.set()  # a real cancel drops the connection
            super().cancel()

    async def run():
        service = Service(SlowIdentity, processing_timeout=.5)
        ticker, turns = await loop_is_live()
        work = asyncio.create_task(service.ask(req('slow')))
        try:
            await spin(lambda: bool(created))
            gate = created[0]
            await spin(gate.entered.is_set, timeout=GATE + 1)
            before = turns()
            await spin(lambda: turns() > before)
            assert gate.thread is not threading.main_thread(), 'cancel ran on the loop thread'
        finally:
            for executor in created: executor.release.set(); executor.identity.set()
            ticker.cancel()
        answer = await asyncio.wait_for(work, GATE)
        assert created[0].calls == 1
        assert answer.status == 'timeout'
    asyncio.run(run())


@pytest.mark.xfail(reason='known limitation: ask() holds self.guard across the offloaded cancel',
                   strict=True)
def test_revision_cancellation_does_not_unblock_other_sessions():
    """Offloading cancel frees the loop, not the process-wide guard.

    ask() still holds self.guard while awaiting cancellation, so an unrelated
    session's request cannot start until the cancel returns. Recorded as a
    strict xfail: it flips to a failure the day a lock redesign fixes it, and
    stops the loop-responsiveness fix above being read as a fix for this.
    """
    async def run():
        gate = GatedCancel()
        service = Service(GatedCancel)
        service.tasks['s'] = Task(revision=1)
        service.tasks['s'].executor = gate
        bump = asyncio.create_task(service.ask(req('bump', revision=2)))
        other = asyncio.create_task(service.ask(req('other', session_id='other')))
        try:
            await spin(gate.entered.is_set)
            await spin(other.done, timeout=.5)
        finally:
            gate.release.set()
            await asyncio.gather(bump, other, return_exceptions=True)
    asyncio.run(run())
