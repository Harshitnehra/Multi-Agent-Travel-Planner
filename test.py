import os

from tools.tavily_tool import tavily_search


def test_tavily_search():
    query = "best travel destinations in Japan"
    print("Testing Tavily search for:", query)

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is missing. Add it to your .env file.")

    result = tavily_search(query)

    if not result or not isinstance(result, str):
        raise AssertionError("Tavily search returned no result.")

    if "http" not in result.lower():
        raise AssertionError("Tavily result does not contain any URL output.")

    print("\n--- Tavily Result ---")
    print(result)
    print("\nTest passed: Tavily returned search results successfully.")


if __name__ == "__main__":
    test_tavily_search()
