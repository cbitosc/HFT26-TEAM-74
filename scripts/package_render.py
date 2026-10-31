"""Package tracked/unignored app source for the private Render deployment repo."""
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
out = root / 'work/cloud-upload'
out.mkdir(parents=True, exist_ok=True)
names = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
secret_values = []
env = root / '.env'
if env.exists():
    for line in env.read_text().splitlines():
        if '=' not in line or line.startswith('#'): continue
        key, value = line.split('=', 1)
        value = value.strip().strip('\"\'')
        if any(word in key for word in ('KEY', 'SECRET', 'TOKEN', 'PASSWORD')) and len(value) > 12:
            secret_values.append(value.encode())
files = []
for name in sorted(set(names)):
    p = root / name
    if not name or not p.is_file() or p.is_symlink(): continue
    if any(part in p.parts for part in ('node_modules', '.next', '.venv', '__pycache__', 'work')): continue
    if p.name.startswith('.env') and p.name != '.env.example': continue
    if name.startswith(('apps/', 'assessment_packs/', 'deploy/render/', 'scripts/', 'docs/submission/')) or name in ('LICENSE', 'NOTICE', 'README.md', 'PROOFSTACK.md', 'AUTHORS.md', 'THIRD_PARTY_NOTICES.md', '.env.example', '.gitignore', '.dockerignore'):
        if any(secret in p.read_bytes() for secret in secret_values):
            raise SystemExit('Secret detected; packaging stopped')
        files.append(name)
with tarfile.open(out / 'source.tar.gz', 'w:gz') as archive:
    for name in files: archive.add(root / name, arcname=name, recursive=False)
s = (root / 'deploy/render/Dockerfile').read_text()
s = 'FROM alpine:3.22 AS source\nWORKDIR /source\nADD source.tar.gz /source/\n\n' + s
s = s.replace('COPY apps/', 'COPY --from=source /source/apps/').replace(' apps/web/package-lock.json', ' /source/apps/web/package-lock.json')
s = s.replace('COPY assessment_packs', 'COPY --from=source /source/assessment_packs').replace('COPY deploy/render', 'COPY --from=source /source/deploy/render')
(out / 'Dockerfile').write_text(s)
print(f'Packaged {len(files)} source files without environment files or configured secrets.')
