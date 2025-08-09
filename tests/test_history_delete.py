# tests/test_history_delete.py
def test_history_delete_removes_row_and_pdf(client, monkeypatch):
    # Avoid launching a real browser for PDF generation
    monkeypatch.setattr("app.html_to_pdf", lambda html: b"%PDF-1.4 fake\n%%EOF")

    # --- 1) Create an invoice row in DB via /generate (this persists payload_json) ---
    invoice_no = "INV-DELETE-0001"
    base_payload = {
        "invoice_no": invoice_no,          # respected by /generate (no auto numbering)
        "client": "ACME",
        "client_address": "",
        "sender": "Your Biz",
        "sender_address": "",
        "invoice_date": "2025-08-01",
        "due_date": "",
        "payment_terms": "",
        "currency_symbol": "£",
        "tax_rate": 0.0,
        "amount_paid": 0.0,
        "line_items": [{"description": "X", "quantity": 1, "unit_price": 10.0}],
    }
    res_gen = client.post("/generate", json=base_payload)
    assert res_gen.status_code == 200

    # --- 2) Generate PDF and update pdf_path via /download-pdf ---
    res_pdf = client.post("/download-pdf", json={
        "invoice_no": invoice_no,          # same invoice number as above
        "client": "ACME",
        "currency_symbol": "£",
        "tax_rate": 0.0,
        "line_items": [{"description": "X", "quantity": 1, "unit_price": 10.0}],
    })
    assert res_pdf.status_code == 200
    assert res_pdf.headers["Content-Type"] == "application/pdf"

    # --- 3) Verify the row exists and PDF file is on disk ---
    from db import get_invoice
    row = get_invoice(invoice_no)
    assert row is not None
    pdf_rel = row["pdf_path"]            # e.g., "invoices/INV-DELETE-0001.pdf"
    assert pdf_rel and pdf_rel.endswith(f"{invoice_no}.pdf")

    import os
    from app import app as flask_app
    pdf_abs = os.path.join(flask_app.static_folder, pdf_rel)
    assert os.path.exists(pdf_abs)

    # --- 4) Delete the invoice via the endpoint ---
    res_del = client.post(f"/history/delete/{invoice_no}")
    assert res_del.status_code == 200
    body = res_del.get_json()
    assert body and body.get("ok") is True

    # --- 5) Assert DB row is gone and file removed ---
    assert get_invoice(invoice_no) is None
    assert not os.path.exists(pdf_abs)

    # --- 6) History page no longer lists the invoice ---
    html_after = client.get("/history").get_data(as_text=True)
    assert invoice_no not in html_after
