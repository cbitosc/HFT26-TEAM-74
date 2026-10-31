FROM node:22-bookworm-slim AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
ENV NEXT_PUBLIC_PYTHON_RUNTIME="Python 3.15 · WASI sandbox"
RUN npm run build

FROM python:3.13-slim-bookworm
WORKDIR /app
COPY apps/api/pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib,subprocess; subprocess.check_call(['pip','install','--no-cache-dir',*tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['dependencies'],'wasmtime==50.0.1'])"
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /app/apps/web /app/apps/web
COPY apps/api /app/apps/api
COPY apps/worker/signalloop_worker /app/apps/worker/signalloop_worker
COPY assessment_packs /app/assessment_packs
COPY deploy/render /app/deploy/render
ENV PYTHONPATH=/app/apps/api:/app/apps/worker \
    PROOFSTACK_WASI_ROOT=/opt/python-wasi \
    PROOFSTACK_EXECUTION_RUNTIME=wasi \
    EXECUTION_WORKER_URL=http://127.0.0.1:9001 \
    PROOFSTACK_API_INTERNAL_URL=http://127.0.0.1:8001 \
    REPORT_ADVISORY_ENABLED=false \
    NODE_ENV=production
RUN python /app/deploy/render/download_runtime.py
RUN useradd --create-home app && chown -R app:app /app
USER app
EXPOSE 10000
CMD ["python","/app/deploy/render/start.py"]
