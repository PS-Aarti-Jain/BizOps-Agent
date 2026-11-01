import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from llm.groq_client import GroqClient


def test_groq_client_init_and_defaults():
    """Verifies default configuration and initialization of GroqClient."""
    client = GroqClient(api_key="gsk_test1234567890abcdef", model="llama-3.3-70b-versatile")
    assert client.api_key == "gsk_test1234567890abcdef"
    assert client.model == "llama-3.3-70b-versatile"
    assert client.model_name == "llama-3.3-70b-versatile"
    assert client.provider_name == "groq"
    assert client.base_url == "https://api.groq.com/openai/v1"


@pytest.mark.asyncio
async def test_groq_client_missing_key():
    """Verifies that is_available returns False when GROQ_API_KEY is empty."""
    client = GroqClient(api_key="")
    available = await client.is_available()
    assert available is False
    assert client.last_status == "KEY_MISSING"
    assert "GROQ_API_KEY is not set" in client.last_error_message

    # generate_json should also return None immediately without throwing
    res = await client.generate_json(prompt="test", system_prompt="test")
    assert res is None
    assert client.last_status == "KEY_MISSING"


def test_clean_and_parse_json():
    """Verifies robust JSON extraction from various model output formats."""
    client = GroqClient(api_key="gsk_dummy")

    # Clean raw JSON
    raw = '{"thought": "test", "action": "inventory.get_stock", "action_input": {"product_id": "P1001"}}'
    assert client._clean_and_parse_json(raw)["action"] == "inventory.get_stock"

    # Markdown fenced code block
    fenced = '```json\n{"thought": "evaluating", "final_answer": "Done"}\n```'
    assert client._clean_and_parse_json(fenced)["final_answer"] == "Done"

    # Conversational text surrounding JSON
    surrounded = 'Here is the response:\n{"result": "success"}\nHope this helps!'
    assert client._clean_and_parse_json(surrounded)["result"] == "success"

    # Malformed text
    malformed = "This is not json at all."
    assert client._clean_and_parse_json(malformed) is None


@pytest.mark.asyncio
async def test_generate_json_mocked_sdk():
    """Verifies successful generation using mocked AsyncGroq SDK."""
    client = GroqClient(api_key="gsk_test_mock_key")
    mock_choice = MagicMock()
    mock_choice.message.content = '{"thought": "checking stock", "action": "google_sheets.read_data", "action_input": {}}'
    mock_response = MagicMock(choices=[mock_choice])

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create = AsyncMock(return_value=mock_response)
    client._sdk_client = mock_sdk

    result = await client.generate_json(prompt="Check sheets", system_prompt="You are BizOps")
    assert result is not None
    assert result["thought"] == "checking stock"
    assert result["action"] == "google_sheets.read_data"
    assert client.last_status == "SUCCESS"
    assert client.last_latency_ms is not None


@pytest.mark.asyncio
async def test_bounded_retries_and_error():
    """Verifies that transient errors trigger bounded retries and set status to ERROR."""
    client = GroqClient(api_key="gsk_test_key", max_retries=1)
    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create = AsyncMock(side_effect=Exception("Connection reset by peer"))
    client._sdk_client = mock_sdk

    res = await client.generate_json(prompt="Ping", system_prompt="System")
    assert res is None
    assert client.last_status == "ERROR"
    assert "Connection reset" in client.last_error_message
    # Initial attempt + 1 retry = 2 calls
    assert mock_sdk.chat.completions.create.call_count == 2

