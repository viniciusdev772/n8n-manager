"""Entrypoint — inicializa infra e sobe o servidor."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import SERVER_PORT
from app.docker_client import close_client
from app.infra import bootstrap_infra
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_infra()
    yield
    close_client()


app = FastAPI(title="N8N Instance Manager", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=SERVER_PORT, reload=False)
