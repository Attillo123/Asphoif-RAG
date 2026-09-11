from app.ingestion.chunking import FixedChunker
from app.ingestion.parsers import (
    MarkdownParser,
    ParserNotImplementedError,
    PdfParser,
    TextParser,
    WordParser,
)
from app.services.ingestion import stable_chunk_id


def test_markdown_parser_keeps_heading_path_and_body() -> None:
    parsed = MarkdownParser().parse("# 费用制度\n\n报销需要发票。\n\n## 额度\n\n单次不超过一万元。")

    assert parsed.language == "zh"
    assert any(block.section_path == ("费用制度",) for block in parsed.blocks)
    assert any("报销需要发票" in block.text for block in parsed.blocks)
    assert any(block.section_path == ("费用制度", "额度") for block in parsed.blocks)


def test_text_parser_supports_utf8_and_fixed_chunking() -> None:
    parsed = TextParser().parse("第一段内容。\n\n第二段内容。")
    chunks = FixedChunker(max_chars=5, overlap=1).chunk(parsed)

    assert len(chunks) >= 2
    assert all(chunk.content_hash for chunk in chunks)
    assert all(chunk.language == "zh" for chunk in chunks)


def test_chunk_id_is_stable_and_retry_safe() -> None:
    assert stable_chunk_id("version-01", 0) == stable_chunk_id("version-01", 0)
    assert stable_chunk_id("version-01", 0) != stable_chunk_id("version-01", 1)
    assert len(stable_chunk_id("version-01", 0)) == 26


def test_pdf_and_word_are_explicit_extension_points() -> None:
    for parser in (PdfParser(), WordParser()):
        try:
            parser.parse("not implemented")
        except ParserNotImplementedError:
            pass
        else:
            raise AssertionError("extension parser unexpectedly implemented")
