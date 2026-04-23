import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    payment_initiated = "payment_initiated"
    payment_processed = "payment_processed"
    payment_failed = "payment_failed"
    settled = "settled"


class EventIn(BaseModel):
    event_id: str
    event_type: EventType
    transaction_id: str
    merchant_id: str
    merchant_name: str
    amount: Decimal = Field(gt=0)
    currency: str
    timestamp: datetime

    @field_validator("currency")
    @classmethod
    def currency_three_uppercase(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("currency must be exactly 3 uppercase letters")
        return v

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v


class IngestResult(BaseModel):
    status: Literal["created", "duplicate", "flagged"]
    transaction_id: str
    conflict_reason: str | None = None
