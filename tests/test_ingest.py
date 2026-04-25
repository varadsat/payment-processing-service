import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.schemas.events import EventIn, EventType
from app.services.events import ingest_event


def _make_event(
    event_type: EventType,
    txn_id: str,
    *,
    event_id: str | None = None,
    timestamp: datetime | None = None,
) -> EventIn:
    return EventIn(
        event_id=event_id or str(uuid.uuid4()),
        event_type=event_type,
        transaction_id=txn_id,
        merchant_id="merchant_test",
        merchant_name="Test Merchant",
        amount=Decimal("100.00"),
        currency="INR",
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def _ingest(session, event_type, txn_id, **kwargs):
    event = _make_event(event_type, txn_id, **kwargs)
    with session.begin():
        return ingest_event(session, event)


def test_happy_path(db_session):
    txn_id = str(uuid.uuid4())

    r1 = _ingest(db_session, EventType.payment_initiated, txn_id)
    r2 = _ingest(db_session, EventType.payment_processed, txn_id)
    r3 = _ingest(db_session, EventType.settled, txn_id)
    
    assert r1.status == "created"
    assert r2.status == "created"
    assert r3.status == "created"
    assert r3.conflict_reason is None

    row = db_session.execute(
        text(
            "SELECT payment_status, settlement_status, has_conflict "
            "FROM transactions WHERE transaction_id = :id"
        ),
        {"id": txn_id},
    ).fetchone()
    assert row.payment_status == "processed"
    assert row.settlement_status == "settled"
    assert not row.has_conflict


def test_duplicate_event(db_session):
    txn_id = str(uuid.uuid4())
    shared_event_id = str(uuid.uuid4())

    r1 = _ingest(db_session, EventType.payment_initiated, txn_id, event_id=shared_event_id)
    assert r1.status == "created"

    # Same event_id, different event_type — should be treated as duplicate
    event = _make_event(EventType.payment_processed, txn_id, event_id=shared_event_id)
    with db_session.begin():
        r2 = ingest_event(db_session, event)

    assert r2.status == "duplicate"

    # Transaction state must not have changed
    row = db_session.execute(
        text("SELECT payment_status FROM transactions WHERE transaction_id = :id"),
        {"id": txn_id},
    ).fetchone()
    assert row.payment_status == "initiated"


def test_stuck_settlement(db_session):
    txn_id = str(uuid.uuid4())
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)

    _ingest(db_session, EventType.payment_initiated, txn_id, timestamp=old_ts)
    r = _ingest(db_session, EventType.payment_processed, txn_id, timestamp=old_ts)

    assert r.status == "flagged"
    assert r.conflict_reason == "stuck_settlement"

    row = db_session.execute(
        text("SELECT conflict_reason FROM transactions WHERE transaction_id = :id"),
        {"id": txn_id},
    ).fetchone()
    assert row.conflict_reason == "stuck_settlement"


def test_settled_after_fail(db_session):
    txn_id = str(uuid.uuid4())

    _ingest(db_session, EventType.payment_initiated, txn_id)
    _ingest(db_session, EventType.payment_failed, txn_id)
    r = _ingest(db_session, EventType.settled, txn_id)

    assert r.status == "flagged"
    assert r.conflict_reason == "settled_after_fail"


def test_conflicting_transition(db_session):
    txn_id = str(uuid.uuid4())

    _ingest(db_session, EventType.payment_initiated, txn_id)
    _ingest(db_session, EventType.payment_failed, txn_id)
    r = _ingest(db_session, EventType.payment_processed, txn_id)

    assert r.status == "flagged"
    assert r.conflict_reason == "conflicting_transition"
