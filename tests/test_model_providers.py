"""Unit tests for Arity ModelProvider implementations."""
import unittest
from unittest.mock import MagicMock, patch

from arity.handlers import (
    CLIModelProvider,
    GeminiModelProvider,
    OpenAIModelProvider,
    create_default_model_provider,
    create_model_provider,
)
from arity.ledger import Seat
from arity.types import CallModel, ModelCompleted, ModelFailed


class TestModelProviders(unittest.TestCase):
    def test_gemini_provider_message_formatting(self):
        provider = GeminiModelProvider(api_key="mock-key", model="gemini-3.6-flash")
        effect = CallModel(
            messages=[
                {"role": "system", "content": "You are a helpful bot."},
                {"role": "user", "content": "Hello!"},
            ],
            temperature=0.5,
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"candidates": [{"content": {"parts": [{"text": "Hello user!"}]}, "finishReason": "STOP"}],'
            b'"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}'
        )
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertEqual(res.content, "Hello user!")
            self.assertEqual(res.usage["prompt_tokens"], 10)
            self.assertEqual(res.seat_id, "gemini:gemini-3.6-flash")

    def test_cli_provider_codex_execution(self):
        provider = CLIModelProvider(harness="codex", model="gpt-5.6-sol")
        effect = CallModel(
            messages=[{"role": "user", "content": "test prompt"}],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "2026-08-28 LOG\nHello from Codex!\ntokens used\n100\nHello from Codex!"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = provider.call(effect)
            self.assertIsInstance(res, ModelCompleted)
            self.assertIn("Hello from Codex!", res.content)
            self.assertEqual(res.seat_id, "codex:gpt-5.6-sol")

    def test_create_model_provider_factory(self):
        seat_gemini = Seat(id="s1", provider="gemini", endpoint="", model="gemini-3.6-flash", api_key="k1")
        p1 = create_model_provider(seat_gemini)
        self.assertIsInstance(p1, GeminiModelProvider)

        seat_claude = Seat(id="s2", provider="claude", endpoint="", model="claude-3-7-sonnet")
        p2 = create_model_provider(seat_claude)
        self.assertIsInstance(p2, CLIModelProvider)

        seat_custom = Seat(id="s3", provider="custom-api", endpoint="https://api.openai.com/v1", model="gpt-4o")
        p3 = create_model_provider(seat_custom)
        self.assertIsInstance(p3, OpenAIModelProvider)

if __name__ == "__main__":
    unittest.main()
