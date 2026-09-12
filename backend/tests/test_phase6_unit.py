import json

from app.api.chat import _context, _sse


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
