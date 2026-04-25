import uuid
from datetime import datetime, timezone
from decimal import Decimal


def _event_payload(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "event_type": "payment_initiated",
        "transaction_id": str(uuid.uuid4()),
        "merchant_id": "merchant_api_test",
        "merchant_name": "API Test Merchant",
        "amount": "100.00",
        "currency": "INR",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def test_single_event_returns_201(client):
    resp = client.post("/events", json=_event_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "created"


def test_duplicate_event_returns_200_duplicate(client):
    shared_id = str(uuid.uuid4())
    txn_id = str(uuid.uuid4())

    r1 = client.post("/events", json=_event_payload(event_id=shared_id, transaction_id=txn_id))
    assert r1.status_code == 201

    r2 = client.post("/events", json=_event_payload(event_id=shared_id, transaction_id=txn_id))
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


def test_malformed_payload_returns_422(client):
    resp = client.post("/events", json={"event_id": "x"})
    assert resp.status_code == 422


def test_bulk_three_events_one_duplicate(client):
    shared_id = str(uuid.uuid4())
    txn_id = str(uuid.uuid4())

    events = [
        _event_payload(transaction_id=str(uuid.uuid4())),
        _event_payload(event_id=shared_id, transaction_id=txn_id),
        _event_payload(event_id=shared_id, transaction_id=txn_id),
    ]

    resp = client.post("/events/bulk", json=events)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    statuses = [r["status"] for r in body]
    assert statuses.count("created") == 2
    assert statuses.count("duplicate") == 1
