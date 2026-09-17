import logging
import re
import uuid
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from backend import resume_travel_plan, start_travel_plan, strip_structured_plan_section
from config import get_settings
from database import AuditEvent, MonitorSnapshot, User, create_schema, get_session
from repositories import TripRepository, trip_detail, trip_summary
from services import (
    AuthService,
    DemoBookingConflict,
    DemoBookingNotFound,
    DemoBookingService,
    DemoOfferExpired,
    EmailAlreadyRegistered,
    InvalidCredentials,
    WorkspaceNotReady,
    WorkspaceProviderError,
    WorkspaceService,
    workspace_payload,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
settings = get_settings()
SESSION_COOKIE = "tripmate_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Ensure declared tables exist; Alembic still owns managed schema upgrades."""
    try:
        await run_in_threadpool(create_schema)
    except SQLAlchemyError:
        logger.exception("Application database initialization failed")
    yield


app = FastAPI(
    title=settings.app_name,
    description="Supervised LangGraph travel planning with MCP tools and human approval.",
    version=settings.app_version,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, _exc: SQLAlchemyError):
    logger.exception("Application database request failed")
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "error": (
                "The account database is unavailable. Check APP_DATABASE_URL or "
                "DATABASE_URL, then restart TripMate."
            ),
        },
    )


class TravelRequest(BaseModel):
    message: str = Field(max_length=2_000)
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    action: Literal["approve", "revise", "reject"]
    feedback: str = Field(default="", max_length=1_000)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoFlightDetails(StrictRequest):
    passenger_name: str = Field(min_length=2, max_length=120)
    origin: str = Field(min_length=2, max_length=120)
    origin_iata: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    destination: str = Field(min_length=2, max_length=120)
    destination_iata: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    departure_date: date
    passengers: int = Field(default=1, ge=1, le=9)
    cabin: Literal["economy", "premium_economy", "business"] = "economy"


class DemoOfferSearchRequest(StrictRequest):
    booking_type: Literal["flight", "hotel"]
    flight_details: DemoFlightDetails | None = None


class DemoPaymentRequest(StrictRequest):
    idempotency_key: str = Field(min_length=16, max_length=100)


class DemoPaymentSimulationRequest(StrictRequest):
    event_id: str = Field(min_length=8, max_length=100)
    outcome: Literal["success", "failed", "cancelled"]


class RazorpayVerificationRequest(StrictRequest):
    event_id: str = Field(min_length=8, max_length=100)
    razorpay_order_id: str = Field(min_length=6, max_length=200)
    razorpay_payment_id: str = Field(min_length=6, max_length=200)
    razorpay_signature: str = Field(min_length=32, max_length=256)


class FlightDetailsRequest(StrictRequest):
    flight_number: str = Field(min_length=3, max_length=10, pattern=r"^[A-Z0-9]+$")
    flight_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("flight_number", mode="before")
    @classmethod
    def normalize_flight_number(cls, value):
        if not isinstance(value, str):
            return value
        normalized = re.sub(r"[\s/\-_\u2010-\u2015]+", "", value).upper()
        if not re.fullmatch(r"[A-Z0-9]+", normalized):
            raise ValueError("Enter a flight number such as AI171 or AI-171.")
        return normalized


class RiskDecisionRequest(StrictRequest):
    decision: Literal["recreate", "skip"]


def _recovery_provider_failed(result: dict) -> bool:
    itinerary = str(result.get("itinerary") or result.get("answer") or "").lower()
    plan = result.get("structured_plan") or {}
    return (
        result.get("status") == "guardrail_rejected"
        or "provider is temporarily unavailable" in itinerary
        or str(plan.get("summary") or "").startswith(
            "Structured details could not be extracted"
        )
    )


