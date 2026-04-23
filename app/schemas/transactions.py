from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class PaymentStatus(str, Enum):
    initiated = "initiated"
    processed = "processed"
    failed = "failed"


class SettlementStatus(str, Enum):
    pending = "pending"
    settled = "settled"
    not_applicable = "not_applicable"


class MerchantOut(BaseModel):
    merchant_id: str
    name: str

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    event_id: str
    event_type: str
    amount: Decimal | None
    currency: str | None
    event_timestamp: datetime
    received_at: datetime

    model_config = {"from_attributes": True}


class TransactionOut(BaseModel):
    transaction_id: str
    merchant_id: str
    amount: Decimal
    currency: str
    payment_status: PaymentStatus
    settlement_status: SettlementStatus
    first_event_at: datetime
    last_event_at: datetime
    has_conflict: bool
    conflict_reason: str | None = None

    model_config = {"from_attributes": True}


class TransactionDetail(TransactionOut):
    merchant: MerchantOut
    events: list[EventOut]


class TransactionListResponse(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
