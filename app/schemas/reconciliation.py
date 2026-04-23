from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class SummaryGroup(BaseModel):
    keys: dict[str, Any]
    count: int
    total_amount: Decimal
    currency: str


class SummaryResponse(BaseModel):
    group_by: list[str]
    groups: list[SummaryGroup]


class DiscrepancyOut(BaseModel):
    transaction_id: str
    merchant_id: str
    amount: Decimal
    currency: str
    payment_status: str
    settlement_status: str
    conflict_reason: str
    last_event_at: datetime
    age_hours: float

    model_config = {"from_attributes": True}


class DiscrepancyListResponse(BaseModel):
    items: list[DiscrepancyOut]
    total: int
    limit: int
    offset: int
