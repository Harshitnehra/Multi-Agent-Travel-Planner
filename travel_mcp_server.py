"""Local MCP server for TripMate's external travel-data tools."""

from mcp.server.fastmcp import FastMCP

from custom_weather_mcp_server import get_current_weather, get_forecast
from tools.flight_tool import (
    AIRPORTS,
    get_flight_status as get_flight_status_data,
    parse_route,
    search_flights as search_flight_data,
)
from tools.tavily_tool import tavily_search

mcp = FastMCP("TripMate Travel Data")


@mcp.tool()
def search_flights(query: str, limit: int = 5) -> str:
    """Return live flight-status information for a natural-language route."""
    return search_flight_data(query, limit=max(1, min(limit, 10)))


@mcp.tool()
def get_flight_status(flight_number: str, flight_date: str) -> str:
    """Return exact flight status for a flight number and ISO travel date."""
    return get_flight_status_data(flight_number, flight_date)


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


@mcp.tool()
def weather_for_city(city: str) -> dict:
    """Return weather using a destination city, independent of any flight."""
    normalized_city = city.strip()
    if not normalized_city:
        raise ValueError("A destination city is required.")
    return {
        "city": normalized_city,
        "current": get_current_weather(normalized_city),
        "forecast": get_forecast(normalized_city),
    }


@mcp.resource("travel://capabilities")
def capabilities() -> str:
    """Describe the non-secret capabilities exposed by this server."""
    return "Exact flight status, flight search, hotel research, route resolution, and city weather."


if __name__ == "__main__":
    mcp.run(transport="stdio")
