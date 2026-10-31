"""ProofStack candidate-first sessions. Bearer links grant access to one session only."""
import ast
import copy
import hashlib
import json
import secrets
import time
from threading import BoundedSemaphore, Lock
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from signalloop_api.database import get_session, SessionLocal
from signalloop_api.models import PortfolioSession
from signalloop_api.portfolio_tasks import TOPICS, TIERS, template_task, personalize_task, validate_task, evaluate
from signalloop_api.ai_provider import get_ai_provider
from signalloop_api.ai_policy import fallback_classify, AIDecision

router=APIRouter(prefix='/portfolio',tags=['ProofStack'])

class Project(BaseModel):
    name: str=Field(min_length=1,max_length=120)
    description: str=Field(default='',max_length=2000)
    tech_stack: list[str]=Field(default_factory=lambda:['Python'],max_length=12)

class Portfolio(BaseModel):
    name: str=Field(min_length=1,max_length=100)
    domain: Literal['Backend','Web','ML','Security']='Backend'
    projects: list[Project]=Field(min_length=1,max_length=5)
    skills: list[str]=Field(min_length=1,max_length=20)
    dsa_topics: list[str]=Field(min_length=1,max_length=3)
    selected_project: int=Field(default=0,ge=0,le=4)

    @field_validator('dsa_topics')
    @classmethod
    def topics(cls,value):
        value=list(dict.fromkeys(x.strip().lower().replace(' ','_') for x in value))
        if set(value)-set(TOPICS): raise ValueError('Supported topics: arrays, hash_maps, strings')
        return value

class CodePayload(BaseModel):
    files: dict[str,str]
    revision: int=Field(ge=0)

class RunPayload(CodePayload):
    question: Literal["project","dsa"] | None = None

class HintPayload(BaseModel):
    message: str=Field(min_length=1,max_length=2000)

class StartPayload(BaseModel):
    accept_visibility_rule: bool = False


def event(s,kind,detail,tier=None):
    s['events'].append({'at':time.time(),'kind':kind,'detail':detail,'tier':tier})
    s['events']=s['events'][-500:]


def persist(db,row,s):
    row.state=copy.deepcopy(s); db.add(row); db.commit()


def load(db,token):
    row=db.scalar(select(PortfolioSession).where(PortfolioSession.token==token).with_for_update())
    if not row: raise HTTPException(404,'Assessment not found')
    return row,copy.deepcopy(row.state)


def safe_view(row,s):
    tasks=[]
    for i,t in enumerate(s['tasks']):
        if s.get('status') in ('ready','generating','error'):
            tasks.append({'index':i,'tier':t['tier'],'ready':True})
            continue
        tasks.append({k:v for k,v in t.items() if k not in {'reference','cases','starter_checks'}} | {'examples':[c for c in t['cases'] if c['public']], 'index':i})
    return {'token':row.token,**{k:v for k,v in s.items() if k not in {'tasks','generation_version','generation_attempts','recovery_count','generation_started'}},'tasks':tasks,'server_time':time.time()}


def ensure_active(s):
    if s['status']!='active': raise HTTPException(409,'Start the assessment first, or view its completed report.')
    if time.time()>=s['deadline']: raise HTTPException(409,'Time is up. Submit the saved assessment to view your report.')


def get_task(s,index):
    if index<0 or index>=len(s['tasks']): raise HTTPException(404,'Task not found')
    return s['tasks'][index]


def apply_code(t,payload):
    if payload.revision!=t['revision']: raise HTTPException(409,'A newer save exists. Reload before editing in another tab.')
    if set(payload.files)!=set(t['starter']) or sum(map(len,payload.files.values()))>100000:
        raise HTTPException(422,'Only the provided Python files may be edited (100KB maximum).')
    t['files']=payload.files; t['revision']+=1


# Bound provider concurrency across all session-generation jobs.
_author_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='portfolio-author')
_author_slots = BoundedSemaphore(2)
GENERATION_VERSION = 2
TASK_FIELDS = ('tier','title','statement','dsa_title','dsa_statement','topic','why',
               'starter','reference','cases','source','validation','fallback_reason','starter_checks','contract_version')


