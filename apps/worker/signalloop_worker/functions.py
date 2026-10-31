"""JSON function evaluation. Expected outputs stay outside the candidate container."""
import os
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from fastapi import APIRouter
from pydantic import BaseModel, Field
from signalloop_worker.workspace import write_workspace

router = APIRouter()

class FunctionRequest(BaseModel):
    files: dict[str, str]
    cases: list[dict] = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=8, ge=1, le=20)

@router.post('/evaluate-functions')
def evaluate_functions(payload: FunctionRequest):
    allowed = {'task_api/main.py', 'task_api/helpers.py', 'task_api/storage.py', 'task_api/dsa.py', 'task_api/__init__.py'}
    if set(payload.files) - allowed or sum(map(len, payload.files.values())) > 100_000:
        from fastapi import HTTPException
        raise HTTPException(422, 'Invalid code files')
    if os.environ.get('PROOFSTACK_EXECUTION_RUNTIME') == 'wasi':
        from signalloop_worker.wasi_runtime import evaluate_wasi
        return evaluate_wasi(payload)
    name = 'proofstack-' + uuid4().hex
    started = time.monotonic()
    with TemporaryDirectory(prefix='proofstack-run-') as folder:
        root = Path(folder)
        root.chmod(0o755)
        write_workspace(root, payload.files)
        # Only arguments and entry module enter the container, never expected values.
        inputs = [{'module': c['module'], 'input': c['input']} for c in payload.cases]
        (root / 'inputs.json').write_text(json.dumps(inputs))
        (root / 'runner.py').write_text('''import json, importlib, sys, contextlib, io
cases = json.load(open('/workspace/inputs.json'))
for index, case in enumerate(cases):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            value = importlib.import_module(case['module']).solve(case['input'])
        print(json.dumps({'index': index, 'value': value}), flush=True)
    except BaseException as error:
        print(json.dumps({'index': index, 'error': type(error).__name__}), flush=True)
''')
        command = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--cpus', '1',
                   '--memory', '256m', '--pids-limit', '32', '--read-only', '--user', '65534:65534',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--ulimit', 'fsize=1024:1024', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                   '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', f'{root}:/workspace:ro', '-w', '/workspace',
                   'signalloop-python-assessment:3.11', 'python', '-B', 'runner.py']
        status = 'completed'
        # Regular bounded output files avoid unbounded capture_output allocation.
        with TemporaryDirectory(prefix='proofstack-output-') as outdir:
            with open(Path(outdir)/'stdout', 'w+b') as out, open(Path(outdir)/'stderr', 'w+b') as err:
                process = subprocess.Popen(command, stdout=out, stderr=err)
                try:
                    while process.poll() is None:
                        if time.monotonic()-started > payload.timeout_seconds:
                            status = 'timeout'; break
                        if out.tell() + err.tell() > 1_000_000:
                            status = 'output_limit'; break
                        time.sleep(0.05)
                    if status != 'completed':
                        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
                        process.kill()
                    process.wait(timeout=10)
                    if process.returncode and status == 'completed': status = 'infrastructure_error' if process.returncode == 125 else 'error'
                    out.seek(0); raw = out.read(1_000_000).decode(errors='replace')
                    err.seek(0); errors = err.read(4000).decode(errors='replace')
                finally:
                    subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
                    if process.poll() is None: process.kill()
        actual = {}
        for line in raw.splitlines():
            try:
                item = json.loads(line)
                if type(item.get('index')) is int: actual[item['index']] = item
            except (ValueError, AttributeError): pass
        checks = []
        for i, case in enumerate(payload.cases):
            value = actual.get(i, {})
            passed = status == 'completed' and 'value' in value and json.dumps(value['value'],sort_keys=True) == json.dumps(case['expected'],sort_keys=True)
            checks.append({'name':case.get('name',f'Case {i+1}'),'passed':passed,
                           'error':value.get('error') if status == 'completed' else status,
                           **({'actual':value['value']} if case.get('public') and 'value' in value else {})})
        return {'status':status,'passed':sum(c['passed'] for c in checks),'total':len(checks),
                'checks':checks,'duration_ms':round((time.monotonic()-started)*1000),
                'stderr':errors if status == 'error' else ''}
