from __future__ import annotations

from unittest.mock import MagicMock, patch

from session_mem.llm.qwen_client import QwenClient


def test_chat_completion_skips_json_schema_when_not_supported():
    client = QwenClient(
        api_key="test",
        base_url="http://localhost/v1",
        model="qwen-test",
        supports_json_schema=False,
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "test", "schema": {"type": "object"}},
    }

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="ok"))]

    with patch.object(
        client._client.chat.completions, "create", return_value=mock_resp
    ) as mock_create:
        result = client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            response_format=response_format,
        )

    assert result == "ok"
    call_kwargs = mock_create.call_args.kwargs
    assert "response_format" not in call_kwargs
    assert call_kwargs["model"] == "qwen-test"


def test_chat_completion_allows_model_override_via_kwargs():
    client = QwenClient(
        api_key="test",
        base_url="http://localhost/v1",
        model="qwen-test",
        supports_json_schema=True,
    )

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="override"))]

    with patch.object(
        client._client.chat.completions, "create", return_value=mock_resp
    ) as mock_create:
        result = client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )

    assert result == "override"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"


def test_chat_completion_keeps_json_schema_when_supported():
    client = QwenClient(
        api_key="test",
        base_url="http://localhost/v1",
        model="qwen-test",
        supports_json_schema=True,
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "test", "schema": {"type": "object"}},
    }

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="ok"))]

    with patch.object(
        client._client.chat.completions, "create", return_value=mock_resp
    ) as mock_create:
        result = client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            response_format=response_format,
        )

    assert result == "ok"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs.get("response_format") == response_format
