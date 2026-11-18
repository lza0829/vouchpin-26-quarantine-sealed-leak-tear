"""Regression tests for the quarantine/sealed boundary.

Each test reproduces one of the four tears called out in the bug report:

* 「隔离码进 sealed」  — a quarantined row leaking into the sealed batch,
* 「晋升回执撒谎」      — a promotion receipt claiming success while the row
                          stays quarantined (or half-materialised),
* 「双栖同键」          — the same event/coupon living in both tables,
* 「晋升中钉进半截」    — a promotion/seal interleave pinning a half batch.

They also pin the count identity between the formal side and the event side.
"""
from __future__ import annotations

import sqlite3

from app.config import DB_PATH


def _issue(client, code, camp, day):
    r = client.post("/api/events", json={
        "event_type": "issue", "coupon_code": code, "campaign_code": camp, "amount": 0,
        "business_day": day,
    })
    assert r.status_code == 200, r.text


def _redeem(client, code, camp, amount, day=None):
    body = {
        "event_type": "redeem", "coupon_code": code, "campaign_code": camp, "amount": amount,
    }
    if day:
        body["business_day"] = day
    return client.post("/api/events", json=body)


def _quarantine_row(qid):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM redeem_quarantine WHERE id=?", (qid,)).fetchone())
    finally:
        conn.close()


