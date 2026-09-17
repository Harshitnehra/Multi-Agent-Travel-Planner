from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

from config import get_settings


mcp = FastMCP("Weather MCP Server")


def _get_api_key() -> str:
    api_key = get_settings().openweather_api_key
    if not api_key:
        raise RuntimeError(
            "OPENWEATHER_API_KEY is missing "
            "from the project .env file."
        )

    return api_key


def _request_json(
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    try:
        response = requests.get(
            url,
            params=params,
            timeout=settings.request_timeout_seconds,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RuntimeError("OpenWeather could not be reached or rejected the request.") from None


@mcp.tool()
def get_current_weather(
    city: str,
) -> dict[str, Any]:
    """Return the current weather for a city."""

    city = city.strip()

    if not city:
        raise ValueError(
            "city cannot be empty"
        )

    data = _request_json(
        "https://api.openweathermap.org/data/2.5/weather",
        {
            "q": city,
            "appid": _get_api_key(),
            "units": "metric",
        },
    )

    return {
        "city": data["name"],
        "temperature_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],
        "condition": data["weather"][0]["description"],
        "wind_speed": data["wind"]["speed"],
    }


@mcp.tool()
def get_forecast(
    city: str,
) -> dict[str, Any]:
    """
    Return the first five three-hour
    forecast entries for a city.
    """

    city = city.strip()

    if not city:
        raise ValueError(
            "city cannot be empty"
        )

    data = _request_json(
        "https://api.openweathermap.org/data/2.5/forecast",
        {
            "q": city,
            "appid": _get_api_key(),
            "units": "metric",
        },
    )

    forecast = [
        {
            "datetime": item["dt_txt"],
            "temperature_c": item["main"]["temp"],
            "condition": item["weather"][0]["description"],
        }
        for item in data.get("list", [])[:5]
    ]

    return {
        "city": data.get(
            "city",
            {},
        ).get(
            "name",
            city,
        ),
        "forecast": forecast,
    }


if __name__ == "__main__":
    # mcp_client.py launches this as a stdio subprocess.
    mcp.run(
        transport="stdio",
    )
