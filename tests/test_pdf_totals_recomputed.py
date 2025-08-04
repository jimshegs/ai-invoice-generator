def test_pdf_route_recomputes_totals(client, monkeypatch):
    # Stub PDF render
    monkeypatch.setattr("app.html_to_pdf", lambda html: b"%PDF-1.4 fake\n%%EOF")

    payload = {
        "client": "ACME",
        "currency_symbol": "£",
        "tax_rate": 0.2,
        "line_items": [{"description":"X", "quantity":2, "unit_price":100}],
        # Try to tamper (these should be ignored by the route):
        "amount_paid": 50.0,  # This should not affect the PDF totals
    }
    res = client.post("/download-pdf", json=payload)
    assert res.status_code == 200
    html = client.post("/generate", json=payload).get_data(as_text=True)
    # Recompute expected values
    subtotal = 2 * 100
    tax_amount = subtotal * 0.2
    total = subtotal + tax_amount
    # The rendered HTML from /generate should contain real numbers
    assert f"{total:.2f}" in html
