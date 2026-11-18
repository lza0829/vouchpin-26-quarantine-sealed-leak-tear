from __future__ import annotations


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


def test_illegal_redeems_go_to_quarantine_with_reason_codes(client, day):
    # (coupon, campaign, amount, expected reason, whether to have an issue event)
    cases = [
        ("Q-NOISSUE", "NEW10", 10, "no_issue", False),
        ("Q-NOCAMP", "GHOST", 10, "campaign_not_found", False),
        ("Q-NEG", "NEW10", -5, "non_positive_amount", True),
        ("Q-ZERO", "NEW10", 0, "non_positive_amount", True),
    ]
    for code, camp, amount, expected, has_issue in cases:
        if has_issue:
            _issue(client, code, camp, day)
        r = _redeem(client, code, camp, amount)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "quarantined", (code, body)
        assert body["reason_code"] == expected, (code, body)
        assert body["fact_id"] is None

    rows = client.get("/api/quarantine").json()
    by_code = {r["coupon_code"]: r for r in rows}
    for code, _, _, expected, _ in cases:
        assert by_code[code]["reason_code"] == expected
        assert by_code[code]["status"] == "quarantined"

    # No fact was materialised, and the pinning preview / total never sees them.
    prev = client.get("/api/redeem/preview").json()
    assert prev["excluded_quarantine_count"] == len(cases)
    codes = {c[0] for c in cases}
    assert all(f["coupon_code"] not in codes for f in prev["facts"])

    dash = client.get("/api/dashboard").json()
    assert dash["illegal_redeem_count"] == len(cases)
    assert dash["quarantine_count"] == len(cases)
    # seeded day has 2 legitimate facts; quarantine never inflates this
    assert dash["fact_count"] == 2
    assert dash["redeem_total_today"] == prev["open_total"]


def test_mismatched_campaign_and_duplicate_are_quarantined(client, day):
    _issue(client, "Q-DUP", "VIP20", day)
    # wrong campaign for the issue
    r = _redeem(client, "Q-DUP", "NEW10", 10)
    assert r.json()["reason_code"] == "campaign_mismatch"
    # legal redeem of the same coupon on the right campaign
    r = _redeem(client, "Q-DUP", "VIP20", 20)
    assert r.json()["status"] == "accepted"
    # second redeem is refused into quarantine, not into a fact
    r = _redeem(client, "Q-DUP", "VIP20", 20)
    assert r.json()["status"] == "quarantined"
    assert r.json()["reason_code"] == "already_redeemed"


def test_promote_after_validation_then_seal(client, day):
    _issue(client, "Q-PROMO", "NEW10", day)
    bad = _redeem(client, "Q-PROMO", "GHOST", 10).json()
    assert bad["status"] == "quarantined" and bad["reason_code"] == "campaign_not_found"
    qid = bad["quarantine_id"]

    # Promoting without the campaign fixed must still fail validation.
    r = client.post(f"/api/quarantine/{qid}/promote", json={})
    assert r.status_code == 409
    assert "campaign_mismatch" in r.text or "campaign_not_found" in r.text

    r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10, "note": "补正活动"})
    assert r.status_code == 200, r.text
    fact_id = r.json()["fact_id"]
    assert r.json()["status"] == "promoted"

    # Promoted row is now a normal open fact: preview includes it, quarantine does not.
    prev = client.get("/api/redeem/preview").json()
    assert fact_id in [f["id"] for f in prev["facts"]]
    assert prev["excluded_quarantine_count"] == 0

    rows = client.get("/api/quarantine").json()
    row = next(x for x in rows if x["id"] == qid)
    assert row["status"] == "promoted" and row["fact_id"] == fact_id

    seal = client.post("/api/redeem/seal", json={"note": "EOD"}).json()
    assert fact_id in [f["id"] for f in client.get("/api/facts").json() if f["status"] == "sealed"]
    assert seal["fact_count"] == 3  # 2 seeded + promoted
    batch = client.get("/api/batches").json()[0]
    assert batch["fact_count"] == 3


def test_promotion_after_seal_is_refused(client, day):
    _issue(client, "Q-LATE", "TEA5", day)
    bad = _redeem(client, "Q-LATE", "GHOST", 5).json()
    qid = bad["quarantine_id"]

    assert client.post("/api/redeem/seal", json={}).status_code == 200

    r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "TEA5", "amount": 5})
    assert r.status_code == 409
    assert "已钉扎" in r.json()["detail"]

    # sealed batch total is untouched by the still-quarantined row
    batch = client.get("/api/batches").json()[0]
    assert batch["total_amount"] == client.get("/api/dashboard").json()["redeem_total_today"]
    row = next(x for x in client.get("/api/quarantine").json() if x["id"] == qid)
    assert row["status"] == "quarantined"


