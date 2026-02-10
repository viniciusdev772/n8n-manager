"""Entrypoint — inicializa infra e sobe o servidor."""

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.cleanup import start_cleanup, stop_cleanup
from app.config import ALLOWED_ORIGINS, SERVER_PORT
from app.logger import setup_logging
from app.docker_client import close_client
from app.infra import bootstrap_infra
from app.n8n import sync_instance_env_vars
from app.queue import close_rabbitmq
from app.routes import router
from app.worker import start_worker, stop_worker

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bootstrap_infra()
    sync_instance_env_vars()
    start_worker()
    start_cleanup()
    yield
    stop_cleanup()
    stop_worker()
    close_rabbitmq()
    close_client()


app = FastAPI(title="N8N Instance Manager", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.mount("/ui", StaticFiles(directory=os.path.join(_BASE_DIR, "frontend"), html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=SERVER_PORT, reload=False)
