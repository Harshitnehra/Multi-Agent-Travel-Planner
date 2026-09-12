from tavily import TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    TimeoutError as TavilyTimeoutError,
    UsageLimitExceededError,
)
import os
import requests
from dotenv import load_dotenv

load_dotenv()


def tavily_search(query):
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Hotel search unavailable: TAVILY_API_KEY is not configured."

    client = TavilyClient(api_key=api_key)

    try:
        response = client.search(
            query=query,
            max_results=5,
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        return (
            "Live hotel search is temporarily unavailable because the server "
            "cannot connect to api.tavily.com on HTTPS port 443. Continue the "
            "itinerary using general hotel guidance and clearly label it as not live-verified."
        )
    except (TavilyTimeoutError, requests.exceptions.Timeout):
        return (
            "Live hotel search timed out. Continue using general hotel guidance "
            "and clearly label it as not live-verified."
        )
    except InvalidAPIKeyError:
        return "Hotel search unavailable: the configured TAVILY_API_KEY is invalid."
    except UsageLimitExceededError:
        return "Hotel search unavailable: the Tavily API usage limit has been reached."
    except ForbiddenError:
        return "Hotel search unavailable: Tavily rejected access for this API key."
    except BadRequestError:
        return "Hotel search unavailable: Tavily rejected the search request."
    except requests.exceptions.RequestException:
        return (
            "Live hotel search is temporarily unavailable due to a network error. "
            "Continue using general hotel guidance and label it as not live-verified."
        )

    search_results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(search_results, list):
        return "Hotel search unavailable: Tavily returned an unexpected response."

    if not search_results:
        return "No live hotel search results were found for this request."

    results = []

    for i, r in enumerate(search_results, 1):
        title   = r.get("title", "Unknown")
        url     = r.get("url", "")
        snippet = r.get("content", "").strip()
        # Keep only the first 300 characters to avoid wall-of-text
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "..."

        results.append(f"{i}. **{title}**\n   {url}\n   {snippet}")

    return "\n\n".join(results)
