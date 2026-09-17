# Multi-Agent Travel Planner

TripMate is a supervised travel-planning application built with FastAPI,
LangGraph, and the Model Context Protocol (MCP). It collects flight and hotel
research, prepares one evidence-aware draft, and pauses for a person to approve,
revise, or reject the itinerary before it becomes final.

## Workflow

For a package-by-package implementation audit, current completion status, and
remaining roadmap, see [PROJECT_WORKFLOW.md](PROJECT_WORKFLOW.md).

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

## Project structure

Runtime configuration is centralized in `config/settings.py`. The package
boundaries for the next development phases are:

- `domain`: framework-independent trip and booking types
- `database`: application-owned database connections and models
- `repositories`: persistence interfaces
- `services`: application orchestration
- `providers`: external and simulated provider adapters
- `guardian`: monitoring, risk, and recovery workflows
- `chat`: trip-scoped chat and intent routing

These packages intentionally contain no Guardian business logic yet. Phase 1
establishes ownership boundaries while preserving the existing planning API.

## Application database

TripMate keeps application-owned data separate from LangGraph checkpoint data.
SQLAlchemy models cover users, trips, itinerary versions, chat messages,
bookings, payment orders, monitoring snapshots, risks, recovery proposals, and
audit events. `APP_DATABASE_URL` can point at a separate database; otherwise it
uses `DATABASE_URL`, then falls back to `tripmate.db` for local development.

Create or update the schema with:

```text
alembic upgrade head
```

Production and shared environments should use PostgreSQL. SQLite is intended
for local development and automated tests.

## Structured trip plans

The itinerary specialist returns one validated result containing both the
Markdown document used by the current UI and a structured `TripPlan`. Domain
models in `domain/trip_models.py` cover locations, flights, hotel stays,
activities, dates, costs, assumptions, and missing information. Unknown booking
details remain null and suggested items are never represented as confirmed
bookings. API responses expose the data as `structured_plan` for later trip
activation and persistence.

## Accounts and resumable trips

Phase 4 adds local, database-backed accounts without requiring an external auth
provider. Passwords are stored as salted PBKDF2 hashes and sign-in creates a
revocable, 30-day session in an HttpOnly cookie. Run `alembic upgrade head` to
create the `user_sessions` table before starting the updated app.

Planning and approval routes require a signed-in user. A generated draft is
saved immediately as a trip with an immutable version, the original request,
and its chat history. Later revisions create new versions instead of replacing
old plans. Users can restore their workspace through:

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/trips`
- `GET /api/trips/{trip_id}`

Trip access is always scoped to the authenticated owner. The browser UI includes
registration, login, logout, saved-trip listing, and reopening a paused plan.
PostgreSQL remains recommended for durable LangGraph checkpoints; when a local
in-memory checkpoint is unavailable after restart, approve/reject is restored
from the saved version and a requested revision starts a replacement checkpoint.

## Trip Workspace

An approved trip now opens an ownership-protected workspace with Book Flight,
Book Hotel, Skip Booking, Check Flight Status, and Check Weather actions. Booking
buttons prepare the selected structured candidates for the later demo checkout
phase; Skip Booking is recorded in the audit history without disabling monitoring.

Workspace availability is calculated by the server, not guessed by the browser.
Flight status requires both a flight number and departure date. Weather requires
only a destination city, so it remains available when there is no selected or
booked flight. Manual monitoring results are saved as `monitor_snapshots`, and
all successful workspace actions create an `audit_events` record.

The browser shows a loading, success, or retryable error state on every workspace
action. The API routes are:

- `GET /api/trips/{trip_id}/workspace`
- `POST /api/trips/{trip_id}/workspace/actions/{action}`

Exact flight lookup uses the existing AviationStack configuration. City weather
uses the existing OpenWeather configuration; no new paid API is required.

## Demo booking and Razorpay Test Mode

Phase 6 adds an explicitly simulated flight and hotel booking lifecycle. An
approved trip can generate three local demo offers, select one, and hold it for
15 minutes. Every response and screen carries `demo: true` and the notice that
no real reservation or charge exists. An expired offer or hold cannot enter the
payment flow.

Phase 7 supports two safe learning modes. With no Razorpay credentials it uses
the local success/failed/cancelled simulator. When `RAZORPAY_KEY_ID` and
`RAZORPAY_KEY_SECRET` contain Razorpay **test-mode** credentials, the server
creates a real Razorpay test order and the browser opens Standard Checkout.
Live-mode key IDs are rejected. The secret stays server-side and successful
checkout data is accepted only after HMAC-SHA256 signature verification against
the order ID stored by TripMate.

To enable Standard Checkout, create Test Mode keys in the Razorpay dashboard and
set these in `.env`:

```text
RAZORPAY_KEY_ID=rzp_test_your_public_key
RAZORPAY_KEY_SECRET=your_test_secret
```

Do not put card, CVV, UPI PIN, live keys, or the secret in browser code. Razorpay
provides test payment details inside its Test Mode checkout. With credentials
unset, the local simulator remains available for offline development.

Webhook-like events are stored in `payment_events` with a unique event ID.
Payment-to-booking ownership is checked before every transition, preventing one
payment from confirming another booking. Request models reject extra fields, so
the application does not accept card numbers, CVV, UPI credentials, Razorpay
keys, or other real payment credentials.

Apply the Phase 7 schema migration with `alembic upgrade head`. The demo APIs are:

- `POST /api/trips/{trip_id}/demo-offers/search`
- `POST /api/trips/{trip_id}/demo-offers/{booking_id}/hold`
- `POST /api/trips/{trip_id}/demo-bookings/{booking_id}/payments`
- `POST /api/trips/{trip_id}/demo-bookings/{booking_id}/payments/{payment_id}/simulate`
- `POST /api/trips/{trip_id}/demo-bookings/{booking_id}/payments/{payment_id}/verify`

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
