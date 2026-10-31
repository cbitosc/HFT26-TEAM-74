"""Real API/Docker smoke test; creates its own clearly labeled assessment."""
import copy
import secrets
import time
import httpx
from signalloop_api.database import SessionLocal
from signalloop_api.models import PortfolioSession
from signalloop_api.portfolio_tasks import template_task, validate_task

p={'name':'Automated verification','domain':'Backend','projects':[{'name':'Synthetic test project','tech_stack':['Python']}],'skills':['Python'],'dsa_topics':['arrays','hash_maps','strings']}
tasks=[]
for i in range(3):
 t=validate_task(template_task(p,i)); t.update(files=copy.deepcopy(t['starter']),revision=0,runs=[],hints=[],chat=[],final=None,fallback_reason=None);tasks.append(t)
token=secrets.token_urlsafe(32)
with SessionLocal() as db:
 db.add(PortfolioSession(token=token,state={'portfolio':p,'status':'ready','created_at':time.time(),'started_at':None,'deadline':None,'tasks':tasks,'events':[],'report':None,'error':None}));db.commit()
base='http://127.0.0.1:8001/portfolio/sessions/'+token
c=httpx.Client(timeout=60)
def check(method,path,expected=200,**kwargs):
 r=c.request(method,base+path,**kwargs)
 assert r.status_code==expected,(path,r.status_code,r.text[:200]);return r.json()
view=check('GET','')
assert all('reference' not in t and 'cases' not in t for t in view['tasks'])
check('POST','/tasks/0/run',409,json={'files':tasks[0]['starter'],'revision':0})
check('POST','/start')
check('POST','/tasks/0/hint',403,json={'message':'Explain this'})
for i,t in enumerate(tasks):
 check('PUT',f'/tasks/{i}/code',422,json={'files':{'runner.py':'bad'},'revision':0})
 result=check('POST',f'/tasks/{i}/run',json={'files':t['reference'],'revision':0})
 assert result['result']['passed']==result['result']['total']
 check('PUT',f'/tasks/{i}/code',409,json={'files':t['reference'],'revision':0})
finished=check('POST','/finish')
assert finished['report']['score']==100,finished['report']
check('PUT','/tasks/0/code',409,json={'files':tasks[0]['starter'],'revision':1})
assert check('POST','/finish')['report']==finished['report']
print('PASS: private answers withheld; start gate; Easy AI disabled; file allowlist; real public + hidden tests; stale saves blocked; perfect score; immutable final result.')
# Expiry enforcement, using a separate synthetic session.
token2=secrets.token_urlsafe(32)
expired=copy.deepcopy(finished)
expired.pop('token',None);expired.pop('server_time',None)
# Keep full private specs for this fixture, never candidate-view copies.
expired.update(status='active',tasks=copy.deepcopy(tasks),deadline=time.time()-1,started_at=time.time()-3601,report=None)
with SessionLocal() as db:
 db.add(PortfolioSession(token=token2,state=expired));db.commit()
r=c.put('http://127.0.0.1:8001/portfolio/sessions/'+token2+'/tasks/0/code',json={'files':tasks[0]['reference'],'revision':0})
assert r.status_code==409
print('PASS: expired session rejects late code.')
