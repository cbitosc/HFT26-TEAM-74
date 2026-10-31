#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
if [[ ! -f .env ]]; then cp .env.example .env; fi
if [[ ! -f apps/web/.env.local ]]; then
 printf '%s\n' 'PROOFSTACK_API_INTERNAL_URL=http://127.0.0.1:8001' > apps/web/.env.local
fi
for cmd in docker npm uv; do
 command -v "$cmd" >/dev/null || { echo "Missing prerequisite: $cmd (see README.md)"; exit 1; }
done
PYTHON="$ROOT/apps/api/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then (cd apps/api && uv sync --frozen); fi
if [[ ! -d apps/web/node_modules ]]; then (cd apps/web && npm ci); fi
if ! docker info >/dev/null 2>&1; then echo 'Start Docker Desktop, then retry.'; exit 1; fi
if docker container inspect proofstack-postgres >/dev/null 2>&1; then
 docker start proofstack-postgres >/dev/null
else
 docker run -d --name proofstack-postgres -e POSTGRES_PASSWORD=proofstack-local -e POSTGRES_DB=proofstack -v proofstack-data:/var/lib/postgresql/data -p 127.0.0.1:55432:5432 postgres:16-alpine >/dev/null
fi
if ! docker image inspect signalloop-python-assessment:3.11 >/dev/null 2>&1; then
 docker build -f apps/worker/docker/python-assessment.Dockerfile -t signalloop-python-assessment:3.11 apps/worker
fi
for i in {1..30}; do docker exec proofstack-postgres pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
(cd apps/api && .venv/bin/alembic upgrade head)
(cd apps/worker && exec "$PYTHON" -m uvicorn signalloop_worker.main:app --host 127.0.0.1 --port 9001) & WORKER_PID=$!
(cd apps/api && exec "$PYTHON" -m uvicorn signalloop_api.main:app --host 127.0.0.1 --port 8001) & API_PID=$!
(cd apps/web && exec npm run dev -- --hostname 127.0.0.1 --port 3001) & WEB_PID=$!
trap 'kill "$WORKER_PID" "$API_PID" "$WEB_PID" 2>/dev/null || true' EXIT INT TERM
echo 'ProofStack: http://127.0.0.1:3001 — Ctrl+C stops these app processes, preserving your database.'
wait
