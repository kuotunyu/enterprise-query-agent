import asyncio
import time
import copy
from dataclasses import dataclass, field
from .contracts import AnswerEnvelope, PlannerDecision
from .providers import configured_provider

@dataclass
class Task:
    revision: int = 0
    memory: dict = field(default_factory=dict)
    calls: int = 0
    sql: int = 0
    clarifications: int = 0
    active: float = 0
    started: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    executor: object = None
    attempts: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

class Service:
    def __init__(self, executor_factory=None, provider=None):
        if executor_factory is None:
            from .executor import Executor
            executor_factory=Executor
        self.executor_factory=executor_factory
        self.provider=provider or configured_provider()
        self.tasks={}
        self.requests={}
        self.fingerprints={}
        self.guard=asyncio.Lock()
        self.provider_lock=asyncio.Lock()
        self.provider_jobs=set()

    def cancel(self,session_id):
        task=self.tasks.get(session_id)
        if task:
            task.cancelled=True
            if task.executor is not None: task.executor.cancel()
        return {'cancelled':bool(task)}

    async def ask(self,request):
        key=request.request_id
        fingerprint=request.model_dump_json()
        async with self.guard:
            if key in self.requests:
                if self.fingerprints[key]!=fingerprint:
                    return AnswerEnvelope(status='rejected',message='request_id 已用於另一個請求。',request_id=key,session_id=request.session_id,revision=request.revision)
                future=self.requests[key]
            else:
                task=self.tasks.setdefault(request.session_id,Task())
                if request.revision <= task.revision:
                    return AnswerEnvelope(status='rejected',message='此修訂已過期。',request_id=key,session_id=request.session_id,revision=request.revision)
                if request.revision > task.revision:
                    if task.executor is not None: task.executor.cancel()
                    task.revision=request.revision
                future=asyncio.create_task(self._run(request,task))
                self.requests[key]=future
                self.fingerprints[key]=fingerprint
        answer=await asyncio.shield(future)
        return self._stamp(answer.model_copy(deep=True),request,self.tasks[request.session_id])

    async def _run(self,r,t):
        async with t.lock:
            started=time.monotonic()
            def envelope(status,message,**kwargs):
                return AnswerEnvelope(status=status,message=message,**kwargs)
            answer=None
            try:
                if r.revision!=t.revision: return self._stamp(envelope('rejected','此修訂已過期。'),r,t)
                if t.cancelled: return self._stamp(envelope('rejected','任務已取消；請建立新任務。'),r,t)
                if t.calls>=3: return self._stamp(envelope('rejected','已達每任務 3 次規劃呼叫上限；請建立新任務。'),r,t)
                remaining=60-t.active
                if remaining<=0: return self._stamp(envelope('timeout','任務已達 60 秒實際處理上限。'),r,t)
                async with asyncio.timeout(remaining):
                    # Waiting for a provider slot is cancellable; no thread is queued yet.
                    await self.provider_lock.acquire()
                    if t.cancelled or r.revision!=t.revision:
                        self.provider_lock.release()
                        return self._stamp(envelope('rejected','任務已取消或修訂已更新。'),r,t)
                    provider_remaining=remaining-(time.monotonic()-started)
                    if provider_remaining<=0:
                        self.provider_lock.release()
                        raise TimeoutError('provider queue deadline')
                    t.calls+=1
                    memory=copy.deepcopy(t.memory)
                    attempt={'call':t.calls,'status':'started','provider_usage':None}
                    t.attempts.append(attempt)
                    # This job owns the lock until the actual provider call completes,
                    # even when the request deadline expires while awaiting its result.
                    job=asyncio.create_task(self._provider_job(r,t,memory,attempt,started+remaining))
                    self.provider_jobs.add(job)
                    job.add_done_callback(self.provider_jobs.discard)
                    try:
                        decision,error_type=await asyncio.shield(job)
                        if error_type: raise ValueError('provider failure')
                        if r.revision==t.revision and not t.cancelled: t.memory.update(memory)
                    except Exception:
                        answer=envelope('provider_error','規劃服務失敗或回傳無效結構；此次呼叫已計入額度。')
                    if answer is None:
                        if decision.action=='clarify':
                            if t.clarifications>=2: answer=envelope('rejected','已達兩輪澄清上限；請以完整問題建立新任務。')
                            else:
                                t.clarifications+=1
                                answer=envelope('needs_clarification',decision.message,options=decision.options)
                        elif decision.action=='unsupported': answer=envelope('unsupported',decision.message)
                        else:
                            from .compiler import compile_plan
                            from .facts import build_answer
                            queries=compile_plan(decision.plan)
                            if t.sql+len(queries)>3: answer=envelope('rejected','已達每任務 3 條業務 SELECT 上限；请建立新任務。')
                            else:
                                t.executor=self.executor_factory()
                                await asyncio.to_thread(t.executor.check_identity)
                                results=[]
                                for query in queries:
                                    if t.cancelled or r.revision!=t.revision: break
                                    t.sql+=1
                                    result=await asyncio.to_thread(t.executor.execute,query.sql,parameters=query.parameters,timeout_ms=min(5000,max(1,int((60-t.active-(time.monotonic()-started))*1000))))
                                    results.append(result)
                                if t.cancelled or r.revision!=t.revision: answer=envelope('rejected','任務已取消或修訂已更新。')
                                else: answer=build_answer(decision.plan,results)
            except (TimeoutError, asyncio.TimeoutError):
                if t.attempts and t.attempts[-1]['status']=='started': t.attempts[-1].update(status='timeout',error_type='TimeoutError')
                if t.executor is not None: t.executor.cancel()
                answer=envelope('timeout','查詢或任務超時，已要求資料庫停止執行。')
            except Exception as exc:
                # Do not leak connection strings, filesystem paths or provider traces.
                status='rejected' if type(exc).__name__=='PolicyError' else ('timeout' if 'timeout' in type(exc).__name__.lower() else 'execution_error')
                t.errors.append({'revision':r.revision,'error_type':type(exc).__name__,'status':status})
                answer=envelope(status,'執行失敗；請確認獨立資料庫身分、版本與唯讀設定。')
            finally:
                t.active+=time.monotonic()-started
                t.executor=None
            if r.revision!=t.revision or t.cancelled: answer=envelope('rejected','任務已取消或修訂已更新。')
            return self._stamp(answer,r,t)

    async def _provider_job(self,r,t,memory,attempt,deadline):
        def plan_once():
            remaining=deadline-time.monotonic()
            if t.cancelled or r.revision!=t.revision or remaining<=0:
                return None,{},'RequestInvalidated'
            self.provider.last_usage={}
            try:
                bounded=getattr(self.provider,'decide_with_timeout',None)
                value=bounded(r,memory,remaining) if bounded else self.provider.decide(r,memory)
                decision=PlannerDecision.model_validate(value)
                return decision,copy.deepcopy(getattr(self.provider,'last_usage',{})),None
            except Exception as exc:
                return None,copy.deepcopy(getattr(self.provider,'last_usage',{})),type(exc).__name__
        try:
            decision,usage,error_type=await asyncio.to_thread(plan_once)
            attempt['provider_usage']=usage
            attempt['completion']='failed' if error_type else 'completed'
            if error_type: attempt['error_type']=error_type
            if attempt['status']!='timeout': attempt['status']=attempt['completion']
            return decision,error_type
        finally:
            self.provider_lock.release()

    def _stamp(self,answer,r,t):
        answer.request_id=r.request_id; answer.session_id=r.session_id; answer.revision=r.revision
        answer.mode=self.provider.mode
        answer.usage={'model_calls':t.calls,'sql_selects':t.sql,'clarification_rounds':t.clarifications,'active_seconds':round(t.active,4),'wall_seconds':round(time.monotonic()-t.started,4),'attempts':copy.deepcopy(t.attempts),'errors':copy.deepcopy(t.errors)}
        return answer