def _insert_fact(coupon, campaign, amount, day, event_id, status="open"):
    """Write a formal row behind the API's back, simulating a torn write."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO redeem_facts(coupon_code, campaign_code, amount, business_day, event_id, status) "
            "VALUES(?,?,?,?,?,?)",
            (coupon, campaign, amount, day, event_id, status),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _fact_rows(conn=None):
    own = conn is None
    conn = conn or sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM redeem_facts ORDER BY id")]
    finally:
        if own:
            conn.close()


def test_quarantined_coupon_never_enters_preview_or_seal(client, day):
    """「隔离码进 sealed」: still-quarantined rows are excluded and reported."""
    _issue(client, "K-SEAL", "NEW10", day)
    bad = _redeem(client, "K-SEAL", "GHOST", 10).json()
    assert bad["status"] == "quarantined"
    qid = bad["quarantine_id"]

    prev = client.get("/api/redeem/preview").json()
    assert prev["excluded_quarantine_count"] == 1
    assert prev["excluded_quarantine_amount"] == 10.0
    assert all(f["coupon_code"] != "K-SEAL" for f in prev["facts"])
    assert all(f.get("quarantine_id") is None for f in prev["facts"])
    assert prev["quarantine_still"] == 1

    seal = client.post("/api/redeem/seal", json={}).json()
    assert seal["fact_count"] == 2  # both seeded facts only
    assert seal["total_amount"] == 30.0
    assert seal["excluded_quarantine_count"] == 1
    sealed = [f for f in _fact_rows() if f["status"] == "sealed"]
    assert all(f["coupon_code"] != "K-SEAL" for f in sealed)
    batch = client.get("/api/batches").json()[0]
    assert batch["fact_count"] == seal["fact_count"] == len(sealed)
    assert batch["total_amount"] == seal["total_amount"] == 30.0
    assert _quarantine_row(qid)["status"] == "quarantined"


def test_half_written_open_fact_on_quarantined_event_never_pins(client, day):
    """「隔离未清却进 sealed」: a torn open row is rolled back, not sealed."""
    _issue(client, "K-TORN", "NEW10", day)
    bad = _redeem(client, "K-TORN", "GHOST", 10).json()
    qid = bad["quarantine_id"]
    stray = _insert_fact("K-TORN", "GHOST", 10, day, bad["event_id"], status="open")

    prev = client.get("/api/redeem/preview").json()
    assert stray not in [f["id"] for f in prev["facts"]]
    assert prev["excluded_quarantine_count"] == 1

    seal = client.post("/api/redeem/seal", json={}).json()
    assert seal["fact_count"] == 2
    assert seal["total_amount"] == 30.0
    assert all(f["id"] != stray for f in _fact_rows())
    # The torn row was removed; the quarantine row is still the only home.
    assert _quarantine_row(qid)["status"] == "quarantined"
    audit = client.get("/api/audit", params={"target_type": "quarantine", "target_id": qid}).json()
    assert any(a["action"] == "promotion_rollback" for a in audit)


def test_fake_promotion_receipt_is_rolled_back_and_not_pinned(client, day):
    """「晋升回执撒谎」: promoted without a fact is reverted to quarantined."""
    _issue(client, "K-LIE", "NEW10", day)
    bad = _redeem(client, "K-LIE", "GHOST", 10).json()
    qid = bad["quarantine_id"]
    # Simulate the receipt that lied: status promoted, no fact, fact_id null.
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE redeem_quarantine SET status='promoted', fact_id=NULL WHERE id=?", (qid,))
        conn.commit()
    finally:
        conn.close()

    # Any read heals it: the row is back to quarantined and still excluded.
    prev = client.get("/api/redeem/preview").json()
    assert prev["excluded_quarantine_count"] == 1
    row = _quarantine_row(qid)
    assert row["status"] == "quarantined" and row["fact_id"] is None
    assert all(f["coupon_code"] != "K-LIE" for f in _fact_rows())

    # A rollback is audited and the row can now be promoted properly.
    log = client.get("/api/audit", params={"target_type": "quarantine", "target_id": qid}).json()
    assert any(a["action"] == "promotion_rollback" for a in log)
    ok = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10})
    assert ok.status_code == 200, ok.text
    assert _quarantine_row(qid)["status"] == "promoted"

    # The day still seals only real facts: 2 seeded + the promoted one.
    seal = client.post("/api/redeem/seal", json={}).json()
    assert seal["fact_count"] == 3
    assert seal["total_amount"] == 40.0


def test_promotion_receipt_is_atomic(client, day):
    """A success receipt always comes with a linked, open fact for the event."""
    _issue(client, "K-ATOMIC", "NEW10", day)
    bad = _redeem(client, "K-ATOMIC", "GHOST", 10).json()
    qid = bad["quarantine_id"]
    r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["status"] == "promoted"
    row = _quarantine_row(qid)
    assert row["status"] == "promoted" and row["fact_id"] == body["fact_id"]
    facts = _fact_rows()
    linked = [f for f in facts if f["id"] == body["fact_id"]]
    assert len(linked) == 1
    assert linked[0]["event_id"] == bad["event_id"]
    assert linked[0]["status"] == "open"
    assert linked[0]["coupon_code"] == "K-ATOMIC"


def test_still_quarantined_event_wins_over_open_fact(client, day):
    """「双栖同键」: an open fact beside a still-quarantined row is torn.

    Quarantine is authoritative while a row is unpromoted, so the half formal
    row is rolled back instead of being pinnable, and the quarantine row stays
    the only home.
    """
    _issue(client, "K-DUAL", "NEW10", day)
    bad = _redeem(client, "K-DUAL", "GHOST", 10).json()
    qid = bad["quarantine_id"]
    stray = _insert_fact("K-DUAL", "NEW10", 10, day, bad["event_id"], status="open")

    prev = client.get("/api/redeem/preview").json()
    assert stray not in [f["id"] for f in prev["facts"]]
    assert prev["excluded_quarantine_count"] == 1
    row = _quarantine_row(qid)
    assert row["status"] == "quarantined" and row["fact_id"] is None
    assert all(f["id"] != stray for f in _fact_rows())

    dash = client.get("/api/dashboard").json()
    assert dash["dual_home_event_count"] == 0
    assert dash["formal_plus_quarantine"] == dash["event_illegal_plus_legal"]

    audit = client.get("/api/audit", params={"target_type": "quarantine", "target_id": qid}).json()
    assert any(a["action"] == "promotion_rollback" for a in audit)


def test_sealed_fact_forces_quarantine_clear(client, day):
    """「已进 sealed 的行强制清隔离」: formal wins once the fact is sealed."""
    _issue(client, "K-PIN", "NEW10", day)
    bad = _redeem(client, "K-PIN", "GHOST", 10).json()
    qid = bad["quarantine_id"]
    # Simulate a seal that pinned the event while the quarantine row survived.
    fact_id = _insert_fact("K-PIN", "NEW10", 10, day, bad["event_id"], status="sealed")

    dash = client.get("/api/dashboard").json()
    row = _quarantine_row(qid)
    assert row["status"] == "promoted" and row["fact_id"] == fact_id
    assert dash["dual_home_event_count"] == 0
    assert dash["formal_plus_quarantine"] == dash["event_illegal_plus_legal"]
    assert sum(1 for f in _fact_rows() if f["event_id"] == bad["event_id"]) == 1

    audit = client.get("/api/audit", params={"target_type": "quarantine", "target_id": qid}).json()
    assert any(a["action"] == "promote_reconciled" for a in audit)


def test_promotion_racing_seal_leaves_no_half_batch(client, day):
    """「晋升中钉进半截」: interleaving promotion wins or loses cleanly."""
    import threading

    qids = []
    for i in range(8):
        code = f"K-RACE-{i}"
        _issue(client, code, "NEW10", day)
        bad = _redeem(client, code, "GHOST", 10).json()
        assert bad["status"] == "quarantined"
        qids.append(bad["quarantine_id"])

    results = {"promote": [], "seal": None}
    barrier = threading.Barrier(3)

    def do_promote(ids):
        barrier.wait()
        for qid in ids:
            r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10})
            results["promote"].append((qid, r.status_code))

    def do_seal():
        barrier.wait()
        results["seal"] = client.post("/api/redeem/seal", json={})

    t1 = threading.Thread(target=do_promote, args=(qids[:4],))
    t2 = threading.Thread(target=do_promote, args=(qids[4:],))
    t3 = threading.Thread(target=do_seal)
    for t in (t1, t2, t3):
        t.start()
    for t in (t1, t2, t3):
        t.join()

    assert results["seal"].status_code == 200, results["seal"].text
    seal = results["seal"].json()
    facts = _fact_rows()
    sealed = [f for f in facts if f["status"] == "sealed"]
    promoted = {qid for qid, code in results["promote"] if code == 200}

    # Every successful promotion is promoted with its fact inside the batch.
    for qid in promoted:
        row = _quarantine_row(qid)
        assert row["status"] == "promoted"
        assert any(f["id"] == row["fact_id"] and f["status"] == "sealed" for f in facts)

    # Nothing still quarantined appears in the sealed rows.
    for qid, code in results["promote"]:
        if code == 409:
            row = _quarantine_row(qid)
            assert row["status"] == "quarantined"
            assert all(f["event_id"] != row["event_id"] for f in sealed)

    # The batch is whole: header equals its own rows, and the day is not
    # partially sealed.
    batch = client.get("/api/batches").json()[0]
    assert batch["status"] == "sealed"
    assert batch["fact_count"] == len(sealed) == seal["fact_count"]
    assert batch["total_amount"] == round(sum(float(f["amount"]) for f in sealed), 2)
    assert batch["total_amount"] == seal["total_amount"]
    # No torn/pending batch was left claiming the day.
    assert all(b["status"] == "sealed" for b in client.get("/api/batches").json())

    dash = client.get("/api/dashboard").json()
    assert dash["dual_home_event_count"] == 0
    assert dash["homeless_event_count"] == 0
    assert dash["formal_plus_quarantine"] == dash["event_illegal_plus_legal"]
    assert dash["day_sealed"] is True


def test_torn_pending_batch_is_aborted_on_read(client, day):
    """A batch stuck before ``sealed`` releases its rows and never is current."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE redeem_facts SET status='sealed', batch_id=1 WHERE coupon_code='C-1001'")
        conn.execute(
            "INSERT INTO redeem_batches(business_day, status, fact_count, total_amount, note) "
            "VALUES(?,?,?,?,?)",
            (day, "pending", 1, 10.0, "half"),
        )
        # Move the row onto the torn batch instead of the fake id above.
        bid = conn.execute("SELECT MAX(id) FROM redeem_batches").fetchone()[0]
        conn.execute("UPDATE redeem_facts SET batch_id=? WHERE coupon_code='C-1001'", (bid,))
        conn.commit()
    finally:
        conn.close()

    batches = client.get("/api/batches").json()
    torn = next(b for b in batches if b["note"] == "half")
    assert torn["status"] == "aborted" and torn["fact_count"] == 0
    assert client.get("/api/dashboard").json()["day_sealed"] is False
    row = next(f for f in _fact_rows() if f["coupon_code"] == "C-1001")
    assert row["status"] == "open" and row["batch_id"] is None

    # The day is still sealable and seals only the real facts.
    seal = client.post("/api/redeem/seal", json={}).json()
    assert seal["fact_count"] == 2
    assert seal["total_amount"] == 30.0
