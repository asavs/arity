"""Unit tests for gorkbot pluggable search engines (Stdlib & TinyFish A/B testing)."""
import unittest
from unittest.mock import MagicMock, patch

from gorkbot.tools import SandboxToolRunner
from gorkbot.types import ExecuteTool


class TestSearchEngines(unittest.TestCase):
    def test_stdlib_github_search_engine(self):
        runner = SandboxToolRunner(search_engine="stdlib")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"items": [{"full_name": "DietrichGebert/ponytail", "stargazers_count": 2500, "description": "Lazy senior dev skill", "html_url": "https://github.com/DietrichGebert/ponytail"}]}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = runner.execute(ExecuteTool(call_id="c1", name="web_search", arguments={"query": "ponytail skill"}))
            self.assertFalse(res.is_error)
            self.assertIn("DietrichGebert/ponytail", res.output)
            self.assertIn("2500", res.output)

    def test_tinyfish_search_engine_with_key(self):
        runner = SandboxToolRunner(search_engine="tinyfish", tinyfish_api_key="test_key_123")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"results": [{"title": "Ponytail Skill", "snippet": "YAGNI for AI agents", "url": "https://github.com/DietrichGebert/ponytail"}]}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = runner.execute(ExecuteTool(call_id="c2", name="web_search", arguments={"query": "ponytail skill"}))
            self.assertFalse(res.is_error)
            self.assertIn("Ponytail Skill", res.output)
            self.assertIn("YAGNI for AI agents", res.output)

    def test_tinyfish_search_engine_missing_key(self):
        runner = SandboxToolRunner(search_engine="tinyfish", tinyfish_api_key=None)
        with patch.dict("os.environ", {}, clear=True):
            res = runner.execute(ExecuteTool(call_id="c3", name="web_search", arguments={"query": "ponytail"}))
            self.assertIn("TINYFISH_API_KEY required", res.output)


if __name__ == "__main__":
    unittest.main()
