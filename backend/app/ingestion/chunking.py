from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.ingestion.parsers import ParsedDocument


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    content_hash: str
    start_offset: int
    end_offset: int
    section_path: tuple[str, ...]
    language: str


class FixedChunker:
    strategy_version = "fixed-char-v1"

    def __init__(self, max_chars: int = 1000, overlap: int = 150) -> None:
        if max_chars < 1 or overlap < 0 or overlap >= max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for block in document.blocks:
            start = 0
            while start < len(block.text):
                end = min(start + self.max_chars, len(block.text))
                piece = block.text[start:end].strip()
                if piece:
                    piece_start = block.start_offset + start
                    drafts.append(
                        ChunkDraft(
                            ordinal=len(drafts),
                            content=piece,
                            content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                            start_offset=piece_start,
                            end_offset=piece_start + len(piece),
                            section_path=block.section_path,
                            language=document.language,
                        )
                    )
                if end == len(block.text):
                    break
                start = end - self.overlap
        return drafts
