"""Core TripMate domain types, independent of HTTP and persistence."""

from domain.trip_models import (
    Activity,
    FlightSegment,
    HotelStay,
    ItineraryGeneration,
    Location,
    TripPlan,
    fallback_trip_plan,
)

__all__ = [
    "Activity",
    "FlightSegment",
    "HotelStay",
    "ItineraryGeneration",
    "Location",
    "TripPlan",
    "fallback_trip_plan",
]
