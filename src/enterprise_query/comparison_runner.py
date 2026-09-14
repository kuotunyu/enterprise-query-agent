"""Immutable public-development comparison recorder. No gold or holdout."""
import argparse
import asyncio
from datetime import date,datetime,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4
from .comparison import MockMethod,OpenAIMethod,MODEL_PROFILE,LIMITS,prompt_for,COMMON_CONTEXT
from .contracts import QuestionRequest,AnswerEnvelope
from .planner import DEVELOPMENT_QUESTIONS
from .service import Service
from .executor import Executor

ROOT=Path(__file__).resolve().parents[2]

def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def source_hashes():
    paths=[]
    for folder in ('src','scripts','catalog','db'):
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.yaml','.sql','.js','.css','.html'})
    paths.extend(ROOT/name for name in ('pyproject.toml','uv.lock') if (ROOT/name).exists())
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def clarification_script(question):
    # Predeclared public-development user requirements, never answer facts.
    if question=='2018年7月營收多少': return [{'topic':'revenue','reply':'含運 GMV'}]
    if question=='GMV多少': return [{'topic':'period','reply':'2018年7月'}]
    return []

def scripted_reply(script,answer,index):
    if index>=len(script): return None
    item=script[index]
    text=answer.message+' '+' '.join(answer.options)
    relevant=(any(word in text for word in ('口徑','含運','商品金額','付款')) if item['topic']=='revenue' else any(word in text for word in ('期間','年月','月份','2018年')))
    return item['reply'] if relevant else None

class RunRecorder:
    def __init__(self,directory,manifest):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=False)
        self.manifest={**manifest,'manifest_sha256':digest(manifest)}
        with (self.directory/'manifest.json').open('x',encoding='utf-8') as stream:
            stream.write(canonical(self.manifest)+'\n');stream.flush();os.fsync(stream.fileno())
        self.events=self.directory/'events.jsonl'
        self.events.touch(exist_ok=False)
        self.sequence=0;self.last_hash=None

    def append(self,event):
        self.sequence+=1
        row={**event,'sequence':self.sequence,'recorded_at':datetime.now(timezone.utc).isoformat(),'previous_event_sha256':self.last_hash}
        self.last_hash=digest(row);row['event_sha256']=self.last_hash
        with self.events.open('a',encoding='utf-8') as stream:
            stream.write(canonical(row)+'\n');stream.flush();os.fsync(stream.fileno())
        return row

def manifest_for(identity,dataset_manifest,questions,as_of):
    source=source_hashes()
    try: commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    except (OSError,subprocess.CalledProcessError): commit=None
    corpus=[{'id':f'DEV-{i+1:02d}','question':q,'clarification_script':clarification_script(q)} for i,q in enumerate(questions)]
    return {
      'format_version':'development-comparison-v1','mode':'deterministic_mock','scope':'public development only; no holdout, no model quality score',
      'created_at':datetime.now(timezone.utc).isoformat(),'code_commit':commit,'source_files_sha256':source,'source_tree_sha256':digest(source),
      'prompts':{m:prompt_for(m) for m in 'ABC'},'prompt_sha256':{m:digest(prompt_for(m)) for m in 'ABC'},
      'common_context_sha256':digest(COMMON_CONTEXT),'catalog_sha256':digest(COMMON_CONTEXT['catalog']),
      'dataset_identity':identity,'dataset_identity_sha256':digest(identity),'dataset_manifest_sha256':hashlib.sha256(dataset_manifest.read_bytes()).hexdigest() if dataset_manifest else None,
      'model_profile':MODEL_PROFILE,'parameters_sha256':digest(MODEL_PROFILE),'actual_api_calls':0,'limits':LIMITS,
      'as_of_date':str(as_of),'corpus':corpus,'corpus_sha256':digest(corpus),'task_count':len(corpus)*3,
      'method_order':'case index 0: A B C; 1: B C A; 2: C A B; repeating, sequential, one attempt per case/method',
      'cache_policy':'no explicit DB/cache flush; sequential rotating methods; no cross-task response cache; cache cold/warm state not controlled',
      'failure_policy':'all starts and terminal outcomes append-only; no score-based rerun, replacement or automatic resume; interruption may leave unmatched started record',
      'support':{'A':'9 fixed templates, at most one dimension, optional one state equality, no ranking/month grouping','B_mock':'independent finite direct SQL fixtures, adds GMV rank; not real model capability','C':'existing semantic planner compiler'},
      'python_version':sys.version.split()[0],
    }

