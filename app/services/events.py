import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import ConflictReason, PaymentStatus, SettlementStatus
from app.schemas.events import EventIn, EventType, IngestResult

_STUCK_HOURS = 24


@dataclass
class TransitionResult:
    payment_status: PaymentStatus
    settlement_status: SettlementStatus
    conflict_reason: ConflictReason | None


def apply_transition(
    current_payment: PaymentStatus | None,
    current_settlement: SettlementStatus | None,
    event_type: EventType,
) -> TransitionResult:
    PS = PaymentStatus
    SS = SettlementStatus
    CR = ConflictReason
    ET = EventType

    match (event_type, current_payment):
        case (ET.payment_initiated, None):
            return TransitionResult(PS.initiated, SS.pending, None)
        case (ET.payment_processed, PS.initiated):
            return TransitionResult(PS.processed, current_settlement, None)
        case (ET.payment_processed, PS.processed):
            return TransitionResult(PS.processed, current_settlement, None)
        case (ET.payment_processed, PS.failed):
            return TransitionResult(current_payment, current_settlement, CR.conflicting_transition)
        case (ET.payment_failed, PS.initiated):
            return TransitionResult(PS.failed, SS.not_applicable, None)
        case (ET.payment_failed, PS.processed):
            return TransitionResult(current_payment, current_settlement, CR.conflicting_transition)
        case (ET.settled, PS.processed):
            return TransitionResult(current_payment, SS.settled, None)
        case (ET.settled, PS.failed):
            return TransitionResult(current_payment, SS.settled, CR.settled_after_fail)
        case (ET.settled, PS.initiated):
            return TransitionResult(current_payment, current_settlement, CR.conflicting_transition)
        case _:
            return TransitionResult(
                current_payment or PS.initiated,
                current_settlement or SS.pending,
                CR.conflicting_transition,
            )


def ingest_event(session: Session, event: EventIn) -> IngestResult:
    # 1. Upsert merchant
    session.execute(
        text(
            "INSERT INTO merchants (merchant_id, name) VALUES (:mid, :name) "
            "ON CONFLICT (merchant_id) DO NOTHING"
        ),
        {"mid": event.merchant_id, "name": event.merchant_name},
    )

    # 2. Lock transaction row — prevents concurrent updates to the same transaction
    row = session.execute(
        text(
            "SELECT transaction_id, payment_status, settlement_status "
            "FROM transactions WHERE transaction_id = :txn_id FOR UPDATE"
        ),
        {"txn_id": event.transaction_id},
    ).fetchone()

    current_payment = PaymentStatus(row.payment_status) if row else None
    current_settlement = SettlementStatus(row.settlement_status) if row else None

    # 3. Compute new state (pure — easy to unit-test in isolation)
    tr = apply_transition(current_payment, current_settlement, event.event_type)

    # 4. Stuck-settlement post-check
    if (
        tr.payment_status == PaymentStatus.processed
        and tr.settlement_status == SettlementStatus.pending
        and tr.conflict_reason is None
        and event.timestamp < datetime.now(timezone.utc) - timedelta(hours=_STUCK_HOURS)
    ):
        tr.conflict_reason = ConflictReason.stuck_settlement

    has_conflict = tr.conflict_reason is not None
    conflict_val = tr.conflict_reason.value if tr.conflict_reason else None

    # 5. Create transaction row before inserting the event (FK: events → transactions)
    if row is None:
        session.execute(
            text("""
                INSERT INTO transactions (
                    transaction_id, merchant_id, amount, currency,
                    payment_status, settlement_status,
                    first_event_at, last_event_at, has_conflict, conflict_reason
                ) VALUES (
                    :txn_id, :merchant_id, :amount, :currency,
                    CAST(:payment_status AS payment_status),
                    CAST(:settlement_status AS settlement_status),
                    :event_ts, :event_ts, :has_conflict,
                    CAST(:conflict_reason AS conflict_reason)
                )
            """),
            {
                "txn_id": event.transaction_id,
                "merchant_id": event.merchant_id,
                "amount": event.amount,
                "currency": event.currency,
                "payment_status": tr.payment_status.value,
                "settlement_status": tr.settlement_status.value,
                "event_ts": event.timestamp,
                "has_conflict": has_conflict,
                "conflict_reason": conflict_val,
            },
        )

    # 6. Insert event — idempotency via ON CONFLICT DO NOTHING RETURNING
    inserted = session.execute(
        text("""
            INSERT INTO events (
                event_id, transaction_id, merchant_id, event_type,
                amount, currency, event_timestamp, raw_payload
            ) VALUES (
                :event_id, :txn_id, :merchant_id, :event_type,
                :amount, :currency, :event_ts, CAST(:raw_payload AS jsonb)
            )
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
        """),
        {
            "event_id": event.event_id,
            "txn_id": event.transaction_id,
            "merchant_id": event.merchant_id,
            "event_type": event.event_type.value,
            "amount": event.amount,
            "currency": event.currency,
            "event_ts": event.timestamp,
            "raw_payload": json.dumps(event.model_dump(mode="json")),
        },
    )
    if inserted.fetchone() is None:
        return IngestResult(status="duplicate", transaction_id=event.transaction_id)

    # 7. Update existing transaction projection
    if row is not None:
        session.execute(
            text("""
                UPDATE transactions SET
                    payment_status    = CAST(:payment_status AS payment_status),
                    settlement_status = CAST(:settlement_status AS settlement_status),
                    last_event_at     = :event_ts,
                    has_conflict      = :has_conflict,
                    conflict_reason   = CAST(:conflict_reason AS conflict_reason)
                WHERE transaction_id = :txn_id
            """),
            {
                "txn_id": event.transaction_id,
                "payment_status": tr.payment_status.value,
                "settlement_status": tr.settlement_status.value,
                "event_ts": event.timestamp,
                "has_conflict": has_conflict,
                "conflict_reason": conflict_val,
            },
        )

    status: str = "flagged" if has_conflict else "created"
    return IngestResult(
        status=status,
        transaction_id=event.transaction_id,
        conflict_reason=conflict_val,
    )
