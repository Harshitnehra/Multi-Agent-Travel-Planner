"""Ownership-scoped persistence for resumable travel plans."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import Trip, TripMessage, TripVersion
from itinerary_formatting import strip_structured_plan_section


GENERIC_TRIP_TITLES = {
    "travel plan",
    "travel plan draft",
    "trip plan",
    "trip plan draft",
}


def _clean_history_title(value: object) -> str:
    """Turn a planning prompt into a short, chat-history style title."""
    title = " ".join(str(value or "").split()).strip(" .,:;-\n\t")
    title = title.removeprefix("Please ").removeprefix("please ")
    for prefix in ("Plan me ", "plan me ", "Plan ", "plan ", "Create ", "create "):
        if title.startswith(prefix):
            title = title[len(prefix) :]
            break
    for prefix in ("a ", "an ", "my "):
        if title.lower().startswith(prefix):
            title = title[len(prefix) :]
            break
    if len(title) > 64:
        title = title[:61].rstrip(" .,:;-") + "..."
    return title[:1].upper() + title[1:] if title else "New trip"


def history_title(plan: dict[str, Any], request_text: str = "") -> str:
    """Choose a useful saved-trip title without exposing workflow metadata."""
    proposed = " ".join(str(plan.get("title") or "").split()).strip()
    if proposed and proposed.casefold() not in GENERIC_TRIP_TITLES:
        return _clean_history_title(proposed)

    destination = plan.get("destination") or {}
    if isinstance(destination, dict):
        place = destination.get("city") or destination.get("name")
        if place:
            return _clean_history_title(f"{place} trip")

    return _clean_history_title(request_text or plan.get("original_request"))


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def workflow_status(value: str) -> str:
    return {
        "approval_required": "paused",
        "completed": "approved",
        "approved": "approved",
        "rejected": "cancelled",
    }.get(value, "paused")


class TripRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_from_workflow(
        self, user_id: str, request_text: str, result: dict[str, Any]
    ) -> Trip:
        plan = result.get("structured_plan") or {}
        trip = Trip(
            user_id=user_id,
            planning_thread_id=result["thread_id"],
            title=history_title(plan, request_text),
            status=workflow_status(result.get("status", "")),
            start_date=_date(plan.get("start_date")),
            end_date=_date(plan.get("end_date")),
            timezone=plan.get("timezone"),
            currency=plan.get("currency") or "INR",
            current_version=1,
        )
        trip.versions.append(
            TripVersion(
                version_number=1,
                structured_plan=plan,
                rendered_itinerary=result.get("itinerary") or result.get("answer") or "",
                change_reason="Initial generated draft",
                created_by="planning_agent",
            )
        )
        trip.messages.extend(
            [
                TripMessage(role="user", content=request_text, message_type="request"),
                TripMessage(
                    role="assistant",
                    content=result.get("itinerary") or result.get("answer") or "",
                    message_type="itinerary",
                    metadata_json={"version": 1, "status": result.get("status")},
                ),
            ]
        )
        self.session.add(trip)
        self.session.flush()
        return trip

    def get_owned(self, trip_id: str, user_id: str) -> Trip | None:
        return self.session.scalar(
            select(Trip)
            .where(Trip.id == trip_id, Trip.user_id == user_id)
            .options(selectinload(Trip.versions), selectinload(Trip.messages))
        )

    def get_by_thread(self, thread_id: str, user_id: str) -> Trip | None:
        return self.session.scalar(
            select(Trip)
            .where(Trip.planning_thread_id == thread_id, Trip.user_id == user_id)
            .options(selectinload(Trip.versions), selectinload(Trip.messages))
        )

    def list_owned(self, user_id: str) -> list[Trip]:
        return list(
            self.session.scalars(
                select(Trip)
                .where(Trip.user_id == user_id)
                .options(selectinload(Trip.versions))
                .order_by(Trip.updated_at.desc())
            )
        )

    def apply_workflow_result(
        self,
        trip: Trip,
        result: dict[str, Any],
        *,
        action: str,
        feedback: str,
    ) -> None:
        plan = result.get("structured_plan") or {}
        itinerary = result.get("itinerary") or result.get("answer") or ""
        latest = max(trip.versions, key=lambda item: item.version_number)
        trip.messages.append(
            TripMessage(
                role="user",
                content=feedback or f"{action.title()} this travel plan.",
                message_type=f"approval_{action}",
            )
        )
        is_new_draft = bool(itinerary) and (
            itinerary != latest.rendered_itinerary or plan != latest.structured_plan
        )
        if is_new_draft:
            next_version = trip.current_version + 1
            trip.versions.append(
                TripVersion(
                    version_number=next_version,
                    structured_plan=plan,
                    rendered_itinerary=itinerary,
                    change_reason=feedback or "Revised generated draft",
                    created_by="planning_agent",
                )
            )
            trip.current_version = next_version
            trip.messages.append(
                TripMessage(
                    role="assistant",
                    content=itinerary,
                    message_type="itinerary",
                    metadata_json={"version": next_version, "status": result.get("status")},
                )
            )
        trip.status = workflow_status(result.get("status", ""))
        if plan:
            trip.title = history_title(
                plan,
                str(plan.get("original_request") or trip.title),
            )
            trip.start_date = _date(plan.get("start_date"))
            trip.end_date = _date(plan.get("end_date"))
            trip.timezone = plan.get("timezone")
            trip.currency = plan.get("currency") or trip.currency


def trip_summary(trip: Trip) -> dict[str, Any]:
    latest = max(trip.versions, key=lambda item: item.version_number)
    title = history_title(
        latest.structured_plan or {},
        str((latest.structured_plan or {}).get("original_request") or trip.title),
    )
    return {
        "id": trip.id,
        "thread_id": trip.planning_thread_id,
        "title": title,
        "status": trip.status,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "end_date": trip.end_date.isoformat() if trip.end_date else None,
        "currency": trip.currency,
        "current_version": trip.current_version,
        "guardian_enabled": trip.guardian_enabled,
        "updated_at": trip.updated_at.isoformat(),
        "itinerary": strip_structured_plan_section(latest.rendered_itinerary),
        "structured_plan": latest.structured_plan,
    }


def trip_detail(trip: Trip) -> dict[str, Any]:
    payload = trip_summary(trip)
    payload["versions"] = [
        {
            "version": version.version_number,
            "itinerary": strip_structured_plan_section(version.rendered_itinerary),
            "structured_plan": version.structured_plan,
            "change_reason": version.change_reason,
            "created_by": version.created_by,
            "created_at": version.created_at.isoformat(),
        }
        for version in sorted(trip.versions, key=lambda item: item.version_number)
    ]
    payload["messages"] = [
        {
            "role": message.role,
            "content": message.content,
            "message_type": message.message_type,
            "created_at": message.created_at.isoformat(),
        }
        for message in sorted(trip.messages, key=lambda item: item.created_at)
    ]
    return payload