def cost_summary(ledger, request_ids):
    if ledger is None:
        return {'actual_api_calls':0,'api_cost_usd':'0','cost_basis':'deterministic mock, no API request'}
    records=ledger.records(request_ids)
    return {'actual_api_calls':None,'api_attempts':len(records),'api_cost_usd':None,
        'charged_upper_usd':str(sum((Decimal(c['charged_upper_usd']) for c in records.values()),Decimal(0))),
        'ledger_records':records,'cost_basis':'persistent reservations or usage-based upper bounds; not exact invoice cost'}


async def run_development(directory,executor_factory=Executor,questions=None,as_of=date(2026,9,14),dataset_manifest=None,provider_factory=MockMethod,paid_ledger=None):
    """Run only known public development input. Injection points support offline tests."""
    questions=list(DEVELOPMENT_QUESTIONS if questions is None else questions)
    providers={m:provider_factory(m) for m in 'ABC'}
    if paid_ledger is not None:
        if paid_ledger.stage!='development': raise ValueError('Paid public development requires development budget')
        if any(not isinstance(p,OpenAIMethod) or p.ledger is not paid_ledger for p in providers.values()):
            raise ValueError('Paid methods must share the same development ledger')
    elif any(provider.mode!='mock' for provider in providers.values()):
        raise ValueError('development runner is offline mock only; API providers are forbidden')
    identity=await asyncio.to_thread(executor_factory().check_identity)
    if dataset_manifest is not None:
        recorded=json.loads(dataset_manifest.read_text(encoding='utf-8'))
        if any(recorded.get(key)!=identity.get(key) for key in ('project_id','dataset_id','schema_version','etl_version')):
            raise ValueError('dataset manifest does not match runtime identity')
    manifest=manifest_for(identity,dataset_manifest,questions,as_of)
    if paid_ledger is not None:
        manifest.update(mode='paid_development',actual_api_calls=None,
            scope='public development; no holdout or quality score',
            support={'A':'9 fixed SQL templates and typed slots','B':'model-produced SQL and result metadata under shared SQL guard','C':'model-produced QueryPlan and compiler'},
            budget={'stage':'development','stage_upper_usd_before':str(paid_ledger.total()),
                'approved_limit_usd':paid_ledger._read()['limits']['development'],
                'ledger_sha256_before':hashlib.sha256(paid_ledger.path.read_bytes()).hexdigest()},
            failure_policy=manifest['failure_policy']+'; transport/budget errors and timeouts stop the run; invalid model output remains a failed terminal and does not stop remaining tasks')
    recorder=RunRecorder(directory,manifest)
    if paid_ledger is not None:
        traces=Path(directory)/'provider-traces';traces.mkdir()
        def trace(request_id, data):
            with (traces/(request_id+'.json')).open('x',encoding='utf-8') as stream:
                stream.write(canonical(data));stream.flush();os.fsync(stream.fileno())
        for provider in providers.values(): provider.trace=trace
    services={m:Service(executor_factory,providers[m]) for m in 'ABC'}
    counts={};terminal_count=0;all_requests=set();stop=False
    for index,question in enumerate(questions):
        order='ABC'[index%3:]+'ABC'[:index%3]
        for method in order:
            task_id=str(uuid4());started=time.monotonic();turns=[];task_requests=set()
            recorder.append({'event':'started','case_id':f'DEV-{index+1:02d}','method':method,'task_id':task_id,'question':question})
            request=QuestionRequest(question=question,session_id=task_id,request_id=str(uuid4()),as_of_date=as_of)
            script=clarification_script(question);script_index=0
            try:
                while True:
                    task_requests.add(request.request_id);all_requests.add(request.request_id)
                    if paid_ledger is not None:
                        recorder.append({'event':'turn_started','method':method,'task_id':task_id,'request':request.model_dump(mode='json')})
                    answer=await services[method].ask(request)
                    turns.append({'request':request.model_dump(mode='json'),'answer':answer.model_dump(mode='json')})
                    if answer.status!='needs_clarification': break
                    reply=scripted_reply(script,answer,script_index)
                    if reply is None:
                        answer=answer.model_copy(update={'status':'rejected','message':'開發脚本沒有此澄清的相關回覆；未自動提供額外需求。'})
                        break
                    script_index+=1
                    request=request.model_copy(update={'request_id':str(uuid4()),'revision':request.revision+1,'clarification':reply})
            except Exception as exc:
                answer=AnswerEnvelope(status='execution_error',message='runner exception: '+type(exc).__name__,request_id=request.request_id,session_id=task_id,revision=request.revision,usage={'error_type':type(exc).__name__})
            terminal_count+=1;counts[method+':'+answer.status]=counts.get(method+':'+answer.status,0)+1
            recorder.append({'event':'terminal','case_id':f'DEV-{index+1:02d}','method':method,'task_id':task_id,'outcome':answer.model_dump(mode='json'),'turns':turns,'wall_seconds':time.monotonic()-started,**cost_summary(paid_ledger,task_requests)})
            attempts=answer.usage.get('attempts',[])
            model_failure=bool(attempts) and attempts[-1].get('error_type') in {
                'ValidationError','JSONDecodeError','ValueError','LengthFinishReasonError','ContentFilterFinishReasonError'
            } and attempts[-1].get('provider_usage',{}).get('tokens') is not None
            if paid_ledger is not None and (answer.status in ('timeout','execution_error') or
                    (answer.status=='provider_error' and not model_failure)):
                stop=True;break
        if stop: break
    # Account for late provider completions without replacing terminal outcomes.
    pending=[job for service in services.values() for job in service.provider_jobs]
    if pending: await asyncio.gather(*pending,return_exceptions=True)
    summary={'event':'run_stopped' if stop else 'run_completed','terminal_count':terminal_count,
        'unstarted_count':len(questions)*3-terminal_count,'status_counts':counts,**cost_summary(paid_ledger,all_requests),
        'quality_score':None,'qualification':'development statuses only; no gold loaded'}
    recorder.append(summary)
    return summary