def fresh_task(task):
    # Never copy another session's answers, hints, results, timing or revision.
    task = copy.deepcopy({key: task[key] for key in TASK_FIELDS if key in task})
    task.update(files=copy.deepcopy(task['starter']), revision=0, runs=[], hints=[], chat=[], final=None)
    return task


def reusable_tasks(state, portfolio, now=None):
    now = time.time() if now is None else now
    if (state.get('portfolio') != portfolio or state.get('status') not in ('ready','active','completed')
        or state.get('generation_version', 1) != GENERATION_VERSION
        or not 0 <= now - state.get('created_at', 0) < 86400):
        return None
    tasks = state.get('tasks', [])
    if len(tasks) != 3:
        return None
    for tier, task in enumerate(tasks):
        validation = task.get('validation') or {}
        if (task.get('tier') != TIERS[tier] or not validation.get('released')
            or not validation.get('total') or validation.get('reference_passed') != validation['total']):
            return None
        if not all(key in task for key in TASK_FIELDS if key not in ('fallback_reason',)):
            return None
    return [fresh_task(task) for task in tasks]


def author_task(portfolio, tier, grounded=False):
    with _author_slots:
        return personalize_task(portfolio, tier, grounded=grounded)


def generate(token):
    with SessionLocal() as db:
        row,s=load(db,token)
        if s['status'] != 'generating': return
        portfolio=copy.deepcopy(s['portfolio'])
        selected=portfolio.get('selected_project',0)
        portfolio['projects']=[portfolio['projects'][selected]]
        s['tasks']=[];s['generation_started']=time.time()
        persist(db,row,s)
    # Authoring overlaps; sandbox validation remains sequential on the small runner.
    futures = [_author_pool.submit(author_task, portfolio, tier) for tier in range(3)]
    for tier, future in enumerate(futures):
        task=None; fallback_reason=None
        for attempt in range(2):
            try:
                candidate = future.result() if attempt == 0 else author_task(portfolio,tier,grounded=True)
                task=validate_task(candidate); break
            except Exception:
                continue
        if task is None:
            fallback_reason='AI task did not meet the release checks. A verified template was used.'
            try: task=validate_task(template_task(portfolio,tier))
            except Exception:
                for pending in futures: pending.cancel()
                with SessionLocal() as db:
                    row,s=load(db,token); s['status']='error'; s['error']='Execution validation is unavailable. Please retry shortly.'
                    persist(db,row,s)
                return
        task['fallback_reason']=fallback_reason
        task=fresh_task(task)
        with SessionLocal() as db:
            row,s=load(db,token)
            if s['status']!='generating': return
            s['tasks'].append(task)
            event(s,'validated',f'{TIERS[tier]}: reference passed {task["validation"]["total"]} checks; starter failed both challenges.',tier)
            if tier==2: s['status']='ready'
            persist(db,row,s)

@router.post('/sessions',status_code=202)
def create(payload:Portfolio,background:BackgroundTasks,request:Request,db:Session=Depends(get_session)):
    from signalloop_api.portfolio_limits import intake_limit
    intake_limit(request,'create')
    data=payload.model_dump()
    if data['selected_project'] >= len(data['projects']): raise HTTPException(422,'Choose a project from your portfolio.')
    if len(json.dumps(data))>20000: raise HTTPException(422,'Portfolio must be under 20KB.')
    skills=' '.join(data['skills']+sum([p['tech_stack'] for p in data['projects']],[])).lower()
    if 'python' not in skills: raise HTTPException(422,'This version supports Python portfolios. Include Python in your skills or project tech stack.')
    token=secrets.token_urlsafe(32)
    s={'portfolio':data,'status':'generating','created_at':time.time(),'started_at':None,'deadline':None,'tasks':[],'events':[],'report':None,'error':None,'generation_version':GENERATION_VERSION,'recovery_count':0}
    event(s,'created','Portfolio received; preparing three validated challenges.')
    # Persistent cache survives free-host sleeps. Exact portfolio equality is required.
    for previous in db.scalars(select(PortfolioSession).order_by(PortfolioSession.id.desc()).limit(30)):
        cached = reusable_tasks(previous.state, data)
        if cached:
            s['tasks']=cached; s['status']='ready'
            event(s,'reused','Previously validated questions reused for the identical portfolio; answers and scores reset.')
            break
    row=PortfolioSession(token=token,state=s); db.add(row); db.commit()
    if s['status'] != 'ready':
        if not schedule_generation(token):
            s['status']='error';s['error']='The question queue is full. Retry this assessment shortly.';persist(db,row,s)
    return {'token':token}

