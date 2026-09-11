from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import dispose_engine, get_db_session, init_engine
from app.ingestion.embeddings import QwenEmbeddingClient
from app.ingestion.indexers import OpenSearchChunkIndexer
from app.ingestion.storage import LocalFileStorage
from app.models.user import User
from app.retrieval.opensearch import OpenSearchIndexManager
from app.services.auth import hash_password
from app.services.ingestion import (
    claim_next_ingestion_job,
    process_ingestion_job,
    reset_ingestion_job_for_reindex,
    reset_ingestion_job_for_retry,
)


def _ingestion_clients(settings):
    embedding_client = QwenEmbeddingClient(
        base_url=settings.qwen_embedding_base_url,
        api_key=settings.qwen_embedding_api_key.get_secret_value(),
        model=settings.qwen_embedding_model,
        dimension=settings.qwen_embedding_dimension,
        timeout_seconds=settings.healthcheck_timeout_seconds,
    )
    indexer = OpenSearchChunkIndexer(
        base_url=settings.opensearch_url,
        write_alias=settings.opensearch_write_alias,
        index_version=settings.opensearch_index,
        embedding_model=settings.qwen_embedding_model,
        username=settings.opensearch_username,
        password=(
            settings.opensearch_password.get_secret_value()
            if settings.opensearch_password
            else None
        ),
        verify_ssl=settings.opensearch_verify_ssl,
        timeout_seconds=settings.healthcheck_timeout_seconds,
    )
    return LocalFileStorage(settings.local_file_root), embedding_client, indexer


async def create_user(username: str, password: str, role: str) -> None:
    settings = get_settings()
    init_engine(settings)
    async for session in get_db_session():
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            raise SystemExit(f"user already exists: {username}")
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            )
        )
        await session.commit()
        print(f"created user: {username} ({role})")
    await dispose_engine()


async def process_job(job_id: str) -> None:
    settings = get_settings()
    init_engine(settings)
    storage, embedding_client, indexer = _ingestion_clients(settings)
    try:
        async for session in get_db_session():
            job = await process_ingestion_job(
                session,
                job_id,
                settings,
                storage,
                embedding_client,
                indexer,
            )
            print(f"processed ingestion job: {job.id} ({job.status})")
    finally:
        await dispose_engine()


async def process_next_job() -> None:
    settings = get_settings()
    init_engine(settings)
    try:
        async for session in get_db_session():
            claimed = await claim_next_ingestion_job(session, settings)
            if claimed is None:
                print("no pending ingestion jobs")
                return
            storage, embedding_client, indexer = _ingestion_clients(settings)
            job = await process_ingestion_job(
                session, claimed.id, settings, storage, embedding_client, indexer
            )
            print(f"processed ingestion job: {job.id} ({job.status})")
    finally:
        await dispose_engine()


async def retry_job(job_id: str) -> None:
    settings = get_settings()
    init_engine(settings)
    try:
        async for session in get_db_session():
            job = await reset_ingestion_job_for_retry(session, job_id)
            print(f"reset ingestion job: {job.id} ({job.status})")
    finally:
        await dispose_engine()


async def reindex_job(job_id: str) -> None:
    settings = get_settings()
    init_engine(settings)
    try:
        async for session in get_db_session():
            job = await reset_ingestion_job_for_reindex(session, job_id)
            print(f"scheduled ingestion reindex: {job.id} ({job.status})")
    finally:
        await dispose_engine()


async def init_opensearch() -> None:
    settings = get_settings()
    password = (
        settings.opensearch_password.get_secret_value()
        if settings.opensearch_password
        else None
    )
    manager = OpenSearchIndexManager(
        base_url=settings.opensearch_url,
        index_name=settings.opensearch_index,
        read_alias=settings.opensearch_read_alias,
        write_alias=settings.opensearch_write_alias,
        dimension=settings.qwen_embedding_dimension,
        username=settings.opensearch_username,
        password=password,
        verify_ssl=settings.opensearch_verify_ssl,
        timeout_seconds=settings.healthcheck_timeout_seconds,
    )
    result = await manager.ensure()
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Asphoif RAG administration CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("--username", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--role", choices=("personal", "admin"), default="personal")
    process = subparsers.add_parser("process-ingestion-job")
    process.add_argument("--job-id", required=True)
    subparsers.add_parser("process-next-ingestion-job")
    retry = subparsers.add_parser("retry-ingestion-job")
    retry.add_argument("--job-id", required=True)
    reindex = subparsers.add_parser("reindex-ingestion-job")
    reindex.add_argument("--job-id", required=True)
    subparsers.add_parser("init-opensearch")
    args = parser.parse_args()
    if args.command == "create-user":
        asyncio.run(create_user(args.username, args.password, args.role))
    elif args.command == "process-ingestion-job":
        asyncio.run(process_job(args.job_id))
    elif args.command == "process-next-ingestion-job":
        asyncio.run(process_next_job())
    elif args.command == "retry-ingestion-job":
        asyncio.run(retry_job(args.job_id))
    elif args.command == "reindex-ingestion-job":
        asyncio.run(reindex_job(args.job_id))
    elif args.command == "init-opensearch":
        asyncio.run(init_opensearch())


if __name__ == "__main__":
    main()
