"""Pinned upstream CPython WASI distribution, verified before extraction."""
import hashlib
import os
import urllib.request
import zipfile
from pathlib import Path
URL='https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.15.2/python-3.15.2-wasi_sdk-33.zip'
SHA256='b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888'
root=Path(os.environ['PROOFSTACK_WASI_ROOT']);root.mkdir(parents=True,exist_ok=True)
archive=root/'runtime.zip'
with urllib.request.urlopen(URL,timeout=60) as response: archive.write_bytes(response.read())
if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA256: raise RuntimeError('Runtime checksum mismatch')
with zipfile.ZipFile(archive) as z:
 for entry in z.infolist():
  if not (root/entry.filename).resolve().is_relative_to(root.resolve()): raise RuntimeError('Invalid archive path')
 z.extractall(root)
archive.unlink()
from signalloop_worker.wasi_runtime import runtime
_,_,module=runtime()
(root/'python.cwasm').write_bytes(module.serialize())
print('Verified and compiled Python WASI runtime.')
