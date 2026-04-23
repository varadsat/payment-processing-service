from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.events import EventIn, EventType


def _base(**overrides):
    data = {
        "event_id": "b768e3a7-9eb3-4603-b21c-a54cc95661bc",
        "event_type": EventType.payment_initiated,
        "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
        "merchant_id": "merchant_2",
        "merchant_name": "FreshBasket",
        "amount": Decimal("15248.29"),
        "currency": "INR",
        "timestamp": datetime(2026, 1, 8, 12, 11, 58, tzinfo=timezone.utc),
    }
    return {**data, **overrides}


def test_valid_event_parses():
    event = EventIn(**_base())
    assert event.merchant_id == "merchant_2"
    assert event.currency == "INR"


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventIn(**_base(timestamp=datetime(2026, 1, 8, 12, 11, 58)))


def test_negative_amount_rejected():
    with pytest.raises(ValidationError):
        EventIn(**_base(amount=Decimal("-1.00")))


def test_zero_amount_rejected():
    with pytest.raises(ValidationError):
        EventIn(**_base(amount=Decimal("0")))


def test_lowercase_currency_rejected():
    with pytest.raises(ValidationError, match="3 uppercase"):
        EventIn(**_base(currency="inr"))


def test_short_currency_rejected():
    with pytest.raises(ValidationError, match="3 uppercase"):
        EventIn(**_base(currency="IN"))


def test_numeric_currency_rejected():
    with pytest.raises(ValidationError, match="3 uppercase"):
        EventIn(**_base(currency="123"))