def main():
    parser=argparse.ArgumentParser(description='A/B/C public development comparison; mock unless --paid is explicit')
    parser.add_argument('--paid',action='store_true',help='Use approved development budget and local key; never formal research')
    parser.add_argument('--run-id',default=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6])
    parser.add_argument('--as-of-date',type=date.fromisoformat,default=date(2026,9,14))
    parser.add_argument('--dataset-manifest',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.run_id): parser.error('run-id must be a simple name')
    if not args.dataset_manifest.is_file(): parser.error('dataset manifest must exist')
    if os.getenv('EQA_BOOTSTRAP_PASSWORD'): parser.error('bootstrap credentials forbidden')
    output=ROOT/'.local'/'comparison-runs'/args.run_id
    kwargs={}
    if args.paid:
        from dotenv import dotenv_values
        from openai import OpenAI
        from .costs import CostLedger
        if os.getenv('EQA_ENABLE_PAID_API')!='1': parser.error('--paid also requires EQA_ENABLE_PAID_API=1')
        key=os.getenv('OPENAI_API_KEY') or dotenv_values(ROOT/'.local'/'openai.env').get('OPENAI_API_KEY')
        if not key: parser.error('Set the API key in the local-only credential file')
        ledger=CostLedger(ROOT/'.local'/'cost-ledger.json','development')
        client=OpenAI(api_key=key,base_url='https://api.openai.com/v1/',max_retries=0,timeout=55)
        kwargs={'paid_ledger':ledger,'provider_factory':lambda method:OpenAIMethod(method,client,ledger)}
    result=asyncio.run(run_development(output,as_of=args.as_of_date,dataset_manifest=args.dataset_manifest,**kwargs))
    print(canonical({'run_id':args.run_id,**result}))

if __name__=='__main__': main()
