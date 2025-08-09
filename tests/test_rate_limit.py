import time

def test_parse_rate_limit_429(client, monkeypatch):
    import app as appmod

    # Clear cache & make parsing fast (no OpenAI latency)
    appmod.ai_parse_invoice_cached.cache_clear()
    monkeypatch.setattr(
        appmod, "_ai_parse_invoice_uncached",
        lambda prompt, version: {"ai_processed": True, "client": "ACME", "line_items": []}
    )

    # Start on a fresh second boundary to avoid spillover from previous tests
    time.sleep(1.1)

    payload = {"text": "Invoice ACME for X"}
    res1 = client.post("/parse", json=payload)  # 1st within same second
    res2 = client.post("/parse", json=payload)  # 2nd within same second
    res3 = client.post("/parse", json=payload)  # 3rd within same second -> should be 429

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res3.status_code == 429
