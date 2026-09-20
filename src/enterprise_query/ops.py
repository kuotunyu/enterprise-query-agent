"""Opt-in, single-process operations lab around the existing business service."""
import asyncio
import copy
import math
import os
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .contracts import AnswerEnvelope, QuestionRequest, StrictModel
from .planner import MockPlanner
from .service import Service, Task

TIMINGS = ('queue_seconds', 'provider_seconds', 'sql_seconds', 'total_seconds')


class OpsError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class DelayedMockPlanner(MockPlanner):
    def __init__(self, delay=1.0, fail=False):
        if not math.isfinite(delay) or delay < 0 or delay > 60:
            raise ValueError('provider delay must be finite and between 0 and 60 seconds')
        self.delay, self.fail = delay, fail

    def decide(self, request, memory):
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError('injected mock provider failure')
        return super().decide(request, memory)


@dataclass
class Session:
    touched: float
    request_ids: set = field(default_factory=set)
    cancel_job: object = None


@dataclass
class RequestRecord:
    session_id: str
    fingerprint: str
    response: asyncio.Future
    started: float
    timings: dict = field(default_factory=lambda: dict.fromkeys(TIMINGS, 0.0))
    job: object = None
    complete: bool = False


class OpsRuntime:
    def __init__(self, executor_factory=None, provider=None, *, max_active=4,
                 max_sessions=500, retention_seconds=1800, max_requests_per_session=32,
                 processing_timeout=60, readiness_timeout=1.0, clock=time.perf_counter):
        if os.getenv('EQA_ENABLE_PAID_API', '0') != '0' or os.getenv('EQA_PROVIDER', 'mock') != 'mock':
            raise RuntimeError('operations lab refuses paid provider configuration')
        if os.getenv('EQA_BOOTSTRAP_PASSWORD'):
            raise RuntimeError('Bootstrap credentials cannot be present in the web process')
        if provider is not None and not isinstance(provider, MockPlanner):
            raise RuntimeError('operations lab requires a deterministic mock provider')
        if min(max_active, max_sessions, max_requests_per_session, retention_seconds,
               processing_timeout, readiness_timeout) <= 0:
            raise ValueError('operations limits must be positive')
        self.epoch = uuid4().hex
        self.clock = clock
        self.max_active, self.max_sessions = max_active, max_sessions
        self.retention_seconds = retention_seconds
        self.max_requests_per_session = max_requests_per_session
        self.readiness_timeout = readiness_timeout
        self.sessions, self.records = {}, {}
        self.active = 0
        self.draining = False
        self.readiness_job = None
        self.counters = Counter()
        self.outcomes = Counter()
        self.rejections = Counter()
        self.totals = {key: {'count': 0, 'sum': 0.0, 'max': 0.0} for key in TIMINGS}
        self.service = Service(executor_factory, provider or DelayedMockPlanner(),
                               observer=self._observe, processing_timeout=processing_timeout)

    def _observe(self, request_id, phase, seconds):
        record = self.records.get(request_id)
        if record is not None and phase in record.timings:
            record.timings[phase] += max(0.0, seconds)

    def _reject(self, code, status=409):
        self.rejections[code] += 1
        raise OpsError(code, status)

    def _session(self, epoch, session_id):
        if epoch != self.epoch:
            self._reject('stale_epoch')
        self.cleanup()
        if session_id not in self.sessions:
            self._reject('unknown_session', 404)
        return self.sessions[session_id]

    def cleanup(self):
        now = self.clock()
        for sid, session in list(self.sessions.items()):
            if session.cancel_job is not None and not session.cancel_job.done():
                continue
            if any(not self.records[rid].complete for rid in session.request_ids):
                continue
            if now - session.touched < self.retention_seconds:
                continue
            for rid in session.request_ids:
                self.records.pop(rid, None)
                self.service.requests.pop(rid, None)
                self.service.fingerprints.pop(rid, None)
                self.service.workers.pop(rid, None)
            self.service.tasks.pop(sid, None)
            del self.sessions[sid]

    def create_session(self):
        self.cleanup()
        if self.draining:
            self._reject('draining', 503)
        if len(self.sessions) >= self.max_sessions:
            self._reject('session_capacity', 429)
        sid = uuid4().hex
        self.sessions[sid] = Session(self.clock())
        self.service.tasks[sid] = Task()
        return {'epoch': self.epoch, 'session_id': sid}

    async def ask(self, epoch, request):
        session = self._session(epoch, request.session_id)
        fingerprint = request.model_dump_json()
        record = self.records.get(request.request_id)
        if record is not None:
            if record.fingerprint != fingerprint:
                self._reject('request_conflict')
            self.counters['coalesced'] += 1
        else:
            if self.draining:
                self._reject('draining', 503)
            if self.active >= self.max_active:
                self._reject('overloaded', 429)
            if len(session.request_ids) >= self.max_requests_per_session:
                self._reject('request_capacity', 429)
            record = RequestRecord(request.session_id, fingerprint,
                                   asyncio.get_running_loop().create_future(), self.clock())
            self.records[request.request_id] = record
            session.request_ids.add(request.request_id)
            self.active += 1
            self.counters['admitted'] += 1
            record.job = asyncio.create_task(self._run(request, record))
        answer = await asyncio.shield(record.response)
        timings = dict(record.timings)
        if not record.complete:
            timings['total_seconds'] = max(0.0, self.clock() - record.started)
        return {'epoch': self.epoch, 'answer': answer,
                'timings': timings, 'work_pending': not record.complete}

    async def _run(self, request, record):
        try:
            answer = await self.service.ask(request)
        except Exception:
            answer = AnswerEnvelope(status='execution_error', message='operations worker failed',
                                    request_id=request.request_id, session_id=request.session_id,
                                    revision=request.revision)
        record.response.set_result(answer.model_dump(mode='json'))
        # The response can be terminal while the provider or DB thread still runs.
        await self.service.wait_workers(request.request_id)
        session = self.sessions[request.session_id]
        if session.cancel_job is not None:
            await asyncio.gather(asyncio.shield(session.cancel_job), return_exceptions=True)
        record.timings['total_seconds'] = max(0.0, self.clock() - record.started)
        record.complete = True
        session.touched = self.clock()
        self.active -= 1
        self.outcomes[answer.status] += 1
        for key, seconds in record.timings.items():
            total = self.totals[key]
            total['count'] += 1
            total['sum'] += seconds
            total['max'] = max(total['max'], seconds)

    async def cancel(self, epoch, session_id):
        session = self._session(epoch, session_id)
        task = self.service.tasks[session_id]
        task.cancelled = True
        if session.cancel_job is None:
            # Repeated cancellation never queues unlimited KILL/connection work.
            session.cancel_job = asyncio.create_task(asyncio.to_thread(self.service.cancel, session_id))
        try:
            await asyncio.shield(session.cancel_job)
        except Exception:
            self._reject('cancel_failed', 503)
        return {'epoch': self.epoch, 'cancelled': True, 'outstanding_jobs': self.active}

    def drain(self):
        self.draining = True
        return {'epoch': self.epoch, 'draining': True, 'outstanding_jobs': self.active}

    async def ready(self):
        if self.draining:
            return False
        if self.readiness_job is None or self.readiness_job.done():
            async def check():
                try:
                    await asyncio.to_thread(self.service.executor_factory().check_identity)
                    return True
                except Exception:
                    return False
            self.readiness_job = asyncio.create_task(check())
        try:
            async with asyncio.timeout(self.readiness_timeout):
                ready = await asyncio.shield(self.readiness_job)
                return ready and not self.draining
        except TimeoutError:
            return False

    def meta(self):
        provider = self.service.provider
        return {'epoch': self.epoch, 'mode': 'mock', 'max_active': self.max_active,
                'max_sessions': self.max_sessions, 'retention_seconds': self.retention_seconds,
                'max_requests_per_session': self.max_requests_per_session,
                'provider_delay_seconds': getattr(provider, 'delay', 0),
                'provider_error': getattr(provider, 'fail', False), 'draining': self.draining}

    def metrics(self):
        self.cleanup()
        return {'epoch': self.epoch, 'active_jobs': self.active, 'sessions': len(self.sessions),
                'retained_requests': len(self.records), 'draining': self.draining,
                'admitted': self.counters['admitted'], 'coalesced': self.counters['coalesced'],
                'outcomes': dict(self.outcomes), 'rejections': dict(self.rejections),
                'timings': copy.deepcopy(self.totals)}

    async def close(self):
        self.drain()
        await asyncio.gather(*(asyncio.shield(r.job) for r in self.records.values()), return_exceptions=True)
        if self.readiness_job is not None:
            await asyncio.shield(self.readiness_job)