def test_discard_is_terminal_and_audited(client, day):
    _issue(client, "Q-DROP", "NEW10", day)
    bad = _redeem(client, "Q-DROP", "NEW10", -1).json()
    qid = bad["quarantine_id"]

    r = client.post(f"/api/quarantine/{qid}/discard", json={"note": "测试夹具"})
    assert r.status_code == 200 and r.json()["status"] == "discarded"

    r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10})
    assert r.status_code == 409
    assert "discarded" in r.json()["detail"]

    r = client.post(f"/api/quarantine/{qid}/discard", json={})
    assert r.status_code == 409

    log = client.get("/api/audit", params={"target_type": "quarantine", "target_id": qid}).json()
    actions = [entry["action"] for entry in log]
    assert actions == ["discard", "quarantine"]

    # discarded row never becomes a fact or part of the day total
    prev = client.get("/api/redeem/preview").json()
    assert prev["open_count"] == 2
    assert client.get("/api/dashboard").json()["quarantine_discarded_count"] == 1


def test_sealed_batch_does_not_absorb_quarantine(client, day):
    _issue(client, "Q-SEAL", "VIP20", day)
    bad = _redeem(client, "Q-SEAL", "GHOST", 20).json()
    assert bad["status"] == "quarantined"

    assert client.post("/api/redeem/seal", json={}).status_code == 200
    batch = client.get("/api/batches").json()[0]
    fact_ids = [f["id"] for f in client.get("/api/facts").json() if f["status"] == "sealed"]

    # explicitly assert the quarantined row never landed in sealed facts
    assert all(f["coupon_code"] != "Q-SEAL" for f in client.get("/api/facts").json())
    assert batch["fact_count"] == len(fact_ids) == 2
    assert batch["total_amount"] == 30.0

    # redeeming on a sealed day still enters quarantine with its own reason
    _issue(client, "Q-SEAL2", "VIP20", day)
    r = _redeem(client, "Q-SEAL2", "VIP20", 20)
    assert r.json()["status"] == "quarantined"
    assert r.json()["reason_code"] == "day_sealed"


def test_promote_and_seal_race_never_pins_unpromoted(client, day):
    """A promotion racing a seal may go either way, but never half way.

    Both endpoints run under BEGIN IMMEDIATE on the same sqlite file. The
    invariant under test: a quarantine row is pinned iff its promotion
    committed first; a row still quarantined is never inside the sealed batch,
    and the batch total always equals the sum of its own fact rows.
    """
    import threading

    qids = {2: [], 3: []}
    for i in range(6):
        code = f"Q-RACE-{i}"
        _issue(client, code, "NEW10", day)
        bad = _redeem(client, code, "GHOST", 10).json()
        assert bad["status"] == "quarantined"
        qids[2 if i % 2 else 3].append(bad["quarantine_id"])

    results = {"promote": [], "seal": None}

    def do_promote(ids):
        for qid in ids:
            r = client.post(f"/api/quarantine/{qid}/promote", json={"campaign_code": "NEW10", "amount": 10})
            results["promote"].append((qid, r.status_code))

    def do_seal():
        results["seal"] = client.post("/api/redeem/seal", json={}).status_code

    t1 = threading.Thread(target=do_promote, args=(qids[2],))
    t2 = threading.Thread(target=do_promote, args=(qids[3],))
    t3 = threading.Thread(target=do_seal)
    for t in (t1, t2, t3):
        t.start()
    for t in (t1, t2, t3):
        t.join()

    assert results["seal"] == 200
    facts = client.get("/api/facts").json()
    sealed = [f for f in facts if f["status"] == "sealed"]
    rows = {r["id"]: r for r in client.get("/api/quarantine").json()}

    promoted = [qid for qid, code in results["promote"] if code == 200]
    for qid in promoted:
        assert rows[qid]["status"] == "promoted"
        assert any(f["id"] == rows[qid]["fact_id"] and f["status"] == "sealed" for f in facts), (
            "promotion committed before seal must be pinned"
        )
    for qid, row in rows.items():
        if row["status"] == "quarantined":
            assert all(f["coupon_code"] != row["coupon_code"] for f in sealed)
        if row["status"] == "promoted" and qid not in promoted:
            # promoted by the other thread's post-seal retry is impossible here
            raise AssertionError("unexpected promotion")

    batch = client.get("/api/batches").json()[0]
    assert batch["fact_count"] == len(sealed)
    assert batch["total_amount"] == round(sum(float(f["amount"]) for f in sealed), 2)
