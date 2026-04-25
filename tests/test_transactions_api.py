import uuid
from datetime import datetime, timezone


def _event(event_type, txn_id, merchant_id, merchant_name="Test Merchant", **kwargs):
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "transaction_id": txn_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": "100.00",
        "currency": "INR",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }


def _post(client, payload):
    return client.post("/events", json=payload)


def test_list_filter_by_merchant(client):
    m1, m2 = f"m-{uuid.uuid4()}", f"m-{uuid.uuid4()}"

    for _ in range(3):
        _post(client, _event("payment_initiated", str(uuid.uuid4()), m1))
    for _ in range(2):
        _post(client, _event("payment_initiated", str(uuid.uuid4()), m2))

    resp = client.get(f"/transactions?merchant_id={m1}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert all(item["merchant_id"] == m1 for item in body["items"])


def test_pagination_covers_distinct_rows(client):
    m = f"m-{uuid.uuid4()}"
    for _ in range(10):
        _post(client, _event("payment_initiated", str(uuid.uuid4()), m))

    r1 = client.get(f"/transactions?merchant_id={m}&limit=5&offset=0")
    r2 = client.get(f"/transactions?merchant_id={m}&limit=5&offset=5")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["total"] == 10

    ids1 = {i["transaction_id"] for i in r1.json()["items"]}
    ids2 = {i["transaction_id"] for i in r2.json()["items"]}
    assert len(ids1) == 5
    assert len(ids2) == 5
    assert ids1.isdisjoint(ids2)


def test_detail_returns_event_history_in_order(client):
    txn_id = str(uuid.uuid4())
    m = f"m-{uuid.uuid4()}"

    _post(client, _event("payment_initiated", txn_id, m))
    _post(client, _event("payment_processed", txn_id, m))
    _post(client, _event("settled", txn_id, m))

    resp = client.get(f"/transactions/{txn_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["transaction_id"] == txn_id
    assert body["merchant"]["merchant_id"] == m
    assert [e["event_type"] for e in body["events"]] == [
        "payment_initiated", "payment_processed", "settled"
    ]


def test_detail_404_on_unknown(client):
    resp = client.get(f"/transactions/{uuid.uuid4()}")
    assert resp.status_code == 404
