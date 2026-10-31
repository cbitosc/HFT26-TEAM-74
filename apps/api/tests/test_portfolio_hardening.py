import copy,json,time
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from signalloop_api import portfolio, portfolio_limits, portfolio_tasks
from signalloop_api.portfolio_evidence import claim_evidence, solution_replay
from signalloop_api.portfolio_checks import add_project_checks

P={'name':'Alex','domain':'Backend','projects':[{'name':'Campus','tech_stack':['Python']}],'skills':['Python','FastAPI','Authentication'],'dsa_topics':['arrays']}


def test_no_question_content_before_clock():
    class Row: token='test'
    task=portfolio_tasks.template_task(P,0)
    for status in ('generating','ready','error'):
        out=portfolio.safe_view(Row(),{'status':status,'tasks':[task]})
        assert set(out['tasks'][0])=={'index','tier','ready'}
        assert 'statement' not in json.dumps(out)
        assert 'def solve' not in json.dumps(out)


@pytest.mark.parametrize('edit',['noop','correct'])
def test_baseline_passes_never_earn_credit(monkeypatch,edit):
    tasks=[]
    for tier in range(3):
        task=portfolio_tasks.template_task(P,tier)
        task['starter_checks']=[i%2==0 for i in range(len(task['cases']))]
        task.update(files=copy.deepcopy(task['reference'] if edit=='correct' else task['starter']),runs=[],hints=[])
        if edit=='noop':
            for f in task['files']: task['files'][f]+='\npass\n'
        tasks.append(task)
    def evaluate(files,cases):
        passed=[edit=='correct' or i%2==0 for i in range(len(cases))]
        return {'status':'completed','passed':sum(passed),'total':len(cases),'duration_ms':1,'checks':[{'name':c['name'],'passed':p} for c,p in zip(cases,passed)]}
    monkeypatch.setattr(portfolio,'evaluate',evaluate)
    state={'portfolio':P,'tasks':tasks,'started_at':time.time()-10,'deadline':time.time()+100}
    report=portfolio.build_report(state)
    assert report['score']==(100 if edit=='correct' else 0)
    assert all(c['passed']==False for t in report['tasks'] for c in t['checks'] if not c['scored'])
    assert portfolio.build_report(state,stored_results=True)['score']==report['score']


def test_claims_do_not_invent_skill_coverage():
    task=portfolio_tasks.template_task(P,0)
    results=[{'attempted':{'project':True,'dsa':True},'question_results':{'project':{'passed':5,'total':5},'dsa':{'passed':3,'total':3}}}]
    rows=claim_evidence({'portfolio':P,'tasks':[task]},results)
    by_claim={r['claim']:r for r in rows}
    assert by_claim['arrays']['result']=='Demonstrated'
    assert by_claim['Python']['result']=='Demonstrated'
    assert by_claim['FastAPI']['result']==by_claim['Authentication']['result']=='Not tested'


def test_replay_uses_candidate_snapshots_only():
    task=portfolio_tasks.template_task(P,0)
    task.update(files={'task_api/main.py':'final'},runs=[{'at':2,'passed':1,'total':2,'tested_files':{'task_api/main.py':'candidate'}}],final={})
    entries=solution_replay({'tasks':[task],'started_at':1,'finished_at':4,'status':'completed','events':[{'at':3,'kind':'hint','detail':'Hint given','tier':0}]})
    assert [e['kind'] for e in entries]==['start','test','hint','submit']
    assert '+candidate' in entries[1]['changes']['task_api/main.py']
    assert 'reference' not in json.dumps(entries)
    assert entries[-1]['files']['task_api/main.py']=='final'


def test_usage_is_persistent_and_cannot_overrun(monkeypatch,session_factory):
    monkeypatch.setattr(portfolio_limits,'SessionLocal',session_factory)
    portfolio_limits.reserve('test',2,3)
    portfolio_limits.reserve('test',1,3)
    with pytest.raises(HTTPException) as e: portfolio_limits.reserve('test',1,3)
    assert e.value.status_code==429
    with pytest.raises(HTTPException): portfolio_limits.reserve('other',4,3)


