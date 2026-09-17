"""Ownership-scoped persistence for resumable travel plans."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import Trip, TripMessage, TripVersion


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
            title=plan.get("title") or "Travel plan",
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
            trip.title = plan.get("title") or trip.title
            trip.start_date = _date(plan.get("start_date"))
            trip.end_date = _date(plan.get("end_date"))
            trip.timezone = plan.get("timezone")
            trip.currency = plan.get("currency") or trip.currency


def trip_summary(trip: Trip) -> dict[str, Any]:
    latest = max(trip.versions, key=lambda item: item.version_number)
    return {
        "id": trip.id,
        "thread_id": trip.planning_thread_id,
        "title": trip.title,
        "status": trip.status,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "end_date": trip.end_date.isoformat() if trip.end_date else None,
        "currency": trip.currency,
        "current_version": trip.current_version,
        "guardian_enabled": trip.guardian_enabled,
        "updated_at": trip.updated_at.isoformat(),
        "itinerary": latest.rendered_itinerary,
        "structured_plan": latest.structured_plan,
    }


def trip_detail(trip: Trip) -> dict[str, Any]:
    payload = trip_summary(trip)
    payload["versions"] = [
        {
            "version": version.version_number,
            "itinerary": version.rendered_itinerary,
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
