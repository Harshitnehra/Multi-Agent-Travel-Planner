"""Minimal server-side Razorpay Test Mode integration."""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

from config import Settings, get_settings


class RazorpayTestError(RuntimeError):
    pass


class RazorpayTestGateway:
    api_url = "https://api.razorpay.com/v1/orders"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def credentials(self) -> tuple[str, str] | None:
        try:
            return self.settings.razorpay_test_credentials()
        except ValueError as exc:
            raise RazorpayTestError(str(exc)) from exc

    def create_order(
        self, *, amount: Decimal, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any] | None:
        credentials = self.credentials()
        if credentials is None:
            return None
        key_id, key_secret = credentials
        amount_subunits = int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        try:
            response = requests.post(
                self.api_url,
                auth=(key_id, key_secret),
                json={
                    "amount": amount_subunits,
                    "currency": currency.upper(),
                    "receipt": receipt[:40],
                    "notes": notes,
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            raise RazorpayTestError("Razorpay Test Mode order could not be created.") from None
        order_id = str(result.get("id") or "")
        if not order_id.startswith("order_"):
            raise RazorpayTestError("Razorpay returned an invalid test order.")
        return {
            "order_id": order_id,
            "key_id": key_id,
            "amount": amount_subunits,
            "currency": currency.upper(),
        }

    def verify_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        credentials = self.credentials()
        if credentials is None:
            return False
        _key_id, key_secret = credentials
        expected = hmac.new(
            key_secret.encode("utf-8"),
            f"{order_id}|{payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
