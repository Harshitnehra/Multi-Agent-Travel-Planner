import asyncio
import unittest

from travel_mcp_server import mcp


class MCPServerTests(unittest.TestCase):
    def test_expected_tools_are_registered(self):
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "search_flights",
                "get_flight_status",
                "search_hotels",
                "resolve_route",
                "destination_weather",
                "weather_for_city",
            },
        )

    def test_capabilities_resource_is_registered(self):
        resources = asyncio.run(mcp.list_resources())
        self.assertIn("travel://capabilities", {str(item.uri) for item in resources})


if __name__ == "__main__":
    unittest.main()
