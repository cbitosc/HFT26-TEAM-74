"""One public Next listener, private API and WASI worker; exit if any child fails."""
import os,signal,subprocess,sys,time
root='/app'
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=root+'/apps/api',check=True)
children=[]
def stop(*args):
 for p in children:
  if p.poll() is None: p.terminate()
 for p in children:
  try:p.wait(timeout=8)
  except subprocess.TimeoutExpired:p.kill()
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
commands=[
 [sys.executable,'-m','uvicorn','signalloop_worker.functions_app:app','--host','127.0.0.1','--port','9001'],
 [sys.executable,'-m','uvicorn','signalloop_api.main:app','--host','127.0.0.1','--port','8001'],
 ['node',root+'/apps/web/node_modules/next/dist/bin/next','start','-H','0.0.0.0','-p',os.getenv('PORT','10000')],
]
try:
 for command in commands:children.append(subprocess.Popen(command,cwd=root+'/apps/web'))
 while all(p.poll() is None for p in children):time.sleep(1)
finally:stop()