def test_recovery_and_retry_limit(monkeypatch,session_factory):
    from signalloop_api.models import PortfolioSession
    monkeypatch.setattr(portfolio,'SessionLocal',session_factory)
    scheduled=[];monkeypatch.setattr(portfolio,'schedule_generation',lambda token: scheduled.append(token) or True)
    with session_factory() as db:
        for token,count in [('recover',0),('stop',2)]:
            db.add(PortfolioSession(token=token,state={'status':'generating','recovery_count':count,'events':[]}))
        db.commit()
    portfolio.recover_generation()
    assert scheduled==['recover']
    with session_factory() as db:
        row,s=portfolio.load(db,'stop');assert s['status']=='error'


def test_independent_checks_ignore_authored_cases():
    task=portfolio_tasks.template_task(P,2)
    before=len(task['cases']);add_project_checks(task)
    assert len(task['cases'])==before+12
    for c in task['cases'][before:]:
        events={}
        for e in c['input']['events']:events.setdefault(e['id'],e)
        expected={}
        for e in events.values():expected[e['type']]=expected.get(e['type'],0)+e.get('value',0)
        assert c['expected']=={k:v for k,v in expected.items() if v}
        assert not c['public']


def test_ai_cannot_replace_contract_or_tests(monkeypatch):
    import httpx
    monkeypatch.setattr(portfolio_limits,'reserve_ai',lambda *a:None)
    monkeypatch.setattr(portfolio_tasks.settings,'featherless_api_key','test')
    base=portfolio_tasks.template_task(P,0)
    output={'title':'Campus filter','why':'Practice filtering','statement':'Return anything','cases':[],
            'starter':{'main.py':base['starter']['task_api/main.py']},'reference':{'main.py':base['reference']['task_api/main.py']}}
    monkeypatch.setattr(portfolio_tasks.httpx,'post',lambda *a,**kw:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]},request=httpx.Request('POST','https://test.invalid')))
    result=portfolio_tasks.personalize_task(P,0)
    assert result['statement']==base['statement'] and result['cases']==base['cases']


def test_page_leaving_is_once_and_only_for_accepted_active_sessions(monkeypatch):
    state={'status':'active','deadline':time.time()+100,'events':[],'visibility_rule':{'version':1}}
    monkeypatch.setattr(portfolio,'load',lambda *args:(object(),state))
    monkeypatch.setattr(portfolio,'persist',lambda *args:None)
    assert portfolio.page_hidden('test',None)['penalty']==40
    assert portfolio.page_hidden('test',None)['penalty']==40
    assert len(state['events'])==1
    for status,rule,deadline in [('ready',{'version':1},time.time()+100),('completed',{'version':1},time.time()+100),('active',{},time.time()+100),('active',{'version':1},time.time()-1)]:
        state.clear();state.update(status=status,visibility_rule=rule,deadline=deadline,events=[])
        assert portfolio.page_hidden('test',None)['penalty']==0
        assert state['events']==[]


def test_start_requires_explicit_rule_acceptance(monkeypatch):
    state={'status':'ready','generation_version':2,'tasks':[],'events':[]}
    class Row:token='test'
    monkeypatch.setattr(portfolio,'load',lambda *args:(Row(),state))
    monkeypatch.setattr(portfolio,'persist',lambda *args:None)
    with pytest.raises(HTTPException) as error:
        portfolio.start('test',portfolio.StartPayload(),None)
    assert error.value.status_code==422 and state['status']=='ready'
    portfolio.start('test',portfolio.StartPayload(accept_visibility_rule=True),None)
    assert state['visibility_rule']['penalty']==40 and state['status']=='active'


@pytest.mark.parametrize('correct,expected',[(True,60),(False,0)])
def test_penalty_is_separate_clamped_and_updates_outcome(monkeypatch,correct,expected):
    tasks=[]
    for tier in range(3):
        task=portfolio_tasks.template_task(P,tier)
        task.update(starter_checks=[False]*len(task['cases']),files=task['reference'] if correct else task['starter'],runs=[],hints=[])
        tasks.append(task)
    monkeypatch.setattr(portfolio,'evaluate',lambda files,cases:{'status':'completed','passed':len(cases),'total':len(cases),'duration_ms':1,'checks':[{'name':c['name'],'passed':True} for c in cases]})
    state={'tasks':tasks,'started_at':time.time()-10,'deadline':time.time()+100,'visibility_rule':{'version':1},'page_hidden_at':time.time()}
    report=portfolio.build_report(state)
    assert report['score']==expected and report['page_leaving_penalty']==40
    assert report['score_before_penalty']==(100 if correct else 0)
    assert report['outcome']==('Developing' if correct else 'Needs practice')
