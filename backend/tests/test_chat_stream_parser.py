import json

import pytest

from app.api.chat import _upstream_events


class _Response:
    is_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_lines(self):
        for line in (
            'data: ' + json.dumps({"choices": [{"delta": {"content": "你好"}}]}),
            'data: ' + json.dumps({"choices": [], "usage": {"total_tokens": 3}}),
            "data: [DONE]",
        ):
            yield line


class _Client:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *_args, **_kwargs):
        return _Response()


@pytest.mark.asyncio
async def test_usage_only_chunk_does_not_crash_stream_parser(monkeypatch):
    monkeypatch.setattr("app.api.chat.httpx.AsyncClient", _Client)
    settings = type(
        "Settings",
        (),
        {
            "openai_base_url": "https://example.invalid/v1",
            "openai_api_key": type("Key", (), {"get_secret_value": lambda self: "test"})(),
            "chat_model": "test-model",
        },
    )()
    chunks = [item async for item in _upstream_events(settings, [], 1)]
    assert chunks == ["你好"]
