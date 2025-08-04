def test_generate_returns_html(client):
    payload = {
        "client": "ACME Ltd",
        "client_address": "42 Road, London",
        "sender": "Your Biz",
        "sender_address": "10 High St",
        "invoice_no": "",                    # blank means “auto or default”
        "invoice_date": "2025-07-06",
        "due_date": "2025-07-20",
        "payment_terms": "Net 14, Bank XYZ",
        "currency_symbol": "£",
        "tax_rate": 0.2,
        "amount_paid": 50.0,
        "line_items": [
            {"description": "Design", "quantity": 2, "unit_price": 300.0},
            {"description": "Consulting", "quantity": 3, "unit_price": 75.0},
        ],
    }
    res = client.post("/generate", json=payload)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Very light checks so the test is robust:
    assert "ACME Ltd" in html
    assert "Subtotal" in html
    assert "Balance Due" in html
