"""Capability-limited CPython/WASI runner. No network, host env or evaluator answers."""
import json
import os
import platform
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from functools import lru_cache

from signalloop_worker.workspace import write_workspace

_lock = threading.Lock()

@lru_cache(maxsize=1)
def runtime():
    import wasmtime
    root=Path(os.environ['PROOFSTACK_WASI_ROOT']).resolve()
    config=wasmtime.Config()
    # Render build hosts can have newer CPUs than runtime hosts. An explicit
    # target disables native CPU feature inference for portable build artifacts.
    if platform.system() == 'Linux':
        machine = platform.machine()
        if machine in ('x86_64', 'aarch64'):
            config.target = f'{machine}-unknown-linux-gnu' 
    config.consume_fuel=True
    config.epoch_interruption=True
    config.memory_reservation=128*1024*1024
    engine=wasmtime.Engine(config)
    # Only deserialize our own build artifact, never candidate-supplied bytes.
    compiled=root/'python.cwasm'
    module=(wasmtime.Module.deserialize_file(engine,str(compiled)) if compiled.exists()
            else wasmtime.Module.from_file(engine,str(root/'python.wasm')))
    return root,engine,module


def evaluate_wasi(payload):
    import wasmtime
    from fastapi import HTTPException
    if not _lock.acquire(timeout=60):
        raise HTTPException(503,'Code runner is busy. Retry in a moment.')
    started=time.monotonic()
    try:
        root,engine,module=runtime()
        with TemporaryDirectory(prefix='proofstack-wasi-') as folder:
            workspace=Path(folder)
            write_workspace(workspace,payload.files)
            # Expected values are kept exclusively in the host evaluator.
            inputs=[{'module':c['module'],'input':c['input']} for c in payload.cases]
            (workspace/'inputs.json').write_text(json.dumps(inputs))
            (workspace/'runner.py').write_text('''import json,importlib,contextlib,io,sys
sys.path.insert(0,'/workspace')
for index,case in enumerate(json.load(open('/workspace/inputs.json'))):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            value=importlib.import_module(case['module']).solve(case['input'])
        print(json.dumps({'index':index,'value':value}),flush=True)
    except BaseException as error:
        print(json.dumps({'index':index,'error':type(error).__name__}),flush=True)
''')
            output=bytearray();errors=bytearray();exceeded=[False]
            def capture(target):
                def write(chunk):
                    if len(output)+len(errors)+len(chunk)>1_000_000:
                        exceeded[0]=True;engine.increment_epoch();return -1
                    target.extend(chunk);return len(chunk)
                return write
            with wasmtime.Store(engine) as store:
                store.set_limits(memory_size=96*1024*1024,instances=1,memories=1,tables=2,table_elements=100000)
                store.set_fuel(10_000_000_000)
                store.set_epoch_deadline(1)
                wasi=wasmtime.WasiConfig()
                wasi.argv=['python','-B','/workspace/runner.py']
                wasi.env=[('PYTHONHOME','/python'),('PYTHONDONTWRITEBYTECODE','1')]
                wasi.preopen_dir(str(root),'/python',fs_mutable=False)
                wasi.preopen_dir(str(workspace),'/workspace',fs_mutable=False)
                wasi.stdout_custom=capture(output);wasi.stderr_custom=capture(errors)
                store.set_wasi(wasi)
                status='completed'
                timer=threading.Timer(payload.timeout_seconds,engine.increment_epoch)
                timer.daemon=True;timer.start()
                try:
                    with wasmtime.Linker(engine) as linker:
                        linker.define_wasi()
                        instance=linker.instantiate(store,module)
                        instance.exports(store)['_start'](store)
                except wasmtime.ExitTrap as exc:
                    if exc.code: status='error'
                except wasmtime.Trap:
                    status='output_limit' if exceeded[0] else 'timeout'
                finally:
                    timer.cancel();timer.join()
            actual={}
            for line in output.decode(errors='replace').splitlines():
                try:
                    record=json.loads(line)
                    if isinstance(record,dict) and type(record.get('index')) is int: actual[record['index']]=record
                except (ValueError,TypeError): pass
            checks=[]
            for i,case in enumerate(payload.cases):
                item=actual.get(i,{})
                passed=status=='completed' and 'value' in item and json.dumps(item['value'],sort_keys=True)==json.dumps(case['expected'],sort_keys=True)
                checks.append({'name':case.get('name',f'Case {i+1}'),'passed':passed,
                    'error':item.get('error') if status=='completed' else status,
                    **({'actual':item['value']} if case.get('public') and 'value' in item else {})})
            return {'status':status,'passed':sum(c['passed'] for c in checks),'total':len(checks),
                    'checks':checks,'duration_ms':round((time.monotonic()-started)*1000),'stderr':''}
    finally:
        _lock.release()