def _local_recovery_result(latest, risk: dict) -> dict:
    """Create an honest replacement draft when external replanning is unavailable."""
    plan = deepcopy(latest.structured_plan or {})
    category = risk.get("category", "trip")
    reasons = risk.get("reasons") or [risk.get("title", "A trip risk was detected.")]
    reason_text = "; ".join(reasons)
    plan["title"] = f"Recovery plan — {plan.get('title') or 'Trip'}"[:200]
    existing_summary = str(plan.get("summary") or "").strip()
    plan["summary"] = (
        f"Replacement draft created after a {category} risk. {reason_text} "
        f"{existing_summary}"
    )[:2000]
    assumptions = list(plan.get("assumptions") or [])
    assumptions.append(f"Recovery required because: {reason_text}"[:500])
    plan["assumptions"] = list(dict.fromkeys(assumptions))[:30]
    missing = list(plan.get("missing_information") or [])

    recovery_actions: list[str] = []
    readable = strip_structured_plan_section(latest.rendered_itinerary)
    if category == "flight":
        match = re.search(r"\bFlight\s+([A-Z0-9]+)\b", reason_text, re.IGNORECASE)
        affected_number = match.group(1).upper() if match else None
        if affected_number:
            plan["flights"] = [
                flight for flight in (plan.get("flights") or [])
                if str(flight.get("flight_number") or "").upper() != affected_number
            ]
            readable = re.sub(
                rf"\b{re.escape(affected_number)}\b",
                "alternative flight (selection pending)",
                readable,
                flags=re.IGNORECASE,
            )
            missing.append(f"Select and confirm an alternative to flight {affected_number}")
            recovery_actions.append(
                f"Do not use flight {affected_number}; select a replacement before travel."
            )
        else:
            missing.append("Select and confirm an alternative flight")
            recovery_actions.append("Select a replacement flight before travel.")
    else:
        missing.append("Reconfirm outdoor activities against the latest forecast")
        recovery_actions.extend(
            [
                "Move weather-sensitive activities indoors or to a safer time slot.",
                "Recheck the forecast before departure and before each outdoor activity.",
            ]
        )
    plan["missing_information"] = list(dict.fromkeys(missing))[:30]
    actions = "\n".join(f"- {item}" for item in recovery_actions)
    reasons_markdown = "\n".join(f"- {item}" for item in reasons)
    itinerary = (
        "# Replacement trip draft\n\n"
        "## Why this plan was recreated\n\n"
        f"{reasons_markdown}\n\n"
        "## Required recovery changes\n\n"
        f"{actions}\n\n"
        "## Updated trip plan\n\n"
        f"{readable}"
    )
    thread_id = f"recovery_{uuid.uuid4().hex}"
    return {
        "thread_id": thread_id,
        "status": "approval_required",
        "answer": itinerary,
        "itinerary": itinerary,
        "structured_plan": plan,
        "approval": {
            "kind": "trip_plan_approval",
            "question": "Approve this replacement itinerary, request a revision, or reject it.",
            "allowed_actions": ["approve", "revise", "reject"],
        },
    }


def _token_from_request(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=settings.app_environment.lower() == "production",
        samesite="lax",
        path="/",
    )


def current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    user = AuthService(session).authenticate(_token_from_request(request))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def _user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/auth/register", status_code=201)
async def register(
    request_data: RegisterRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    try:
        user, token = AuthService(session).register(
            request_data.email, request_data.password, request_data.display_name
        )
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _set_session_cookie(response, token)
    return {"success": True, "user": _user_payload(user)}


@app.post("/api/auth/login")
async def login(
    request_data: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    try:
        user, token = AuthService(session).login(request_data.email, request_data.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, token)
    return {"success": True, "user": _user_payload(user)}


@app.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    AuthService(session).logout(_token_from_request(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"success": True}


@app.get("/api/auth/me")
async def auth_me(user: User = Depends(current_user)):
    return {"success": True, "user": _user_payload(user)}


@app.post("/api/travel")
async def travel_planner(
    request_data: TravelRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    message = request_data.message.strip()
    if not message:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Enter a travel request."},
        )

    try:
        result = await run_in_threadpool(
            start_travel_plan,
            message,
            None,
        )
    except Exception:
        logger.exception("Travel workflow failed")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "The travel plan could not be generated. Check the server logs.",
            },
        )

    if result["status"] == "guardrail_rejected":
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": result["answer"], **result},
        )
    trip = TripRepository(session).create_from_workflow(user.id, message, result)
    return JSONResponse(content={"success": True, "trip_id": trip.id, **result})