@router.get('/sessions/{token}')
def get(token:str,db:Session=Depends(get_session)):
    row,s=load(db,token)
    if s['status']=='generating' and token not in _generation_pending and time.time()-s.get('generation_started',s['created_at'])>600:
        s['status']='error';s['error']='Preparation was interrupted. Retry to continue.';persist(db,row,s)
    return safe_view(row,s)

@router.post('/sessions/{token}/start')
def start(token:str,payload:StartPayload,db:Session=Depends(get_session)):
    row,s=load(db,token)
    if s['status']=='ready':
        if not payload.accept_visibility_rule:
            raise HTTPException(422,'Accept the one-time 40-point page-leaving rule before starting.')
        if s.get('generation_version',1)<GENERATION_VERSION:
            raise HTTPException(409,'These questions use an older version. Create a new assessment for the updated checks.')
        s['status']='active'; s['started_at']=time.time(); s['deadline']=s['started_at']+3600
        s['focus_index']=0; s['focus_since']=s['started_at']
        s['visibility_rule']={'version':1,'penalty':40,'accepted_at':s['started_at']}
        event(s,'started','One shared 60-minute clock started for all three tiers.'); persist(db,row,s)
    elif s['status'] not in {'active','completed'}: raise HTTPException(409,'Challenges are still being validated.')
    return safe_view(row,s)

@router.post('/sessions/{token}/page-hidden')
def page_hidden(token:str,db:Session=Depends(get_session)):
    row,s=load(db,token)
    # Idempotent: browser beacons and retries can report the same event more than once.
    # Old active sessions never accepted this rule and cannot receive a retroactive penalty.
    if (s['status']=='active' and s.get('visibility_rule',{}).get('version')==1
        and time.time()<s['deadline'] and not s.get('page_hidden_at')):
        s['page_hidden_at']=time.time()
        event(s,'page_hidden','Assessment page became hidden. One-time 40-point penalty recorded.')
        persist(db,row,s)
    return {'recorded':bool(s.get('page_hidden_at')),'penalty':40 if s.get('page_hidden_at') else 0}

@router.put('/sessions/{token}/tasks/{index}/code')
def save(token:str,index:int,payload:CodePayload,db:Session=Depends(get_session)):
    row,s=load(db,token); ensure_active(s); t=get_task(s,index); apply_code(t,payload)
    persist(db,row,s)
    return {'revision':t['revision']}

@router.post('/sessions/{token}/tasks/{index}/run')
def run(token:str,index:int,payload:RunPayload,db:Session=Depends(get_session)):
    row,s=load(db,token); ensure_active(s); t=get_task(s,index); apply_code(t,payload)
    if len(t['runs'])>=50: raise HTTPException(429,'This level has reached its 50 test-run limit. You can still save and submit.')
    all_public=[c for c in t['cases'] if c['public']]
    public=[c for c in all_public if payload.question is None or (c['module'].endswith('dsa')) == (payload.question=='dsa')]
    try: result=evaluate(t['files'],public)
    except Exception: raise HTTPException(503,'The execution worker is unavailable. Your previous saved code is safe.')
    result['question']=payload.question
    result['tested_files']=dict(t['files'])
    result['question_passed']=result['passed']; result['question_total']=result['total']
    if payload.question:
        # Preserve the full-tier denominator for the existing efficiency formula.
        other=[c for c in all_public if c not in public]
        previous=next((r for r in reversed(t['runs']) if r.get('question')!=payload.question and r.get('tested_files') and all(r['tested_files'].get(f)==v for f,v in t['files'].items() if (f.endswith('/dsa.py')) != (payload.question=='dsa'))),None)
        result['passed']+=sum(c['passed'] for c in (previous or {}).get('checks',[]) if c['name'] in {x['name'] for x in other})
        result['total']=len(all_public)
    result['at']=time.time(); result['snapshot']=hashlib.sha256(json.dumps(t['files'],sort_keys=True).encode()).hexdigest()
    t['runs'].append(result); t['runs']=t['runs'][-100:]
    event(s,'test',f'{payload.question or "Tier"}: {result["question_passed"]}/{result["question_total"]} public checks passed.',index)
    persist(db,row,s)
    return {'revision':t['revision'],'result':result,'next_step':{'tier':min(index+1,2) if result['question_passed']==result['question_total'] and payload.question=='dsa' else index,'question':'dsa' if payload.question=='project' and result['question_passed']==result['question_total'] else payload.question or 'project','message':'Examples passed. Try the next challenge.' if result['question_passed']==result['question_total'] else 'Stay with this question: inspect the first failing example.'}}

