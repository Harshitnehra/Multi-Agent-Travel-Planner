"""Trip Workspace readiness rules and user-triggered actions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from database import AuditEvent, MonitorSnapshot, Trip, TripVersion
from domain import TripPlan, fallback_trip_plan
from mcp_client import call_mcp_tool


WorkspaceAction = Literal[
    "book_flight", "book_hotel", "check_flight", "check_weather"
]


class WorkspaceNotReady(ValueError):
    pass


class WorkspaceProviderError(RuntimeError):
    pass


def _latest_plan(trip: Trip) -> dict[str, Any]:
    version = max(trip.versions, key=lambda item: item.version_number)
    plan = dict(version.structured_plan or {})
    if not plan.get("origin") or not plan.get("destination"):
        request = str(plan.get("original_request") or trip.title)
        inferred = fallback_trip_plan(request).model_dump(mode="json")
        for key in ("title", "origin", "destination"):
            if not plan.get(key) and inferred.get(key):
                plan[key] = inferred[key]
    return plan


def _flight_input(plan: dict[str, Any]) -> dict[str, str] | None:
    for flight in plan.get("flights") or []:
        number = str(flight.get("flight_number") or "").strip()
        departure = str(flight.get("departure_at") or "").strip()
        if number and len(departure) >= 10:
            return {"flight_number": number, "flight_date": departure[:10]}
    return None


def _weather_city(plan: dict[str, Any]) -> str | None:
    destination = plan.get("destination") or {}
    return destination.get("city") or destination.get("name")


def _decoded_tool_result(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"result"}:
        value = value["result"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _flight_result(value: Any, query: dict[str, str]) -> dict[str, Any]:
    decoded = _decoded_tool_result(value)
    text = decoded if isinstance(decoded, str) else str(decoded)
    matches = []
    for block in text.split("\n\n---\n\n"):
        item: dict[str, Any] = {"departure": {}, "arrival": {}}
        section: str | None = None
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if line == "Departure:":
                section = "departure"
            elif line == "Arrival:":
                section = "arrival"
            elif line.startswith("- ") and ":" in line and section:
                key, content = line[2:].split(":", 1)
                item[section][key.strip().lower()] = content.strip()
            elif ":" in line and not line.startswith("Source:"):
                key, content = line.split(":", 1)
                item[key.strip().lower()] = content.strip()
        if item.get("flight") or item.get("status"):
            matches.append(item)
    unavailable = any(item.get("status") == "unavailable" for item in matches)
    return {
        "kind": "flight_status",
        "provider": "AviationStack",
        "query": query,
        "availability": "no_live_match" if unavailable else "available",
        "matches": matches,
        "message": (
            "The provider has no live record for this flight and date."
            if unavailable
            else None
        ),
    }


def _weather_result(value: Any, city: str) -> dict[str, Any]:
    decoded = _decoded_tool_result(value)
    if not isinstance(decoded, dict):
        return {
            "kind": "weather",
            "city": city,
            "current": {},
            "forecast": [],
            "message": str(decoded),
        }
    forecast_value = decoded.get("forecast") or {}
    forecast = (
        forecast_value.get("forecast") or []
        if isinstance(forecast_value, dict)
        else forecast_value
    )
    return {
        "kind": "weather",
        "city": decoded.get("city") or city,
        "current": decoded.get("current") or {},
        "forecast": forecast,
    }


def _positive_delay(value: Any) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else 0


def _flight_risk(result: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    severity = "high"
    for flight in result.get("matches") or []:
        number = flight.get("flight") or result.get("query", {}).get("flight_number")
        status = str(flight.get("status") or "").strip().lower()
        if status in {"cancelled", "canceled", "diverted", "incident"}:
            severity = "critical" if status in {"cancelled", "canceled"} else severity
            reasons.append(f"Flight {number} is {status}.")
        elif status in {"delayed", "delay"}:
            reasons.append(f"Flight {number} is delayed.")
        delays = [
            _positive_delay((flight.get("departure") or {}).get("delay")),
            _positive_delay((flight.get("arrival") or {}).get("delay")),
        ]
        delay = max(delays)
        if delay > 0:
            reasons.append(f"Flight {number} has a reported delay of {delay:g} minutes.")
    return {
        "detected": bool(reasons),
        "category": "flight",
        "severity": severity if reasons else "low",
        "title": "Flight disruption detected" if reasons else "No flight disruption detected",
        "reasons": list(dict.fromkeys(reasons)),
        "prompt": "Would you like to recreate this trip or keep the current plan?",
    }


def _weather_risk(result: dict[str, Any]) -> dict[str, Any]:
    rough_terms = {
        "thunderstorm", "storm", "tornado", "squall", "cyclone", "hurricane",
        "blizzard", "hail", "heavy rain", "heavy snow", "freezing rain",
        "dust", "sandstorm", "volcanic ash", "extreme",
    }
    reasons: list[str] = []
    conditions = []
    current = result.get("current") or {}
    if current.get("condition"):
        conditions.append(("Current weather", str(current["condition"])))
    for item in result.get("forecast") or []:
        if item.get("condition"):
            conditions.append((str(item.get("datetime") or "Forecast"), str(item["condition"])))
    for label, condition in conditions:
        lowered = condition.lower()
        if any(term in lowered for term in rough_terms):
            reasons.append(f"{label}: {condition}.")
    wind = current.get("wind_speed")
    try:
        if wind is not None and float(wind) >= 10.8:
            reasons.append(f"Strong wind is reported at {float(wind):g} m/s.")
    except (TypeError, ValueError):
        pass
    return {
        "detected": bool(reasons),
        "category": "weather",
        "severity": "high" if reasons else "low",
        "title": "Rough weather detected" if reasons else "No rough weather detected",
        "reasons": list(dict.fromkeys(reasons)),
        "prompt": "Would you like to recreate this trip or keep the current plan?",
    }


def workspace_payload(trip: Trip) -> dict[str, Any]:
    plan = _latest_plan(trip)
    flight = _flight_input(plan)
    city = _weather_city(plan)
    approved = trip.status in {"approved", "active"}

    def unavailable_reason(available: bool, missing_reason: str) -> str | None:
        if not approved:
            return "Approve the trip before using workspace actions."
        return None if available else missing_reason

    return {
        "trip_id": trip.id,
        "title": trip.title,
        "trip_status": trip.status,
        "version": trip.current_version,
        "actions": {
            "book_flight": {
                "enabled": approved,
                "reason": None if approved else "Approve the trip before searching flights.",
            },
            "book_hotel": {
                "enabled": approved,
                "reason": None if approved else "Approve the trip before searching hotels.",
            },
            "check_flight": {
                "enabled": approved and flight is not None,
                "reason": unavailable_reason(
                    flight is not None, "Add a flight number and departure date first."
                ),
            },
            "check_weather": {
                "enabled": approved and city is not None,
                "reason": unavailable_reason(city is not None, "Add a destination city first."),
            },
        },
        "monitoring": {
            "flight": flight,
            "weather_city": city,
        },
        "booking_defaults": {
            "flight": {
                "origin": (plan.get("origin") or {}).get("city")
                or (plan.get("origin") or {}).get("name"),
                "origin_iata": (plan.get("origin") or {}).get("iata_code"),
                "destination": (plan.get("destination") or {}).get("city")
                or (plan.get("destination") or {}).get("name"),
                "destination_iata": (plan.get("destination") or {}).get("iata_code"),
                "departure_date": plan.get("start_date"),
            }
        },
    }


class WorkspaceService:
    def __init__(self, session: Session):
        self.session = session

    def run_action(self, trip: Trip, action: WorkspaceAction) -> dict[str, Any]:
        workspace = workspace_payload(trip)
        capability = workspace["actions"][action]
        if not capability["enabled"]:
            raise WorkspaceNotReady(capability["reason"] or "This action is not available.")
        plan = _latest_plan(trip)

        if action == "check_flight":
            flight = workspace["monitoring"]["flight"]
            try:
                provider_result = call_mcp_tool("get_flight_status", flight)
            except Exception as exc:
                detail = str(exc).strip()
                if "No status was found" in detail:
                    message = detail.split("No status was found", 1)[1]
                    message = "No status was found" + message
                else:
                    message = (
                        "Flight status could not be checked. Verify the flight number, "
                        "date, and provider configuration, then try again."
                    )
                raise WorkspaceProviderError(
                    message
                ) from exc
            result = _flight_result(provider_result, flight)
            result["risk"] = _flight_risk(result)
            no_live_match = result["availability"] == "no_live_match"
            self.session.add(
                MonitorSnapshot(
                    trip=trip,
                    flight_data={"input": flight, "result": result},
                    source_status={
                        "flight": "no_live_match" if no_live_match else "available",
                        "weather": "not_checked",
                    },
                )
            )
            message = (
                f"Provider checked successfully, but no live status is available for "
                f"{flight['flight_number']} on {flight['flight_date']}."
                if no_live_match
                else f"Flight status checked for {flight['flight_number']}."
            )
            if result["risk"]["detected"]:
                message = (
                    f"Flight disruption detected for {flight['flight_number']}. "
                    "Choose whether to recreate or keep the trip."
                )
        elif action == "check_weather":
            city = workspace["monitoring"]["weather_city"]
            try:
                provider_result = call_mcp_tool("weather_for_city", {"city": city})
            except Exception as exc:
                raise WorkspaceProviderError(
                    "Weather could not be checked. Verify the provider configuration and try again."
                ) from exc
            result = _weather_result(provider_result, city)
            result["risk"] = _weather_risk(result)
            self.session.add(
                MonitorSnapshot(
                    trip=trip,
                    weather_data={"city": city, "result": result},
                    source_status={"flight": "not_checked", "weather": "available"},
                )
            )
            message = f"Weather checked for {city}."
            if result["risk"]["detected"]:
                message = (
                    f"Rough weather detected for {city}. "
                    "Choose whether to recreate or keep the trip."
                )
        else:
            booking_type = "flight" if action == "book_flight" else "hotel"
            candidates = plan.get(f"{booking_type}s") or []
            result = {"booking_type": booking_type, "candidates": candidates}
            message = f"{booking_type.title()} options are ready for the demo booking flow."

        self.session.add(
            AuditEvent(
                trip=trip,
                actor=f"user:{trip.user_id}",
                action=f"workspace.{action}",
                before_data=None,
                after_data={"result": result},
                reason="User initiated this action from the Trip Workspace.",
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.flush()
        state = "success"
        if action == "check_flight" and result.get("availability") == "no_live_match":
            state = "no_data"
        elif result.get("risk", {}).get("detected"):
            state = "risk"
        return {
            "action": action,
            "state": state,
            "message": message,
            "result": result,
            "workspace": workspace_payload(trip),
        }

    def add_flight_details(self, trip: Trip, flight_number: str, flight_date: str) -> dict:
        if trip.status not in {"approved", "active"}:
            raise WorkspaceNotReady("Approve the trip before adding flight details.")
        latest = max(trip.versions, key=lambda item: item.version_number)
        plan = _latest_plan(trip)
        normalized_flight_number = re.sub(
            r"[\s/\-_\u2010-\u2015]+", "", flight_number
        ).upper()
        if not re.fullmatch(r"[A-Z0-9]{3,10}", normalized_flight_number):
            raise ValueError("Enter a flight number such as AI171 or AI-171.")
        flight = {
            "flight_number": normalized_flight_number,
            "departure_at": f"{flight_date}T00:00:00+00:00",
            "origin": plan.get("origin"),
            "destination": plan.get("destination"),
            "status": "selected",
            "source": "User-provided monitoring details",
            "source_verified": False,
        }
        plan["flights"] = [flight, *(plan.get("flights") or [])]
        validated = TripPlan.model_validate(plan).model_dump(mode="json")
        next_version = trip.current_version + 1
        trip.versions.append(
            TripVersion(
                version_number=next_version,
                structured_plan=validated,
                rendered_itinerary=latest.rendered_itinerary,
                change_reason="Added flight monitoring details",
                created_by=f"user:{trip.user_id}",
            )
        )
        trip.current_version = next_version
        self.session.add(
            AuditEvent(
                trip=trip,
                actor=f"user:{trip.user_id}",
                action="workspace.flight_details_added",
                before_data=None,
                after_data={"flight_number": flight["flight_number"], "flight_date": flight_date},
                reason="User supplied the minimum fields needed for flight monitoring.",
            )
        )
        self.session.flush()
        return workspace_payload(trip)
