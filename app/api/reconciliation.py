from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.db.engine import get_session
from app.db.models import Transaction
from app.schemas.reconciliation import (
    DiscrepancyListResponse,
    DiscrepancyOut,
    SummaryGroup,
    SummaryResponse,
)

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])

_VALID_GROUP_BY = {"merchant", "date", "status"}


def _to_str(v) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "value"):
        return v.value
    return str(v)


@router.get("/summary", response_model=SummaryResponse)
def summary(
    group_by: str = Query(..., description="CSV of: merchant, date, status"),
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    session: Session = Depends(get_session),
):
    dimensions = [d.strip() for d in group_by.split(",") if d.strip()]
    invalid = set(dimensions) - _VALID_GROUP_BY
    if not dimensions or invalid:
        raise HTTPException(
            400, f"group_by must be a non-empty CSV subset of: {', '.join(_VALID_GROUP_BY)}"
        )

    select_cols = []
    group_cols = []
    key_names: list[str] = []

    if "merchant" in dimensions:
        select_cols.append(Transaction.merchant_id.label("merchant_id"))
        group_cols.append(Transaction.merchant_id)
        key_names.append("merchant_id")

    if "date" in dimensions:
        date_col = func.date(Transaction.last_event_at).label("date")
        select_cols.append(date_col)
        group_cols.append(func.date(Transaction.last_event_at))
        key_names.append("date")

    if "status" in dimensions:
        select_cols.append(Transaction.payment_status.label("payment_status"))
        select_cols.append(Transaction.settlement_status.label("settlement_status"))
        group_cols.append(Transaction.payment_status)
        group_cols.append(Transaction.settlement_status)
        key_names.extend(["payment_status", "settlement_status"])

    # currency always in GROUP BY — never aggregate across currencies
    select_cols.append(Transaction.currency.label("currency"))
    group_cols.append(Transaction.currency)

    select_cols.append(func.count().label("count"))
    select_cols.append(func.sum(Transaction.amount).label("total_amount"))

    stmt = select(*select_cols).group_by(*group_cols)

    if from_date:
        stmt = stmt.where(Transaction.last_event_at >= from_date)
    if to_date:
        stmt = stmt.where(Transaction.last_event_at < to_date + timedelta(days=1))

    rows = session.execute(stmt).all()

    groups = []
    for row in rows:
        d = row._asdict()
        groups.append(SummaryGroup(
            keys={k: _to_str(d[k]) for k in key_names},
            count=d["count"],
            total_amount=d["total_amount"],
            currency=d["currency"],
        ))

    return SummaryResponse(group_by=dimensions, groups=groups)


@router.get("/discrepancies", response_model=DiscrepancyListResponse)
def discrepancies(
    reason: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
):
    age_hours = (
        extract("epoch", func.now() - Transaction.last_event_at) / 3600
    ).label("age_hours")

    base_where = [Transaction.has_conflict.is_(True)]
    if reason:
        base_where.append(Transaction.conflict_reason == reason)

    count_stmt = select(func.count()).select_from(Transaction).where(*base_where)
    total = session.scalar(count_stmt) or 0

    stmt = (
        select(Transaction, age_hours)
        .where(*base_where)
        .order_by(Transaction.last_event_at.desc())
        .limit(limit)
        .offset(offset)
    )

    items = []
    for txn, age in session.execute(stmt).all():
        items.append(DiscrepancyOut(
            transaction_id=txn.transaction_id,
            merchant_id=txn.merchant_id,
            amount=txn.amount,
            currency=txn.currency,
            payment_status=txn.payment_status.value,
            settlement_status=txn.settlement_status.value,
            conflict_reason=txn.conflict_reason.value,
            last_event_at=txn.last_event_at,
            age_hours=float(age),
        ))

    return DiscrepancyListResponse(items=items, total=total, limit=limit, offset=offset)
