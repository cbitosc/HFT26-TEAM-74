import os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'apps/worker'),str(root/'apps/api')]
assert os.environ.get('PROOFSTACK_WASI_ROOT'), 'Set PROOFSTACK_WASI_ROOT to the verified runtime directory'
os.environ['PROOFSTACK_EXECUTION_RUNTIME']='wasi'
from signalloop_worker.functions import FunctionRequest,evaluate_functions
from signalloop_api.portfolio_tasks import template_task
p={'projects':[{'name':'Runtime check'}],'domain':'Backend','skills':['Python'],'dsa_topics':['arrays']}
for topic in ['arrays','hash_maps','strings']:
 p['dsa_topics']=[topic]
 for tier in range(3):
  t=template_task(p,tier)
  r=evaluate_functions(FunctionRequest(files=t['reference'],cases=t['cases']))
  assert r['passed']==r['total'],(topic,tier,r)
  starter=evaluate_functions(FunctionRequest(files=t['starter'],cases=t['cases']))
  assert starter['passed']<starter['total']
  print(topic,tier,'PASS',r['duration_ms'],flush=True)
def probe(code,expected,timeout=8):
 r=evaluate_functions(FunctionRequest(files={'task_api/__init__.py':'','task_api/main.py':code},cases=[{'name':'probe','module':'task_api.main','input':None,'expected':expected,'public':True}],timeout_seconds=timeout))
 return r
r=probe('def solve(data):\n import os\n return dict(os.environ)\n',{'PYTHONHOME':'/python','PYTHONDONTWRITEBYTECODE':'1'})
assert r['passed']==1,r
for code in ['def solve(data):\n return open("/etc/passwd").read()','def solve(data):\n return open("/workspace/task_api/main.py","w").write("bad")','def solve(data):\n import socket\n return socket.create_connection(("example.com",80))']:
 r=probe(code,None);assert r['passed']==0 and r['checks'][0].get('error'),r
r=probe('def solve(data):\n while True: pass',None,1);assert r['status']=='timeout',r
print('PASS: environment isolation, host file denial, read-only workspace, no sockets, infinite-loop timeout',flush=True)