class OpsQuestion(QuestionRequest):
    epoch: str


class EpochRequest(StrictModel):
    epoch: str


def create_ops_app(runtime=None, *, executor_factory=None):
    if runtime is None:
        error = os.getenv('EQA_OPS_PROVIDER_ERROR', '0')
        if error not in ('0', '1'):
            raise ValueError('EQA_OPS_PROVIDER_ERROR must be 0 or 1')
        provider = DelayedMockPlanner(float(os.getenv('EQA_OPS_PROVIDER_DELAY_SECONDS', '1')), error == '1')
        runtime = OpsRuntime(executor_factory=executor_factory, provider=provider)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await runtime.close()

    app = FastAPI(title='EQA local operations lab', lifespan=lifespan)
    app.state.ops = runtime

    @app.exception_handler(OpsError)
    async def ops_error(request, exc):
        return JSONResponse({'error': exc.code, 'epoch': runtime.epoch}, status_code=exc.status)

    @app.get('/health/live')
    async def live():
        return {'live': True, 'epoch': runtime.epoch}

    @app.get('/health/ready')
    async def ready():
        value = await runtime.ready()
        return JSONResponse({'ready': value, 'epoch': runtime.epoch}, status_code=200 if value else 503)

    @app.get('/ops/meta')
    async def meta():
        return runtime.meta()

    @app.post('/ops/sessions')
    async def sessions():
        return runtime.create_session()

    @app.post('/ops/ask')
    async def ask(request: OpsQuestion):
        question = QuestionRequest.model_validate(request.model_dump(exclude={'epoch'}))
        return await runtime.ask(request.epoch, question)

    @app.post('/ops/cancel/{session_id}')
    async def cancel(session_id: str, request: EpochRequest):
        return await runtime.cancel(request.epoch, session_id)

    @app.post('/ops/drain')
    async def drain():
        return runtime.drain()

    @app.get('/ops/metrics')
    async def metrics():
        return runtime.metrics()

    return app
