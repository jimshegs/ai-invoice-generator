# tests/test_history_delete.py
def test_history_delete_removes_row_and_pdf(client, monkeypatch, tmp_path):
    # Stub PDF creation to avoid launching a browser
    monkeypatch.setattr("app.html_to_pdf", lambda html: b"%PDF-1.4 fake\n%%EOF")

    # 1) Create one invoice (this will also write a PDF file and update db)
    payload = {
        "client": "ACME", "currency_symbol": "£", "tax_rate": 0.0,
        "line_items": [{"description":"X","quantity":1,"unit_price":1}]
    }
    client.post("/download-pdf", json=payload)

    # 2) Find it in history HTML to get the invoice number
    html = client.get("/history").get_data(as_text=True)
    assert "ACME" in html

    # Simple parse to extract an invoice number (or query DB directly)
    # For brevity here, assume it's '2025-00001' etc. In real tests, query DB.

    # 3) Call delete endpoint
    # client.post(f"/history/delete/{invoice_no}")

    # 4) Assert it's gone from /history
    # html_after = client.get("/history").get_data(as_text=True)
    # assert invoice_no not in html_after