@router.post('/sessions/{token}/tasks/{index}/hint')
def hint(token:str,index:int,payload:HintPayload,db:Session=Depends(get_session)):
    row,s=load(db,token); ensure_active(s); t=get_task(s,index)
    if index==0: raise HTTPException(403,'Novice is an independent assessment: AI is disabled.')
    if len(t['hints'])>=12: raise HTTPException(429,'Hint budget reached. Use the existing guidance and run tests.')
    if t['hints'] and time.time()-t['hints'][-1]['at']<3: raise HTTPException(429,'Wait a moment before requesting another hint.')
    recent=[m['message'] for m in t['chat'] if m['role']=='user'][-6:]
    cumulative=fallback_classify(payload.message,recent)
    if 'anti_decomposition' in cumulative.tags:
        decision=AIDecision(allowed=False,policy_tags=['anti_decomposition'],message='I can help you reason about one observed failure, but cannot assemble a complete solution across multiple requests.')
    else:
        visible_files={path:source[:5000] for path,source in t['files'].items()}
        visible_files['CHALLENGE.md']=t['statement']+'\nDSA: '+t['dsa_statement']
        decision=get_ai_provider().evaluate(payload.message,{'path':'CHALLENGE.md'},recent,
                recent_turns=[('candidate' if m['role']=='user' else 'assistant',m['message']) for m in t['chat'][-8:]],workspace_files=visible_files)
    message=decision.message
    if 'could not generate' in message.lower(): raise HTTPException(503,'Featherless is temporarily unavailable. Please retry; this request was not counted.')
    if '```' in message: message='Focus on one failing example. Trace the input through your code, compare it with the stated contract, and check where the behavior first differs. Code solutions are withheld in this assessment.'
    baseline=max([r['passed']/r['total'] for r in t['runs']],default=0)
    t['hints'].append({'at':time.time(),'baseline':baseline,'allowed':decision.allowed})
    t['chat'] += [{'role':'user','message':payload.message},{'role':'assistant','message':message}]
    event(s,'hint','Guarded hint provided.' if decision.allowed else 'Solution request declined.',index)
    persist(db,row,s)
    return {'message':message,'allowed':decision.allowed}


def code_structure(source):
    try:
        tree=ast.parse(source)
        # Comments, whitespace and docstrings are not an attempted implementation.
        for node in ast.walk(tree):
            if isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                node.body=[n for n in node.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str))]
        return ast.dump(tree,include_attributes=False)
    except SyntaxError:
        return source.strip()


def question_attempted(t,question):
    return any(code_structure(t['files'].get(f,''))!=code_structure(starter)
               for f,starter in t['starter'].items()
               if not f.endswith('__init__.py') and (f.endswith('/dsa.py'))==(question=='dsa'))


