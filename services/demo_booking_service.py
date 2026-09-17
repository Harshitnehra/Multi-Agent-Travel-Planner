"""Demo offer holding and local Razorpay-test payment simulation."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import AuditEvent, Booking, PaymentEvent, PaymentOrder, Trip, TripVersion
from domain import TripPlan, fallback_trip_plan
from providers import RazorpayTestError, RazorpayTestGateway


BookingType = Literal["flight", "hotel"]
PaymentOutcome = Literal["success", "failed", "cancelled"]
DEMO_PROVIDER = "tripmate_demo"
OFFER_MINUTES = 30
HOLD_MINUTES = 15
DEMO_NOTICE = "Simulation only — this is not a real reservation or charge."


class DemoBookingNotFound(ValueError):
    pass


class DemoBookingConflict(ValueError):
    pass


class DemoOfferExpired(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(value: object, fallback: Decimal) -> Decimal:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed >= 0 else fallback
    except (InvalidOperation, TypeError):
        return fallback


def _latest_version(trip: Trip) -> TripVersion:
    return max(trip.versions, key=lambda item: item.version_number)


def _demo_trip_item(
    plan: dict[str, Any],
    booking_type: BookingType,
    index: int,
    search_details: dict[str, Any] | None = None,
) -> dict:
    candidates = plan.get(f"{booking_type}s") or []
    source = copy.deepcopy(candidates[index % len(candidates)]) if candidates else {}
    if booking_type == "flight":
        details = search_details or {}
        origin = copy.deepcopy(source.get("origin") or plan.get("origin") or {})
        destination = copy.deepcopy(
            source.get("destination") or plan.get("destination") or {}
        )
        if details:
            origin = {"name": details["origin"], "city": details["origin"]}
            destination = {
                "name": details["destination"],
                "city": details["destination"],
            }
            if details.get("origin_iata"):
                origin["iata_code"] = details["origin_iata"].upper()
            if details.get("destination_iata"):
                destination["iata_code"] = details["destination_iata"].upper()
            departure = datetime.fromisoformat(
                f"{details['departure_date']}T{8 + index * 3:02d}:00:00+00:00"
            )
            source["departure_at"] = departure.isoformat()
            source["arrival_at"] = (departure + timedelta(hours=4)).isoformat()
        source.update(
            {
                "segment_id": source.get("segment_id") or f"demo-flight-{index + 1}",
                "airline": f"TripMate Demo Air {index + 1}",
                "flight_number": f"TM{101 + index}",
                "origin": origin or None,
                "destination": destination or None,
            }
        )
    else:
        destination = plan.get("destination") or {}
        source.update(
            {
                "stay_id": source.get("stay_id") or f"demo-hotel-{index + 1}",
                "name": f"TripMate Demo Hotel {index + 1}",
                "city": source.get("city") or destination.get("city") or destination.get("name"),
                "check_in": source.get("check_in") or plan.get("start_date"),
                "check_out": source.get("check_out") or plan.get("end_date"),
            }
        )
    source.update(
        {
            "status": "suggested",
            "source": "TripMate demo offer generator",
            "source_verified": False,
            "booking_id": None,
            "booking_reference": None,
            "demo_booking": False,
            "currency": source.get("currency") or plan.get("currency") or "INR",
        }
    )
    return source


def booking_payload(booking: Booking) -> dict[str, Any]:
    return {
        "id": booking.id,
        "booking_type": booking.booking_type,
        "provider": "TripMate Demo",
        "status": booking.status,
        "details": booking.details,
        "total_amount": str(booking.total_amount),
        "currency": booking.currency,
        "demo": True,
        "notice": DEMO_NOTICE,
    }


def payment_payload(payment: PaymentOrder) -> dict[str, Any]:
    local_simulator = str(payment.provider_order_id or "").startswith("order_test_")
    return {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "provider_order_id": payment.provider_order_id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "provider": "Razorpay Test Simulator" if local_simulator else "Razorpay Test Mode",
        "checkout": {
            "mode": "local_demo" if local_simulator else "razorpay_test",
        },
        "demo": True,
        "notice": DEMO_NOTICE,
    }


class DemoBookingService:
    def __init__(self, session: Session, gateway: RazorpayTestGateway | None = None):
        self.session = session
        self.gateway = gateway or RazorpayTestGateway()

    def search_offers(
        self,
        trip: Trip,
        booking_type: BookingType,
        search_details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if trip.status not in {"approved", "active"}:
            raise DemoBookingConflict("Approve the trip before searching demo offers.")
        now = _now()
        plan = copy.deepcopy(_latest_version(trip).structured_plan or {})
        if not plan.get("origin") or not plan.get("destination"):
            inferred = fallback_trip_plan(
                str(plan.get("original_request") or trip.title)
            ).model_dump(mode="json")
            for key in ("title", "origin", "destination"):
                if not plan.get(key) and inferred.get(key):
                    plan[key] = inferred[key]
        existing = list(
            self.session.scalars(
                select(Booking).where(
                    Booking.trip_id == trip.id,
                    Booking.booking_type == booking_type,
                    Booking.provider == DEMO_PROVIDER,
                    Booking.status.in_(["searched", "held"]),
                )
            )
        )
        active = []
        for offer in existing:
            expiry = _parse_time(offer.details.get("offer_expires_at"))
            hold_expiry = _parse_time(offer.details.get("held_until"))
            deadline = hold_expiry if offer.status == "held" else expiry
            if deadline and _as_utc(deadline) <= now:
                offer.status = "expired"
            elif not search_details or offer.details.get("search_details") == search_details:
                active.append(offer)
        if active:
            self.session.flush()
            return [booking_payload(item) for item in active]

        currency = plan.get("currency") or trip.currency
        fallback = Decimal("18000") if booking_type == "flight" else Decimal("24000")
        candidates = plan.get(f"{booking_type}s") or []
        offers = []
        for index in range(3):
            trip_item = _demo_trip_item(plan, booking_type, index, search_details)
            candidate = candidates[index % len(candidates)] if candidates else {}
            base = _money(candidate.get("estimated_cost"), fallback)
            amount = (base * (Decimal("1") + Decimal(index) / Decimal("10"))).quantize(
                Decimal("0.01")
            )
            offer = Booking(
                trip=trip,
                booking_type=booking_type,
                provider=DEMO_PROVIDER,
                status="searched",
                details={
                    "label": trip_item.get("airline") or trip_item.get("name"),
                    "trip_item": trip_item,
                    "search_details": search_details or {},
                    "offer_expires_at": (now + timedelta(minutes=OFFER_MINUTES)).isoformat(),
                    "demo_notice": DEMO_NOTICE,
                },
                total_amount=amount,
                currency=currency,
                demo=True,
            )
            self.session.add(offer)
            offers.append(offer)
        self.session.flush()
        return [booking_payload(item) for item in offers]

    def hold_offer(self, trip: Trip, booking_id: str) -> dict[str, Any]:
        booking = self._owned_booking(trip, booking_id)
        if booking.status == "held":
            self._require_unexpired_hold(booking)
            return booking_payload(booking)
        if booking.status != "searched":
            raise DemoBookingConflict("This demo offer can no longer be held.")
        offer_expiry = _parse_time(booking.details.get("offer_expires_at"))
        if offer_expiry is None or _as_utc(offer_expiry) <= _now():
            booking.status = "expired"
            raise DemoOfferExpired("This demo offer has expired. Search again.")
        details = dict(booking.details)
        details["held_until"] = (_now() + timedelta(minutes=HOLD_MINUTES)).isoformat()
        booking.details = details
        booking.status = "held"
        booking.provider_reference = f"hold_test_{secrets.token_hex(8)}"
        self._audit(trip, "demo_offer.held", {"booking_id": booking.id})
        self.session.flush()
        return booking_payload(booking)

    def create_payment(self, trip: Trip, booking_id: str, idempotency_key: str) -> dict[str, Any]:
        booking = self._owned_booking(trip, booking_id)
        existing = self.session.scalar(
            select(PaymentOrder).where(PaymentOrder.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.booking_id != booking.id:
                raise DemoBookingConflict("This payment key belongs to another booking.")
            payload = payment_payload(existing)
            if payload["checkout"]["mode"] == "razorpay_test":
                try:
                    credentials = self.gateway.credentials()
                except RazorpayTestError as exc:
                    raise DemoBookingConflict(str(exc)) from exc
                if credentials:
                    payload["checkout"].update(
                        {
                            "order_id": existing.provider_order_id,
                            "key_id": credentials[0],
                            "amount": int(existing.amount * 100),
                            "currency": existing.currency,
                        }
                    )
            return payload
        self._require_unexpired_hold(booking)
        try:
            checkout = self.gateway.create_order(
                amount=booking.total_amount,
                currency=booking.currency,
                receipt=f"demo-{booking.id}",
                notes={"trip_id": trip.id, "booking_id": booking.id, "demo": "true"},
            )
        except RazorpayTestError as exc:
            raise DemoBookingConflict(str(exc)) from exc
        provider_order_id = (
            checkout["order_id"] if checkout else f"order_test_{secrets.token_hex(10)}"
        )
        payment = PaymentOrder(
            trip_id=trip.id,
            booking=booking,
            provider_order_id=provider_order_id,
            amount=booking.total_amount,
            currency=booking.currency,
            status="pending",
            idempotency_key=idempotency_key,
        )
        booking.status = "payment_pending"
        self.session.add(payment)
        self._audit(trip, "demo_payment.created", {"booking_id": booking.id})
        self.session.flush()
        payload = payment_payload(payment)
        if checkout:
            payload["checkout"].update(checkout)
        return payload

    def verify_razorpay_payment(
        self,
        trip: Trip,
        booking_id: str,
        payment_id: str,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
        event_id: str,
    ) -> dict[str, Any]:
        payment = self._owned_payment(trip, booking_id, payment_id)
        if str(payment.provider_order_id or "").startswith("order_test_"):
            raise DemoBookingConflict("This order uses the local test simulator.")
        if payment.provider_order_id != razorpay_order_id:
            raise DemoBookingConflict("The Razorpay order does not belong to this booking.")
        if not self.gateway.verify_signature(
            payment.provider_order_id, razorpay_payment_id, razorpay_signature
        ):
            raise DemoBookingConflict("Razorpay payment signature verification failed.")
        return self._record_outcome(
            trip,
            booking_id,
            payment,
            event_id,
            "success",
            provider_payment_id=razorpay_payment_id,
        )

    def simulate_payment(
        self,
        trip: Trip,
        booking_id: str,
        payment_id: str,
        event_id: str,
        outcome: PaymentOutcome,
    ) -> dict[str, Any]:
        payment = self._owned_payment(trip, booking_id, payment_id)
        if outcome == "success" and not str(payment.provider_order_id or "").startswith(
            "order_test_"
        ):
            raise DemoBookingConflict(
                "A Razorpay Test Mode success must pass server-side signature verification."
            )
        return self._record_outcome(trip, booking_id, payment, event_id, outcome)

    def _record_outcome(
        self,
        trip: Trip,
        booking_id: str,
        payment: PaymentOrder,
        event_id: str,
        outcome: PaymentOutcome,
        *,
        provider_payment_id: str | None = None,
    ) -> dict[str, Any]:
        duplicate = self.session.scalar(
            select(PaymentEvent).where(PaymentEvent.provider_event_id == event_id)
        )
        if duplicate:
            if duplicate.payment_order_id != payment.id or duplicate.outcome != outcome:
                raise DemoBookingConflict("This webhook event was already used for another result.")
            if duplicate.payment_order_id != payment.id or payment.booking_id != booking_id:
                raise DemoBookingConflict("The payment does not belong to this booking.")
            return {"duplicate": True, "payment": payment_payload(payment)}
        booking = self._owned_booking(trip, booking_id)
        if payment.status not in {"created", "pending"}:
            raise DemoBookingConflict("This demo payment has already reached a final state.")

        payload = {
            "event_id": event_id,
            "payment_id": payment.id,
            "booking_id": booking_id,
            "outcome": outcome,
        }
        self.session.add(
            PaymentEvent(
                payment_order=payment,
                provider_event_id=event_id,
                outcome=outcome,
                payload_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            )
        )
        if outcome == "success":
            self._require_unexpired_hold(booking, allow_payment_pending=True)
            payment.status = "succeeded"
            payment.provider_payment_id = provider_payment_id or f"pay_test_{secrets.token_hex(10)}"
            booking.status = "demo_confirmed"
            self._add_confirmed_trip_version(trip, booking)
        elif outcome == "failed":
            payment.status = "failed"
            booking.status = "failed"
        else:
            payment.status = "cancelled"
            booking.status = "cancelled"
        self._audit(
            trip,
            f"demo_payment.{outcome}",
            {"booking_id": booking.id, "payment_id": payment.id, "event_id": event_id},
        )
        self.session.flush()
        return {
            "duplicate": False,
            "payment": payment_payload(payment),
            "booking": booking_payload(booking),
            "trip_version": trip.current_version,
        }

    def _owned_payment(self, trip: Trip, booking_id: str, payment_id: str) -> PaymentOrder:
        payment = self.session.scalar(
            select(PaymentOrder)
            .join(Booking, PaymentOrder.booking_id == Booking.id)
            .where(PaymentOrder.id == payment_id, Booking.trip_id == trip.id)
        )
        if payment is None:
            raise DemoBookingNotFound("Demo payment not found.")
        if payment.booking_id != booking_id:
            raise DemoBookingConflict("The payment does not belong to this booking.")
        return payment

    def _owned_booking(self, trip: Trip, booking_id: str) -> Booking:
        booking = self.session.scalar(
            select(Booking).where(Booking.id == booking_id, Booking.trip_id == trip.id)
        )
        if booking is None or not booking.demo or booking.provider != DEMO_PROVIDER:
            raise DemoBookingNotFound("Demo booking not found.")
        return booking

    def _require_unexpired_hold(
        self, booking: Booking, *, allow_payment_pending: bool = False
    ) -> None:
        allowed = {"held", "payment_pending"} if allow_payment_pending else {"held"}
        expiry = _parse_time(booking.details.get("held_until"))
        if booking.status not in allowed:
            raise DemoBookingConflict("Hold this demo offer before starting payment.")
        if expiry is None or _as_utc(expiry) <= _now():
            booking.status = "expired"
            raise DemoOfferExpired("This demo hold has expired and cannot be paid.")

    def _add_confirmed_trip_version(self, trip: Trip, booking: Booking) -> None:
        latest = _latest_version(trip)
        plan = copy.deepcopy(latest.structured_plan)
        key = "flights" if booking.booking_type == "flight" else "hotels"
        identity = "segment_id" if booking.booking_type == "flight" else "stay_id"
        item = copy.deepcopy(booking.details["trip_item"])
        item.update(
            {
                "status": "booked",
                "booking_id": booking.id,
                "booking_reference": booking.provider_reference,
                "demo_booking": True,
                "source": "TripMate demo booking simulator",
                "source_verified": False,
            }
        )
        items = plan.setdefault(key, [])
        replaced = False
        for index, existing in enumerate(items):
            if existing.get(identity) == item.get(identity):
                items[index] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        validated = TripPlan.model_validate(plan).model_dump(mode="json")
        next_version = trip.current_version + 1
        trip.versions.append(
            TripVersion(
                version_number=next_version,
                structured_plan=validated,
                rendered_itinerary=(
                    latest.rendered_itinerary
                    + "\n\n> **Demo booking only:** A simulated booking was added. "
                    "No real reservation or payment was made."
                ),
                change_reason=f"Confirmed simulated {booking.booking_type} booking",
                created_by="razorpay_test_simulator",
            )
        )
        trip.current_version = next_version

    def _audit(self, trip: Trip, action: str, data: dict[str, Any]) -> None:
        self.session.add(
            AuditEvent(
                trip=trip,
                actor=f"user:{trip.user_id}",
                action=action,
                before_data=None,
                after_data={**data, "demo": True},
                reason=DEMO_NOTICE,
            )
        )
