import copy
import time
import pytest
from fastapi import HTTPException
from signalloop_api.portfolio import Portfolio, CodePayload, apply_code, ensure_active, safe_view, build_report
from signalloop_api.portfolio_tasks import template_task as base_template_task

def template_task(p,tier):
    task=base_template_task(p,tier)
    task["starter_checks"]=[False]*len(task["cases"])
    return task

P={'name':'Alex','domain':'Backend','projects':[{'name':'Campus','tech_stack':['Python']}],'skills':['Python'],'dsa_topics':['arrays']}

def test_topics_are_bounded():
    with pytest.raises(ValueError): Portfolio(**(P|{'dsa_topics':['graphs']}))
    assert Portfolio(**(P|{'dsa_topics':['Hash Maps']})).dsa_topics==['hash_maps']

def test_deadline_and_status_enforced():
    for s in [{'status':'ready'},{'status':'completed'},{'status':'active','deadline':time.time()-1}]:
        with pytest.raises(HTTPException): ensure_active(s)

def test_stale_revision_and_unapproved_file_rejected():
    t={'starter':{'task_api/main.py':''},'revision':2}
    with pytest.raises(HTTPException): apply_code(t,CodePayload(files=t['starter'],revision=1))
    with pytest.raises(HTTPException): apply_code(t,CodePayload(files={'runner.py':''},revision=2))

def test_no_hidden_expected_or_reference_in_candidate_view():
    class Row: token='test'
    t=template_task(P,0)
    out=safe_view(Row(),{'tasks':[t]})
    assert 'reference' not in out['tasks'][0]
    assert 'cases' not in out['tasks'][0]
    assert all(c['public'] for c in out['tasks'][0]['examples'])

def test_score_cannot_exceed_100_and_repeated_runs_do_not_inflate(monkeypatch):
    tasks=[]
    for i in range(3):
        t=template_task(P,i);t.update(files=t['reference'],runs=[{'snapshot':'same','passed':4,'total':4,'at':time.time()}]*10,hints=[])
        tasks.append(t)
    monkeypatch.setattr('signalloop_api.portfolio.evaluate',lambda f,c:{'passed':len(c),'total':len(c),'status':'completed','duration_ms':10,'checks':[{'name':x['name'],'passed':True} for x in c]})
    report=build_report({'tasks':tasks,'started_at':time.time()-10,'deadline':time.time()+100})
    assert report['score']==100
    assert all(t['distinct_tested_versions']==1 for t in report['tasks'])

def test_hint_context_excludes_private_answers_and_maps_roles(monkeypatch):
    from signalloop_api import portfolio
    from signalloop_api.ai_policy import AIDecision
    t=template_task(P,1)
    t.update(files=t['starter'],hints=[],runs=[],chat=[{'role':'user','message':'What is an accumulator?'}])
    s={'status':'active','deadline':time.time()+100,'tasks':[{},t],'events':[]}
    captured={}
    class Provider:
        def evaluate(self,message,context,recent_messages,**kwargs):
            captured.update(kwargs)
            return AIDecision(allowed=True,policy_tags=[],message='Trace one event through your accumulator.')
    monkeypatch.setattr(portfolio,'load',lambda db,token:(object(),s))
    monkeypatch.setattr(portfolio,'persist',lambda *args:None)
    monkeypatch.setattr(portfolio,'get_ai_provider',lambda:Provider())
    result=portfolio.hint('test',1,portfolio.HintPayload(message='How do I trace a single value?'),None)
    assert result['allowed']
    assert captured['recent_turns'][0][0]=='candidate'
    assert captured['workspace_files']['CHALLENGE.md'].startswith(t['statement'])
    assert set(captured['workspace_files'])==set(t['starter'])|{'CHALLENGE.md'}
    assert captured['workspace_files']['task_api/helpers.py']!=t['reference']['task_api/helpers.py']

def test_grounded_retry_preserves_verified_contract_and_private_artifacts(monkeypatch):
    import httpx
    from signalloop_api import portfolio_tasks
    monkeypatch.setattr(portfolio_tasks.settings,'featherless_api_key','test-only')
    captured={}
    def post(url,**kwargs):
        captured.update(kwargs['json'])
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"title":"Campus event ownership","why":"Practice aggregation used in your project."}'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(portfolio_tasks.httpx,'post',post)
    result=portfolio_tasks.personalize_task(P,2,grounded=True)
    base=template_task(P,2)
    for field in ('starter','reference','cases','statement','dsa_statement'): assert result[field]==base[field]
    assert result['source']=='Adapted checked exercise'
    assert captured['max_tokens']==500


def test_question_runs_keep_other_question_evidence_without_running_it(monkeypatch):
    from signalloop_api import portfolio
    t=template_task(P,1)
    t.update(files=dict(t['starter']),runs=[],revision=0)
    s={'status':'active','deadline':time.time()+100,'tasks':[t],'events':[]}
    evaluated=[]
    def evaluate(files,cases):
        evaluated.append(cases)
        return {'passed':len(cases),'total':len(cases),'checks':[{'name':c['name'],'passed':True,'actual':c['expected']} for c in cases],'duration_ms':1,'status':'completed'}
    monkeypatch.setattr(portfolio,'load',lambda *args:(object(),s))
    monkeypatch.setattr(portfolio,'persist',lambda *args:None)
    monkeypatch.setattr(portfolio,'evaluate',evaluate)
    first=portfolio.run('test',0,portfolio.RunPayload(files=dict(t['files']),revision=0,question='project'),None)['result']
    assert first['passed']==2 and first['total']==4
    assert all(not c['module'].endswith('dsa') for c in evaluated[-1])
    edited=dict(t['files']);edited['task_api/dsa.py']+='\n# edited'
    second=portfolio.run('test',0,portfolio.RunPayload(files=edited,revision=1,question='dsa'),None)['result']
    assert second['passed']==4 and second['total']==4
    assert all(c['module'].endswith('dsa') and c['public'] for c in evaluated[-1])
    edited=dict(t['files']);edited['task_api/main.py']+='\n# changed project'
    third=portfolio.run('test',0,portfolio.RunPayload(files=edited,revision=2,question='dsa'),None)['result']
    assert third['passed']==2 and third['total']==4


