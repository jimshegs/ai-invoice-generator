import io, json

def test_generate_accepts_logo_upload(client):
    # Build the multipart body:
    payload = {
        "client": "ACME Ltd",
        "sender": "Your Biz",
        "line_items": [{"description":"Design", "quantity":1, "unit_price":10}],
        "tax_rate": 0.0,
        "currency_symbol": "£",
        "amount_paid": 0.0
    }
    data = {
        "payload": json.dumps(payload),
        "logo": (io.BytesIO(b"fakepngbytes"), "logo.png"),
    }
    res = client.post("/generate", data=data, content_type="multipart/form-data")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Loose check: the <img> tag for logo got rendered
    assert "<img" in html and "logo" in html.lower()
