"""Minimal internal runner service for the free deployment."""
from fastapi import FastAPI
from signalloop_worker.functions import router
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(router)
