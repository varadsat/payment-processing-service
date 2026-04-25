import uuid
from datetime import datetime, timedelta, timezone


def _event(event_type, txn_id, merchant_id, merchant_name="Recon Merchant", timestamp=None):
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "transaction_id": txn_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": "200.00",
        "currency": "INR",
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


def _post(client, payload):
    return client.post("/events", json=payload)


def test_summary_grouped_by_merchant(client):
    m1, m2 = f"m-{uuid.uuid4()}", f"m-{uuid.uuid4()}"

    for _ in range(2):
        _post(client, _event("payment_initiated", str(uuid.uuid4()), m1))
    for _ in range(3):
        _post(client, _event("payment_initiated", str(uuid.uuid4()), m2))

    resp = client.get("/reconciliation/summary?group_by=merchant")
    assert resp.status_code == 200
    body = resp.json()

    merchant_ids = {g["keys"]["merchant_id"] for g in body["groups"]}
    assert m1 in merchant_ids
    assert m2 in merchant_ids

    m1_group = next(g for g in body["groups"] if g["keys"]["merchant_id"] == m1)
    m2_group = next(g for g in body["groups"] if g["keys"]["merchant_id"] == m2)
    assert m1_group["count"] == 2
    assert m2_group["count"] == 3


def test_summary_grouped_by_merchant_and_status(client):
    m = f"m-{uuid.uuid4()}"
    txn1, txn2 = str(uuid.uuid4()), str(uuid.uuid4())

    # txn1: initiated only
    _post(client, _event("payment_initiated", txn1, m))
    # txn2: initiated → processed
    _post(client, _event("payment_initiated", txn2, m))
    _post(client, _event("payment_processed", txn2, m))

    resp = client.get("/reconciliation/summary?group_by=merchant,status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["group_by"]) == {"merchant", "status"}

    m_groups = [g for g in body["groups"] if g["keys"].get("merchant_id") == m]
    statuses = {g["keys"]["payment_status"] for g in m_groups}
    assert "initiated" in statuses
    assert "processed" in statuses


def test_discrepancies_returns_only_conflicts(client):
    m = f"m-{uuid.uuid4()}"

    # Clean transaction — no conflict
    clean_txn = str(uuid.uuid4())
    _post(client, _event("payment_initiated", clean_txn, m))
    _post(client, _event("payment_processed", clean_txn, m))
    _post(client, _event("settled", clean_txn, m))

    # Conflicting transaction — settled_after_fail
    bad_txn = str(uuid.uuid4())
    _post(client, _event("payment_initiated", bad_txn, m))
    _post(client, _event("payment_failed", bad_txn, m))
    _post(client, _event("settled", bad_txn, m))

    resp = client.get("/reconciliation/discrepancies")
    assert resp.status_code == 200
    ids = {item["transaction_id"] for item in resp.json()["items"]}
    assert bad_txn in ids
    assert clean_txn not in ids


def test_discrepancies_filter_by_reason(client):
    m = f"m-{uuid.uuid4()}"
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)

    # stuck_settlement — processed but no settlement, old timestamp
    stuck_txn = str(uuid.uuid4())
    _post(client, _event("payment_initiated", stuck_txn, m, timestamp=old_ts))
    _post(client, _event("payment_processed", stuck_txn, m, timestamp=old_ts))

    # settled_after_fail — different reason
    fail_txn = str(uuid.uuid4())
    _post(client, _event("payment_initiated", fail_txn, m))
    _post(client, _event("payment_failed", fail_txn, m))
    _post(client, _event("settled", fail_txn, m))

    resp = client.get("/reconciliation/discrepancies?reason=stuck_settlement")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["conflict_reason"] == "stuck_settlement" for i in items)
    assert stuck_txn in {i["transaction_id"] for i in items}
    assert fail_txn not in {i["transaction_id"] for i in items}
