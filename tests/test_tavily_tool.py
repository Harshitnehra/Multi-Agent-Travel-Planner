import os
import unittest
from unittest.mock import patch

import requests

from tools.tavily_tool import tavily_search


class TavilyToolTests(unittest.TestCase):
    @patch("tools.tavily_tool.TavilyClient")
    def test_blocked_socket_returns_fallback_instead_of_raising(self, client_class):
        client_class.return_value.search.side_effect = requests.exceptions.ConnectionError(
            OSError(10013, "Socket access forbidden")
        )

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            result = tavily_search("hotels in Tokyo")

        self.assertIn("temporarily unavailable", result)
        self.assertIn("port 443", result)

    @patch("tools.tavily_tool.TavilyClient")
    def test_successful_results_are_formatted(self, client_class):
        client_class.return_value.search.return_value = {
            "results": [
                {
                    "title": "Example Hotel",
                    "url": "https://example.com/hotel",
                    "content": "A central hotel.",
                }
            ]
        }

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            result = tavily_search("hotels in Tokyo")

        self.assertIn("Example Hotel", result)
        self.assertIn("https://example.com/hotel", result)

    @patch("tools.tavily_tool.TavilyClient")
    def test_malformed_response_is_handled(self, client_class):
        client_class.return_value.search.return_value = {"unexpected": True}

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            result = tavily_search("hotels in Tokyo")

        self.assertIn("unexpected response", result)


if __name__ == "__main__":
    unittest.main()
