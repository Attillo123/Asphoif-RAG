from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ParserNotImplementedError(RuntimeError):
    """The file format is recognized but its parser is not in the initial release."""


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    start_offset: int
    end_offset: int
    section_path: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    blocks: tuple[ParsedBlock, ...]
    parser_version: str
    language: str


class DocumentParser(Protocol):
    parser_version: str

    def parse(self, text: str) -> ParsedDocument:
        ...


def detect_language(text: str) -> str:
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    if cjk_count and cjk_count >= letters:
        return "zh"
    if letters:
        return "en"
    return "unknown"


class TextParser:
    parser_version = "text-v1"

    def parse(self, text: str) -> ParsedDocument:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        blocks: list[ParsedBlock] = []
        for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", normalized):
            block = match.group(0).strip()
            if block:
                start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
                end = start + len(block)
                blocks.append(ParsedBlock(block, start, end))
        if not blocks and normalized.strip():
            start = len(normalized) - len(normalized.lstrip())
            blocks.append(ParsedBlock(normalized.strip(), start, start + len(normalized.strip())))
        return ParsedDocument(
            text=normalized,
            blocks=tuple(blocks),
            parser_version=self.parser_version,
            language=detect_language(normalized),
        )


class MarkdownParser:
    parser_version = "markdown-v1"
    _heading = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

    def parse(self, text: str) -> ParsedDocument:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        paragraph_start: int | None = None
        paragraph_lines: list[str] = []

        def flush(end_offset: int) -> None:
            nonlocal paragraph_start, paragraph_lines
            if paragraph_start is None:
                return
            block = "\n".join(paragraph_lines).strip()
            if block:
                leading = len("\n".join(paragraph_lines)) - len("\n".join(paragraph_lines).lstrip())
                start = paragraph_start + leading
                blocks.append(ParsedBlock(block, start, start + len(block), tuple(headings)))
            paragraph_start = None
            paragraph_lines = []

        offset = 0
        for line in normalized.splitlines(keepends=True):
            line_text = line.rstrip("\n")
            heading_match = self._heading.match(line_text)
            if heading_match:
                flush(offset)
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                headings[:] = headings[: level - 1]
                headings.append(title)
                blocks.append(ParsedBlock(title, offset, offset + len(line_text), tuple(headings)))
            elif line_text.strip():
                if paragraph_start is None:
                    paragraph_start = offset
                paragraph_lines.append(line_text)
            else:
                flush(offset)
            offset += len(line)
        flush(len(normalized))
        return ParsedDocument(
            text=normalized,
            blocks=tuple(blocks),
            parser_version=self.parser_version,
            language=detect_language(normalized),
        )


class PdfParser:
    parser_version = "pdf-v1-interface"

    def parse(self, text: str) -> ParsedDocument:
        raise ParserNotImplementedError("PDF parser is reserved for a later phase")


class WordParser:
    parser_version = "word-v1-interface"

    def parse(self, text: str) -> ParsedDocument:
        raise ParserNotImplementedError("Word parser is reserved for a later phase")


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("text", data, 0, len(data), "unsupported text encoding")


def get_parser(file_name: str, content_type: str | None = None) -> DocumentParser:
    suffix = Path(file_name).suffix.lower()
    if suffix in {".md", ".markdown"} or content_type == "text/markdown":
        return MarkdownParser()
    if suffix == ".txt" or content_type == "text/plain":
        return TextParser()
    if suffix == ".pdf" or content_type == "application/pdf":
        return PdfParser()
    if suffix in {".doc", ".docx"} or content_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return WordParser()
    raise ValueError(f"unsupported document type: {suffix or content_type or 'unknown'}")
