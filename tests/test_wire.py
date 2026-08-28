"""Unit tests for gorkbot direct wire providers and fallback seams."""
import unittest
from unittest.mock import MagicMock, patch

from gorkbot.handlers import CLIModelProvider
from gorkbot.ledger import Seat
from gorkbot.types import CallModel, ModelCompleted, ModelFailed
from gorkbot.wire import (
    AntigravityWireProvider,
    CodexWireProvider,
    FallbackModelProvider,
    GrokWireProvider,
    create_wire_model_provider,
)



class TestWireProviders(unittest.TestCase):
    def test_grok_wire_provider_success(self):
        provider = GrokWireProvider(access_token="mock_token", model="grok-4.5")
        effect = CallModel(
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.7,
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices": [{"message": {"content": "Hello from Grok!"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 4}}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertEqual(res.content, "Hello from Grok!")
            self.assertEqual(res.seat_id, "wire:grok:grok-4.5")

    def test_codex_wire_provider_sse_stream(self):
        provider = CodexWireProvider(access_token="mock_token", account_id="mock_acc", model="gpt-5.6-sol")
        effect = CallModel(
            messages=[{"role": "user", "content": "hello"}],
        )

        sse_lines = [
            b'data: {"type": "response.created", "response": {}}\n',
            b'data: {"type": "response.output_text.delta", "delta": "Hello "}\n',
            b'data: {"type": "response.output_text.delta", "delta": "world!"}\n',
            b'data: {"type": "response.done", "response": {"usage": {"input_tokens": 10, "output_tokens": 5}}}\n',
            b'data: [DONE]\n',
        ]

        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(sse_lines)
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertEqual(res.content, "Hello world!")
            self.assertEqual(res.usage["prompt_tokens"], 10)
            self.assertEqual(res.usage["completion_tokens"], 5)


    def test_antigravity_wire_provider_gemini(self):
        provider = AntigravityWireProvider(
            access_token="mock_token",
            project_id="mock_proj",
            model="gemini-3.6-flash",
        )
        effect = CallModel(
            messages=[{"role": "user", "content": "hello"}],
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"response": {"candidates": [{"content": {"parts": [{"text": "Hello from Gemini via AGY!"}]}}], "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 6, "totalTokenCount": 14}}}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertEqual(res.content, "Hello from Gemini via AGY!")
            self.assertEqual(res.seat_id, "wire:antigravity:gemini-3-flash-agent")
            self.assertEqual(res.usage["total_tokens"], 14)

    def test_antigravity_wire_provider_claude(self):
        provider = AntigravityWireProvider(
            access_token="mock_token",
            project_id="mock_proj",
            model="claude-sonnet-4-6",
        )
        effect = CallModel(
            messages=[{"role": "user", "content": "hello"}],
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"response": {"candidates": [{"content": {"parts": [{"text": "Hello from Claude via AGY!"}]}}], "usageMetadata": {"totalTokenCount": 20}}}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertEqual(res.content, "Hello from Claude via AGY!")
            self.assertEqual(res.seat_id, "wire:antigravity:claude-sonnet-4-6")
    def test_fallback_provider_swaps_on_failure(self):
        primary = MagicMock()
        primary.call.return_value = ModelFailed(error="HTTP 401 Unauthorized", seat_id="primary", retryable=True)

        fallback = MagicMock()
        fallback.call.return_value = ModelCompleted(content="Recovered by fallback", usage={}, seat_id="fallback")

        fallback_wrapper = FallbackModelProvider(primary=primary, fallback=fallback)
        effect = CallModel(messages=[{"role": "user", "content": "hi"}])

        res = fallback_wrapper.call(effect)
        self.assertIsInstance(res, ModelCompleted)
        self.assertEqual(res.content, "Recovered by fallback")
        primary.call.assert_called_once()
        fallback.call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