def build_report(s,stored_results=False):
    results=[]; weights=[0.2,0.35,0.45]
    for index,t in enumerate(s['tasks']):
        result=copy.deepcopy(t['final']) if stored_results else evaluate(t['files'],t['cases'])
        baseline=t.get('starter_checks')
        if baseline is None:
            if stored_results: raise ValueError('Starter baseline required to recalculate legacy report')
            baseline=[c['passed'] for c in evaluate(t['starter'],t['cases'])['checks']]
            t['starter_checks']=baseline
        attempted={q:question_attempted(t,q) for q in ('project','dsa')}
        question_results={q:{'passed':0,'total':0} for q in ('project','dsa')}
        for case,check,starter_passed in zip(t['cases'],result['checks'],baseline):
            question='dsa' if case['module'].endswith('dsa') else 'project'
            check.setdefault('test_passed',check['passed'])
            check['passed']=check['test_passed']
            check['scored']=not starter_passed
            if starter_passed:
                check['passed']=False;check['error']='Already passed by starting code — not scored'
            else:
                question_results[question]['total']+=1
            if not attempted[question]:
                check['passed']=False
                check['error']='Not attempted — starter code earns no credit'
            if check['passed']: question_results[question]['passed']+=1
        result['passed']=sum(c['passed'] for c in result['checks'])
        result['total']=sum(c['scored'] for c in result['checks'])
        if result['status'] in {'error','output_limit'}: # Code errors still grade zero, infrastructure errors are handled separately.
            result['passed']=0
        t['final']=result
        ratio=result['passed']/result['total'] if result['total'] else 0
        unique=len({r['snapshot'] for r in t['runs'] if r.get('question_passed',r['passed'])>0})
        hints=t['hints']; improvements=[]
        for h_index,h in enumerate(hints):
            until=hints[h_index+1]['at'] if h_index+1<len(hints) else float('inf')
            after=[r['passed']/r['total'] for r in t['runs'] if h['at']<r['at']<=until]
            improvements.append(max(0,max(after,default=h['baseline'])-h['baseline']))
        # At most 20 final points; no-hint candidates can earn full credit through verified correctness.
        efficiency=ratio if not hints else min(1,sum(improvements)/len(hints)*2+min(unique,3)/3*0.25)*ratio
        results.append({'tier':t['tier'],'title':t['title'],'passed':result['passed'],'total':result['total'],
                        'attempted':attempted,'question_results':question_results,'correctness':round(ratio*100,1),'ai_efficiency':round(efficiency*100,1) if index else None,
                        'hints':len(hints),'distinct_tested_versions':unique,'runs':len(t['runs']),
                        'execution_ms':result['duration_ms'],'elapsed_seconds':round(t.get('elapsed_seconds',0)),'checks':result['checks'],'weight':weights[index]})
    code=sum(r['correctness']*r['weight'] for r in results)*0.8
    ai=sum(r['ai_efficiency'] for r in results[1:])/2*0.2
    penalty=40 if s.get('visibility_rule',{}).get('version')==1 and s.get('page_hidden_at') else 0
    score=round(max(0,code+ai-penalty),1)
    from signalloop_api.portfolio_evidence import claim_evidence, solution_replay
    return {'scoring_version':4,'claims':claim_evidence(s,results),'replay':solution_replay(s),'score':score,'score_before_penalty':round(code+ai,1),'page_leaving_penalty':penalty,'page_hidden_at':s.get('page_hidden_at'),'coding_points':round(code,1),'ai_points':round(ai,1),
            'elapsed_seconds':round(min(time.time(),s['deadline'])-s['started_at']), 'tasks':results,
            'outcome':'Demonstrated' if score>=70 else 'Developing' if score>=40 else 'Needs practice',
            'formula':'If the page-leaving rule was accepted at Start, a browser-reported hidden page deducts 40 points once, after the coding and hint-use scores, with a minimum final score of zero. Only tests the starting code failed count toward your score. Passing those tests earns credit; existing starter passes earn none. A changed question with no improved test result earns zero. 80% deterministic test correctness (Novice 20%, Adept 35%, Master 45%) + 20% AI efficiency averaged across Adept and Master. With no hints, efficiency equals correctness. With hints: correctness × min(1, 2 × average public-test progress per hint + 0.25 × min(distinct tested versions, 3)/3).',
            'limitations':['Tests measure the stated contracts, not complete mastery or authorship.','Performance uses bounded workloads and a wall-clock timeout, not a formal complexity proof.',('Checks are independently specified for supported contracts, but cannot prove complete mastery.' if s.get('generation_version',1)>=2 else 'This older assessment used AI-authored or template tests; independent-check updates apply to new assessments.'),'Page visibility is browser-reported and can miss events or be bypassed. It does not identify other apps, devices or outside AI use, and does not prove cheating or authorship.']}

