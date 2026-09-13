"""Local MCP server for TripMate's external travel-data tools."""

from mcp.server.fastmcp import FastMCP

from custom_weather_mcp_server import get_current_weather, get_forecast
from tools.flight_tool import AIRPORTS, parse_route, search_flights as search_flight_data
from tools.tavily_tool import tavily_search

mcp = FastMCP("TripMate Travel Data")


@mcp.tool()
def search_flights(query: str, limit: int = 5) -> str:
    """Return live flight-status information for a natural-language route."""
    return search_flight_data(query, limit=max(1, min(limit, 10)))


@mcp.tool()
def search_hotels(query: str) -> str:
    """Return current hotel search results with source links."""
    return tavily_search(query)


@mcp.tool()
def resolve_route(query: str) -> dict[str, str | None]:
    """Resolve a natural-language trip request to departure and arrival IATA codes."""
    departure, arrival = parse_route(query)
    return {"departure_iata": departure, "arrival_iata": arrival}


@mcp.tool()
def destination_weather(query: str) -> dict:
    """Return current weather and a short forecast for the trip destination."""
    _, arrival = parse_route(query)
    if not arrival or arrival not in AIRPORTS:
        raise ValueError("The destination could not be resolved to an airport.")
    airport = AIRPORTS[arrival]
    city = str(airport.get("city") or airport.get("name") or arrival)
    return {
        "city": city,
        "current": get_current_weather(city),
        "forecast": get_forecast(city),
    }


@mcp.resource("travel://capabilities")
def capabilities() -> str:
    """Describe the non-secret capabilities exposed by this server."""
    return "Flight status search, hotel research, route resolution, and destination weather."


if __name__ == "__main__":
    mcp.run(transport="stdio")
