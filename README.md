# Multi-Agent Travel Planner

TripMate is a supervised travel-planning application built with FastAPI,
LangGraph, and the Model Context Protocol (MCP). It collects flight and hotel
research, prepares one evidence-aware draft, and pauses for a person to approve,
revise, or reject the itinerary before it becomes final.

## Workflow

```text
Input guardrail
    -> Supervisor
        -> Flight specialist (MCP)
        -> Hotel specialist (MCP)
        -> Weather specialist (MCP)
        -> Itinerary specialist
    -> Human approval interrupt
        -> Approve -> Final response
        -> Revise  -> Itinerary specialist -> Approval interrupt
        -> Reject  -> End
```

The supervisor uses explicit state and conditional LangGraph edges. External
travel data crosses a real local stdio MCP boundary. HITL state is persisted by
the configured LangGraph checkpointer and resumed with the same `thread_id`.

## Setup

1. Install Python 3.10 or newer.
2. Create and activate a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and add your own credentials.
5. Start the app with `uvicorn app:app --reload`.
6. Open `http://127.0.0.1:8000`.

PostgreSQL is optional during local development. If `DATABASE_URL` is missing or
unreachable, approval checkpoints are stored in memory until the server restarts.

`GROQ_MODEL` is optional and defaults to `openai/gpt-oss-120b`.

`DEFAULT_ORIGIN_IATA` must be a three-letter airport code such as `DAC` or `DEL`,
not a country code such as `IND`.

## Human approval API

`POST /api/travel` creates a draft. A successful planning response has
`status: "approval_required"` and includes the interrupt payload.

Resume the checkpoint with `POST /api/travel/approval`:

```json
{
  "thread_id": "trip_...",
  "action": "approve",
  "feedback": ""
}
```

Actions are `approve`, `revise`, and `reject`. Revision feedback is incorporated
into a new draft, which pauses for approval again.

## MCP server

The stdio server is `travel_mcp_server.py` and exposes:

- `search_flights`
- `search_hotels`
- `resolve_route`
- `destination_weather`

Example host configuration:

```json
{
  "mcpServers": {
    "tripmate-travel-data": {
      "command": "C:/absolute/path/to/python.exe",
      "args": ["C:/absolute/path/to/travel_mcp_server.py"]
    }
  }
}
```

Use absolute paths and the same Python environment in which the requirements are
installed. The MCP server writes protocol messages to stdout; application logs
must go to stderr.

## Tests

Run the offline regression suite with:

```text
python -m unittest discover -s tests -v
```

Run `python test.py` separately for a live Tavily integration check. The regular
test suite does not call paid or remote APIs.

## Security

Never commit `.env`, API keys, or database connection URLs. If a credential is
committed or shared, remove it and rotate it at the provider immediately.
