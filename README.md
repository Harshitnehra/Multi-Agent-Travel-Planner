# Multi-Agent Travel Planner

TripMate AI is a FastAPI and LangGraph travel planner. It combines live flight
status data, hotel web search, and a Groq language model to generate an itinerary.

## Setup

1. Install Python 3.10 or newer.
2. Create and activate a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and add your own credentials.
5. Start the app with `uvicorn app:app --reload`.
6. Open `http://127.0.0.1:8000`.

PostgreSQL is optional during local development. If `DATABASE_URL` is missing or
unreachable, conversations are stored in memory until the server restarts.

`GROQ_MODEL` is optional and defaults to `openai/gpt-oss-120b`.

`DEFAULT_ORIGIN_IATA` must be a three-letter airport code such as `DAC` or `DEL`,
not a country code such as `IND`.

## Tests

Run the offline regression suite with:

```text
python -m unittest discover -s tests -v
```

Run `python test.py` separately for a live Tavily API integration check.

## Security

Never commit `.env`, API keys, or database connection URLs. If a credential is
committed or shared, remove it and rotate it at the provider immediately.
