from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.api.knowledge import router as knowledge_router
from app.api.retrieval import router as retrieval_router
from app.api.evaluation import router as evaluation_router
from app.core.config import Settings, get_settings
from app.core.errors import UTF8JSONResponse, register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.db.session import dispose_engine, init_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_engine(app.state.settings)
    try:
        yield
    finally:
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.app_env)

    app = FastAPI(
        title="Asphoif RAG API",
        version="0.1.0",
        description="RAG system backend foundation",
        default_response_class=UTF8JSONResponse,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    cors_origins = [
        origin.strip()
        for origin in app_settings.cors_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(ingestion_router)
    app.include_router(retrieval_router)
    app.include_router(evaluation_router)
    return app


app = create_app()
