def test_download_pdf_returns_pdf(client, monkeypatch):
    # --- choose ONE of these patch lines, based on app.py import style ---

    # Case A: app.py has "from pdf_utils import html_to_pdf"
    monkeypatch.setattr("app.html_to_pdf", lambda html: b"%PDF-1.4 fake\n%%EOF")

    # Case B: app.py has "import pdf_utils"
    # monkeypatch.setattr("pdf_utils.html_to_pdf", lambda html: b"%PDF-1.4 fake\n%%EOF")

    payload = {
        "client": "ACME Ltd",
        "currency_symbol": "£",
        "tax_rate": 0.2,
        "line_items": [{"description": "Design", "quantity": 1, "unit_price": 10.0}],
    }
    res = client.post("/download-pdf", json=payload)
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/pdf"
    data = res.get_data()
    assert data.startswith(b"%PDF")
