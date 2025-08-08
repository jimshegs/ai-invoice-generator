def test_parse_route_uses_lru_cache(client, monkeypatch):
    """
    Integration with Flask test client:
    - Same text twice → only one uncached call
    - Whitespace-only variation → still cached
    - Bump SCHEMA_VERSION → forces a new uncached call
    """
    import app as appmod

    # Clean cache before we start
    appmod.ai_parse_invoice_cached.cache_clear()

    calls = {"n": 0}

    def fake_uncached(prompt, version):
        calls["n"] += 1
        # Simulate a parsed payload your route would normally return
        return {"ai_processed": True, "client": "ACME", "line_items": []}

    # Monkeypatch the uncached function that the cached wrapper calls
    monkeypatch.setattr(appmod, "_ai_parse_invoice_uncached", fake_uncached)

    # 1) First request → MISS
    res1 = client.post("/parse", json={"text": "Invoice ACME for design work"})
    assert res1.status_code == 200
    assert calls["n"] == 1

    # 2) Same request → HIT (no new uncached call)
    res2 = client.post("/parse", json={"text": "Invoice ACME for design work"})
    assert res2.status_code == 200
    assert calls["n"] == 1

    # 3) Whitespace-only variant → still HIT (route normalizes the prompt)
    res3 = client.post("/parse", json={"text": "  Invoice   ACME  for   design   work  \n"})
    assert res3.status_code == 200
    assert calls["n"] == 1

    # 4) Version bump → MISS (forces new key)
    monkeypatch.setattr(appmod, "SCHEMA_VERSION", "test-v2", raising=False)
    res4 = client.post("/parse", json={"text": "Invoice ACME for design work"})
    assert res4.status_code == 200
    assert calls["n"] == 2