def record_focus(s,index=None):
    now=min(time.time(),s['deadline'])
    previous=s.get('focus_index',0)
    t=s['tasks'][previous]
    t['elapsed_seconds']=t.get('elapsed_seconds',0)+max(0,now-s.get('focus_since',s['started_at']))
    s['focus_since']=now
    if index is not None: s['focus_index']=index

@router.post('/sessions/{token}/focus/{index}')
def focus(token:str,index:int,db:Session=Depends(get_session)):
    row,s=load(db,token);ensure_active(s);get_task(s,index)
    record_focus(s,index);persist(db,row,s)
    return {'ok':True}

@router.post('/sessions/{token}/finish')
def finish(token:str,db:Session=Depends(get_session)):
    row,s=load(db,token)
    if s['status']=='completed': return safe_view(row,s)
    if s['status']!='active': raise HTTPException(409,'Start the assessment first.')
    record_focus(s)
    s['finished_at']=time.time()
    try: report=build_report(s)
    except Exception: raise HTTPException(503,'Final evaluation is temporarily unavailable. Saved code is retained; retry submission.')
    s['report']=report;s['status']='completed'
    event(s,'submitted','Saved code evaluated against public and hidden checks. Assessment locked.')
    persist(db,row,s)
    return safe_view(row,s)


_pdf_slot = BoundedSemaphore(1)

@router.post('/import-pdf')
async def import_pdf(request: Request):
    from starlette.concurrency import run_in_threadpool
    from signalloop_api.portfolio_pdf import MAX_BYTES, extract_pdf, draft_from_text
    from signalloop_api.portfolio_limits import intake_limit
    intake_limit(request,'pdf')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, 'Choose a PDF smaller than 5 MB.')
    if not _pdf_slot.acquire(blocking=False):
        raise HTTPException(429, 'Another PDF is being read. Please try again in a moment.')
    try:
        text = await run_in_threadpool(extract_pdf, bytes(data))
        draft = await run_in_threadpool(draft_from_text, text)
        return {'portfolio': draft, 'extracted_text':text}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        _pdf_slot.release()


# One durable database row per job; bounded local execution and startup recovery.
_generation_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='portfolio-job')
_generation_lock=Lock()
_generation_pending=set()

def schedule_generation(token):
    with _generation_lock:
        if token in _generation_pending:return True
        if len(_generation_pending)>=6:return False
        _generation_pending.add(token)
    def job():
        try:generate(token)
        except Exception:
            with SessionLocal() as db:
                row,s=load(db,token)
                if s['status']=='generating':
                    s['status']='error';s['error']='Preparation was interrupted. Retry this assessment.';persist(db,row,s)
        finally:
            with _generation_lock:_generation_pending.discard(token)
    _generation_pool.submit(job)
    return True


def recover_generation():
    with SessionLocal() as db:
        rows=list(db.scalars(select(PortfolioSession).where(PortfolioSession.state['status'].as_string()=='generating')))
        for row in rows:
            s=copy.deepcopy(row.state)
            if s.get('recovery_count',0)>=2:
                s['status']='error';s['error']='Preparation was interrupted. Please retry.'
            else:
                s['recovery_count']=s.get('recovery_count',0)+1
                event(s,'recovered','Preparation resumed after a server restart.')
            persist(db,row,s)
            if s['status']=='generating' and not schedule_generation(row.token):
                s['status']='error';s['error']='Preparation queue is full. Retry shortly.';persist(db,row,s)


@router.post('/sessions/{token}/retry')
def retry_generation(token:str,request:Request,db:Session=Depends(get_session)):
    from signalloop_api.portfolio_limits import intake_limit
    intake_limit(request,'create')
    row,s=load(db,token)
    if s['status']!='error':raise HTTPException(409,'This assessment does not need a retry.')
    s.update(status='generating',error=None,tasks=[],generation_started=time.time(),generation_version=GENERATION_VERSION)
    persist(db,row,s)
    if not schedule_generation(token):
        s.update(status='error',error='Queue full. Retry shortly.');persist(db,row,s)
        raise HTTPException(429,'Queue full. Retry shortly.')
    return safe_view(row,s)
