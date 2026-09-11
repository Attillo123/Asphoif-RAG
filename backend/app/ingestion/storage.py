from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Protocol


class FileStorage(Protocol):
    async def save(self, data: bytes, relative_uri: str) -> str:
        ...

    async def read(self, storage_uri: str) -> bytes:
        ...


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _safe_path(self, relative_uri: str) -> Path:
        candidate = (self.root / relative_uri).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("storage path escapes local file root")
        return candidate

    async def save(self, data: bytes, relative_uri: str) -> str:
        target = self._safe_path(relative_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")

        def write() -> None:
            with temporary.open("wb") as handle:
                handle.write(data)
            os.replace(temporary, target)

        await asyncio.to_thread(write)
        return relative_uri.replace("\\", "/")

    async def read(self, storage_uri: str) -> bytes:
        target = self._safe_path(storage_uri)
        return await asyncio.to_thread(target.read_bytes)


def safe_file_name(file_name: str) -> str:
    name = Path(file_name).name
    name = re.sub(r"[^\w.()\-\u3400-\u9fff ]", "_", name, flags=re.UNICODE).strip()
    return name[:255] or "document"
