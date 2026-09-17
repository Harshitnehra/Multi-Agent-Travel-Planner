"""Validated structured representation of a generated travel plan."""

from __future__ import annotations

import uuid
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_item_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class TripModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Location(TripModel):
    name: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    iata_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("iata_code", mode="before")
    @classmethod
    def normalize_iata(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class FlightSegment(TripModel):
    segment_id: str = Field(default_factory=lambda: new_item_id("flight"))
    airline: str | None = Field(default=None, max_length=120)
    flight_number: str | None = Field(default=None, max_length=20)
    origin: Location | None = None
    destination: Location | None = None
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    status: Literal["suggested", "selected", "booked"] = "suggested"
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    source: str | None = Field(default=None, max_length=500)
    source_verified: bool = False
    booking_id: str | None = Field(default=None, max_length=36)
    booking_reference: str | None = Field(default=None, max_length=200)
    demo_booking: bool = False

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("flight timestamps must include timezone information")
        return value

    @model_validator(mode="after")
    def validate_times(self):
        if self.departure_at and self.arrival_at and self.arrival_at <= self.departure_at:
            raise ValueError("flight arrival must be after departure")
        return self


class HotelStay(TripModel):
    stay_id: str = Field(default_factory=lambda: new_item_id("hotel"))
    name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=500)
    check_in: date | None = None
    check_out: date | None = None
    status: Literal["suggested", "selected", "booked"] = "suggested"
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    source: str | None = Field(default=None, max_length=500)
    source_verified: bool = False
    booking_id: str | None = Field(default=None, max_length=36)
    booking_reference: str | None = Field(default=None, max_length=200)
    demo_booking: bool = False

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("hotel check-out must be after check-in")
        return self


class Activity(TripModel):
    activity_id: str = Field(default_factory=lambda: new_item_id("activity"))
    name: str = Field(min_length=1, max_length=200)
    day_number: int | None = Field(default=None, ge=1)
    location: Location | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    indoor: bool | None = None
    weather_sensitive: bool = False
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("activity timestamps must include timezone information")
        return value

    @model_validator(mode="after")
    def validate_times(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("activity end must be after start")
        return self


class TripPlan(TripModel):
    title: str = Field(min_length=1, max_length=200)
    original_request: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(default="", max_length=2_000)
    origin: Location | None = None
    destination: Location | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, max_length=64)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    travelers: int | None = Field(default=None, ge=1, le=100)
    total_budget: Decimal | None = Field(default=None, ge=0)
    flights: list[FlightSegment] = Field(default_factory=list, max_length=20)
    hotels: list[HotelStay] = Field(default_factory=list, max_length=30)
    activities: list[Activity] = Field(default_factory=list, max_length=200)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("trip end date cannot be before start date")
        return self


class ItineraryGeneration(TripModel):
    rendered_itinerary: str = Field(min_length=1, max_length=20_000)
    trip_plan: TripPlan


def fallback_trip_plan(original_request: str) -> TripPlan:
    """Return a valid minimal plan when provider structured parsing fails."""
    origin_name = None
    destination_name = None
    route_match = re.search(
        r"\bfrom\s+([A-Za-z][A-Za-z .'-]{1,50}?)\s+to\s+([A-Za-z][A-Za-z .'-]{1,50}?)(?=\s+(?:for|in|under|with|on|from)\b|[,.]|$)",
        original_request,
        re.IGNORECASE,
    )
    trip_match = re.search(
        r"\bplan\s+(?:a|an)\s+(?:(?:\d+\s*-?\s*day|long\s+weekend)\s+(?:in\s+)?)?"
        r"([A-Za-z][A-Za-z .'-]{1,50}?)\s+(?:trip\s+)?from\s+"
        r"([A-Za-z][A-Za-z .'-]{1,50}?)(?=\s+(?:for|in|under|with|on)\b|[,.]|$)",
        original_request,
        re.IGNORECASE,
    )
    if route_match:
        origin_name, destination_name = route_match.group(1), route_match.group(2)
    elif trip_match:
        destination_name, origin_name = trip_match.group(1), trip_match.group(2)
    origin_name = origin_name.strip().title() if origin_name else None
    destination_name = destination_name.strip().title() if destination_name else None
    if destination_name:
        destination_name = re.sub(r"^Plan\s+(?:A|An)\s+", "", destination_name)
    return TripPlan(
        title="Travel plan",
        original_request=original_request,
        summary="Structured details could not be extracted from this draft.",
        origin=Location(name=origin_name, city=origin_name) if origin_name else None,
        destination=(
            Location(name=destination_name, city=destination_name)
            if destination_name
            else None
        ),
        assumptions=["The readable itinerary remains available for human review."],
        missing_information=[
            "Travel dates",
            "Confirmed flight details",
            "Confirmed hotel details",
        ],
    )
