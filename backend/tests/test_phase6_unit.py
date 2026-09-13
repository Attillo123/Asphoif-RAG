import json

from app.api.chat import _context, _redact, _sse


def test_trace_redaction_masks_phone_and_id_and_marks_truncation() -> None:
    value = "联系 13812345678，证件 110101199001011234，敏感内容"
    redacted = _redact(value, 100)
    assert "13812345678" not in redacted
    assert "110101199001011234" not in redacted
    assert "[PHONE]" in redacted
    assert "[ID_NUMBER]" in redacted
    assert "[TRUNCATED]" not in redacted
    assert "[TRUNCATED]" in _redact(value, 20)


def test_sse_frame_is_json_and_sequence_numbered() -> None:
    frame = _sse("delta", 2, {"text": "你好"})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    data = json.loads(frame.split("data: ", 1)[1].strip())
    assert data == {"seq": 2, "text": "你好"}


def test_context_builds_citations_and_applies_character_limit() -> None:
    result = {
        "data": {
            "fused": [
                {
                    "chunk_id": "chunk-1",
                    "rank": 1,
                    "source": {
                        "content": "第一段内容",
                        "document_id": "doc-1",
                        "metadata": {"file_name": "测试.txt"},
                    },
                }
            ]
        }
    }
    context, citations = _context(result, 100)
    assert context.startswith("[1] 第一段内容")
    assert citations == [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "file_name": "测试.txt",
            "rank": 1,
        }
    ]
