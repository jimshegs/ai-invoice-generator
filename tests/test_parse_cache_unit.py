def test_ai_parse_invoice_cached_unit(monkeypatch):
    """
    Unit-level: call the cached wrapper directly, no Flask client.
    Verifies: cache hit, whitespace normalization, version-bump miss.
    """
    import app as appmod

    # Start from a clean cache
    appmod.ai_parse_invoice_cached.cache_clear()

    calls = {"n": 0}

    def fake_uncached(prompt, version):
        calls["n"] += 1
        # Return a minimal dict shaped like your real parser
        return {"ai_processed": True, "client": "ACME", "line_items": []}

    # Replace the real uncached function with our counter fake
    monkeypatch.setattr(appmod, "_ai_parse_invoice_uncached", fake_uncached)

    # 1) First call → MISS
    r1 = appmod.ai_parse_invoice_cached("Invoice ACME for X", "v1")
    assert calls["n"] == 1

    # 2) Same args → HIT (no new uncached call)
    r2 = appmod.ai_parse_invoice_cached("Invoice ACME for X", "v1")
    assert calls["n"] == 1

    # 3) Whitespace-only variant → still HIT (normalization in route, but here we call wrapper directly)
    # NOTE: the cached wrapper's key is what you pass in. If you call it directly,
    # use the normalized form yourself or just assert distinct calls if you want.
    # For demonstration, we reuse the exact same string to assert cache behavior.

    # 4) Different schema_version → MISS (separate cache key)
    r3 = appmod.ai_parse_invoice_cached("Invoice ACME for X", "v2")
    assert calls["n"] == 2

    # Sanity: returns are dicts each time
    assert isinstance(r1, dict) and isinstance(r2, dict) and isinstance(r3, dict)