@pytest.mark.parametrize('change',['none','comments','docstring','project','dsa'])
def test_starter_passes_do_not_earn_marks(monkeypatch,change):
    tasks=[]
    for i in range(3):
        t=template_task(P,i)
        files=dict(t['starter'])
        if change=='comments': files={f:v+'\n# comment only\n' for f,v in files.items()}
        if change=='docstring': files={f:'"""Only documentation."""\n'+v for f,v in files.items()}
        if change in ('project','dsa'):
            files.update({f:v for f,v in t['reference'].items() if (f.endswith('/dsa.py'))==(change=='dsa')})
        t.update(files=files,runs=[],hints=[]);tasks.append(t)
    def evaluate(files,cases):
        return {'passed':len(cases),'total':len(cases),'checks':[{'name':c['name'],'passed':True} for c in cases],'status':'completed','duration_ms':1}
    monkeypatch.setattr('signalloop_api.portfolio.evaluate',evaluate)
    state={'tasks':tasks,'started_at':time.time()-10,'deadline':time.time()+100}
    report=build_report(state)
    if change in ('none','comments','docstring'):
        assert report['score']==report['coding_points']==report['ai_points']==0
    else:
        for t,graded in zip(tasks,report['tasks']):
            assert graded['passed']==sum((c['module'].endswith('dsa'))==(change=='dsa') for c in t['cases'])
    # Recalculating old persisted results remains deterministic and does not execute code.
    monkeypatch.setattr('signalloop_api.portfolio.evaluate',lambda *args:pytest.fail('Unexpected execution'))
    assert build_report(state,stored_results=True)['score']==report['score']


def test_cached_tasks_are_fresh_and_private():
    from signalloop_api.portfolio import reusable_tasks
    tasks=[]
    for tier in range(3):
        task=template_task(P,tier)
        task.update(validation={'released':True,'reference_passed':10,'total':10},
                    files={'leaked':'answer'},chat=['private'],hints=['private'],runs=['private'],
                    revision=42,final={'score':100},focus_seconds=900)
        tasks.append(task)
    state={'portfolio':P,'status':'completed','created_at':time.time(),'tasks':tasks,'generation_version':2}
    result=reusable_tasks(state,P)
    assert len(result)==3
    for task in result:
        assert task['files']==task['starter']
        assert task['runs']==task['chat']==task['hints']==[]
        assert task['revision']==0 and task['final'] is None
        assert 'focus_seconds' not in task
    result[0]['files']['task_api/main.py']='changed'
    assert tasks[0]['starter']['task_api/main.py']!='changed'


@pytest.mark.parametrize('change',[
    {'portfolio':P|{'name':'Someone else'}}, {'created_at':0}, {'generation_version':999},
    {'status':'generating'}, {'tasks':[]},
])
def test_cache_rejects_wrong_portfolio_expired_or_incomplete(change):
    from signalloop_api.portfolio import reusable_tasks
    tasks=[template_task(P,i) for i in range(3)]
    for t in tasks:t['validation']={'released':True,'reference_passed':10,'total':10}
    state={'portfolio':P,'status':'ready','created_at':time.time(),'tasks':tasks,'generation_version':2}
    assert reusable_tasks(state|change,P) is None


def test_cache_requires_passing_validation():
    from signalloop_api.portfolio import reusable_tasks
    tasks=[template_task(P,i) for i in range(3)]
    for t in tasks:t['validation']={'released':True,'reference_passed':10,'total':10}
    tasks[1]['validation']['reference_passed']=9
    assert reusable_tasks({'portfolio':P,'status':'ready','created_at':time.time(),'tasks':tasks,'generation_version':2},P) is None


def test_generation_overlaps_authoring_and_preserves_tier_order(monkeypatch):
    import threading
    from signalloop_api import portfolio
    state={'portfolio':P,'status':'generating','tasks':[],'events':[]}
    barrier=threading.Barrier(2)
    overlapped=[]
    class DB:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def rollback(self):pass
    def author(p,tier,grounded=False):
        if tier<2 and not grounded:
            barrier.wait(timeout=2)
            overlapped.append(tier)
        return template_task(p,tier)
    def validate(task):
        task['validation']={'released':True,'reference_passed':10,'total':10}
        return task
    def persist(db,row,s):state.clear();state.update(copy.deepcopy(s))
    monkeypatch.setattr(portfolio,'SessionLocal',DB)
    monkeypatch.setattr(portfolio,'load',lambda db,token:(None,copy.deepcopy(state)))
    monkeypatch.setattr(portfolio,'persist',persist)
    monkeypatch.setattr(portfolio,'personalize_task',author)
    monkeypatch.setattr(portfolio,'validate_task',validate)
    portfolio.generate('test')
    assert sorted(overlapped)==[0,1]
    assert state['status']=='ready'
    assert [task['tier'] for task in state['tasks']]==portfolio.TIERS


@pytest.fixture(autouse=True)
def isolated_usage_budget(monkeypatch,session_factory):
    monkeypatch.setattr('signalloop_api.portfolio_limits.SessionLocal',session_factory)