@app.post("/api/travel/approval")
async def travel_approval(
    request_data: ApprovalRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repository = TripRepository(session)
    trip = repository.get_by_thread(request_data.thread_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        result = await run_in_threadpool(
            resume_travel_plan,
            request_data.thread_id,
            request_data.action,
            request_data.feedback.strip(),
        )
    except Exception:
        logger.warning("Workflow checkpoint unavailable; restoring from saved trip", exc_info=True)
        latest = max(trip.versions, key=lambda item: item.version_number)
        if request_data.action == "revise":
            original = next(
                (item.content for item in trip.messages if item.message_type == "request"),
                latest.structured_plan.get("original_request", trip.title),
            )
            revised_request = f"{original}\n\nRevision requested: {request_data.feedback.strip()}"
            result = await run_in_threadpool(start_travel_plan, revised_request, None)
            trip.planning_thread_id = result["thread_id"]
        else:
            approved = request_data.action == "approve"
            result = {
                "thread_id": trip.planning_thread_id,
                "status": "completed" if approved else "rejected",
                "answer": latest.rendered_itinerary if approved else "The draft was rejected.",
                "itinerary": latest.rendered_itinerary,
                "structured_plan": latest.structured_plan,
                "approval": None,
            }
    repository.apply_workflow_result(
        trip,
        result,
        action=request_data.action,
        feedback=request_data.feedback.strip(),
    )
    return JSONResponse(content={"success": True, "trip_id": trip.id, **result})


@app.get("/api/trips")
async def list_trips(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trips = TripRepository(session).list_owned(user.id)
    return {"success": True, "trips": [trip_summary(trip) for trip in trips]}


@app.get("/api/trips/{trip_id}")
async def get_trip(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return {"success": True, "trip": trip_detail(trip)}


@app.get("/api/trips/{trip_id}/workspace")
async def get_trip_workspace(
    trip_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return {"success": True, "workspace": workspace_payload(trip)}


@app.post("/api/trips/{trip_id}/workspace/actions/{action}")
async def run_workspace_action(
    trip_id: str,
    action: Literal[
        "book_flight", "book_hotel", "check_flight", "check_weather"
    ],
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        result = await run_in_threadpool(WorkspaceService(session).run_action, trip, action)
    except WorkspaceNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, **result}


@app.post("/api/trips/{trip_id}/workspace/flight-details")
async def add_workspace_flight_details(
    trip_id: str,
    request_data: FlightDetailsRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        workspace = WorkspaceService(session).add_flight_details(
            trip, request_data.flight_number, request_data.flight_date
        )
    except (WorkspaceNotReady, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "workspace": workspace}


@app.post("/api/trips/{trip_id}/workspace/risk-decision")
async def decide_workspace_risk(
    trip_id: str,
    request_data: RiskDecisionRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repository = TripRepository(session)
    trip = repository.get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    snapshot = session.scalar(
        select(MonitorSnapshot)
        .where(MonitorSnapshot.trip_id == trip.id)
        .order_by(MonitorSnapshot.checked_at.desc())
    )
    if snapshot and (snapshot.source_status or {}).get("risk_decision"):
        raise HTTPException(status_code=409, detail="This risk has already been handled.")
    candidates = [] if snapshot is None else [
        (snapshot.flight_data or {}).get("result", {}).get("risk"),
        (snapshot.weather_data or {}).get("result", {}).get("risk"),
    ]
    risk = next((item for item in candidates if item and item.get("detected")), None)
    if risk is None:
        raise HTTPException(status_code=409, detail="No unresolved trip risk is available.")

    reasons = "; ".join(risk.get("reasons") or [risk.get("title", "Trip risk")])
    if request_data.decision == "skip":
        snapshot.source_status = {
            **(snapshot.source_status or {}),
            "risk_decision": "skip",
        }
        session.add(
            AuditEvent(
                trip=trip,
                actor=f"user:{user.id}",
                action="workspace.risk_skipped",
                before_data={"risk": risk},
                after_data={"decision": "keep_current_trip"},
                reason="User chose to keep the current approved trip after a risk warning.",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
        return {
            "success": True,
            "decision": "skip",
            "message": "Current trip kept. The risk warning was recorded in the audit history.",
        }

    latest = max(trip.versions, key=lambda item: item.version_number)
    original_request = str(
        (latest.structured_plan or {}).get("original_request") or trip.title
    )
    current_plan_excerpt = strip_structured_plan_section(
        latest.rendered_itinerary
    )[:900]
    recovery_request = (
        f"Recovery required because a {risk['category']} risk was detected: {reasons}\n\n"
        "Create a genuinely changed replacement itinerary. Do not recommend the disrupted "
        "flight or unsafe activity again. Preserve unaffected dates, destination, budget, "
        "travelers and preferences. Put the recovery changes first and explain them clearly.\n\n"
        f"Original request: {original_request}\n\n"
        f"Current approved plan excerpt:\n{current_plan_excerpt}"
    )
    try:
        result = await run_in_threadpool(start_travel_plan, recovery_request, None)
    except Exception:
        logger.exception(
            "External risk recovery planning failed; using the saved-plan fallback"
        )
        result = _local_recovery_result(latest, risk)
    same_plan = (
        (result.get("itinerary") or result.get("answer")) == latest.rendered_itinerary
        and (result.get("structured_plan") or {}) == (latest.structured_plan or {})
    )
    if _recovery_provider_failed(result) or same_plan:
        result = _local_recovery_result(latest, risk)
    trip.planning_thread_id = result["thread_id"]
    repository.apply_workflow_result(
        trip,
        result,
        action="revise",
        feedback=f"Recreated after {risk['category']} risk: {reasons}"[:1000],
    )
    snapshot.source_status = {
        **(snapshot.source_status or {}),
        "risk_decision": "recreate",
    }
    session.add(
        AuditEvent(
            trip=trip,
            actor=f"user:{user.id}",
            action="workspace.trip_recreated_for_risk",
            before_data={"risk": risk},
            after_data={"version": trip.current_version, "thread_id": result["thread_id"]},
            reason="User requested a replacement draft after a detected trip risk.",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return {"success": True, "decision": "recreate", "trip_id": trip.id, **result}


def _raise_demo_error(exc: Exception) -> None:
    if isinstance(exc, DemoBookingNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, DemoOfferExpired):
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/trips/{trip_id}/demo-offers/search")
async def search_demo_offers(
    trip_id: str,
    request_data: DemoOfferSearchRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        offers = await run_in_threadpool(
            DemoBookingService(session).search_offers,
            trip,
            request_data.booking_type,
            (
                request_data.flight_details.model_dump(mode="json")
                if request_data.flight_details
                else None
            ),
        )
    except DemoBookingConflict as exc:
        _raise_demo_error(exc)
    return {"success": True, "demo": True, "offers": offers}


@app.post("/api/trips/{trip_id}/demo-offers/{booking_id}/hold")
async def hold_demo_offer(
    trip_id: str,
    booking_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        booking = await run_in_threadpool(
            DemoBookingService(session).hold_offer, trip, booking_id
        )
    except (DemoBookingNotFound, DemoBookingConflict, DemoOfferExpired) as exc:
        _raise_demo_error(exc)
    return {"success": True, "demo": True, "booking": booking}


@app.post("/api/trips/{trip_id}/demo-bookings/{booking_id}/payments")
async def create_demo_payment(
    trip_id: str,
    booking_id: str,
    request_data: DemoPaymentRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        payment = await run_in_threadpool(
            DemoBookingService(session).create_payment,
            trip,
            booking_id,
            request_data.idempotency_key,
        )
    except (DemoBookingNotFound, DemoBookingConflict, DemoOfferExpired) as exc:
        _raise_demo_error(exc)
    return {"success": True, "demo": True, "payment": payment}


@app.post(
    "/api/trips/{trip_id}/demo-bookings/{booking_id}/payments/{payment_id}/simulate"
)
async def simulate_demo_payment(
    trip_id: str,
    booking_id: str,
    payment_id: str,
    request_data: DemoPaymentSimulationRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        result = await run_in_threadpool(
            DemoBookingService(session).simulate_payment,
            trip,
            booking_id,
            payment_id,
            request_data.event_id,
            request_data.outcome,
        )
    except (DemoBookingNotFound, DemoBookingConflict, DemoOfferExpired) as exc:
        _raise_demo_error(exc)
    return {"success": True, "demo": True, **result}


@app.post(
    "/api/trips/{trip_id}/demo-bookings/{booking_id}/payments/{payment_id}/verify"
)
async def verify_razorpay_test_payment(
    trip_id: str,
    booking_id: str,
    payment_id: str,
    request_data: RazorpayVerificationRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    trip = TripRepository(session).get_owned(trip_id, user.id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    try:
        result = await run_in_threadpool(
            DemoBookingService(session).verify_razorpay_payment,
            trip,
            booking_id,
            payment_id,
            razorpay_order_id=request_data.razorpay_order_id,
            razorpay_payment_id=request_data.razorpay_payment_id,
            razorpay_signature=request_data.razorpay_signature,
            event_id=request_data.event_id,
        )
    except (DemoBookingNotFound, DemoBookingConflict, DemoOfferExpired) as exc:
        _raise_demo_error(exc)
    return {"success": True, "demo": True, **result}


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "workflow": "guardrails -> supervisor -> specialists -> human approval",
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
