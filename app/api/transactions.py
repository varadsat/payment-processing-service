from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.engine import get_session
from app.db.models import Transaction
from app.schemas.transactions import TransactionDetail, TransactionListResponse

router = APIRouter(prefix="/transactions", tags=["transactions"])

_SORT_COLS = {
    "last_event_at": Transaction.last_event_at,
    "amount": Transaction.amount,
}


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    merchant_id: str | None = None,
    payment_status: str | None = None,
    settlement_status: str | None = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    sort: str = "-last_event_at",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
):
    stmt = select(Transaction)

    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)
    if payment_status:
        stmt = stmt.where(Transaction.payment_status == payment_status)
    if settlement_status:
        stmt = stmt.where(Transaction.settlement_status == settlement_status)
    if from_date:
        stmt = stmt.where(Transaction.last_event_at >= from_date)
    if to_date:
        stmt = stmt.where(Transaction.last_event_at < to_date + timedelta(days=1))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    desc = sort.startswith("-")
    col = _SORT_COLS.get(sort.lstrip("-"), Transaction.last_event_at)
    stmt = stmt.order_by(col.desc() if desc else col.asc()).limit(limit).offset(offset)

    items = session.scalars(stmt).all()

    return TransactionListResponse(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{transaction_id}", response_model=TransactionDetail)
def get_transaction(transaction_id: str, session: Session = Depends(get_session)):
    txn = session.scalars(
        select(Transaction)
        .where(Transaction.transaction_id == transaction_id)
        .options(joinedload(Transaction.merchant), selectinload(Transaction.events))
    ).unique().one_or_none()

    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn
