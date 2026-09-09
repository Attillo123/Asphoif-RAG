from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import dispose_engine, get_db_session, init_engine
from app.models.user import User
from app.services.auth import hash_password


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Asphoif RAG administration CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("--username", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--role", choices=("personal", "admin"), default="personal")
    args = parser.parse_args()
    if args.command == "create-user":
        asyncio.run(create_user(args.username, args.password, args.role))


if __name__ == "__main__":
    main()
