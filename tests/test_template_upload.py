import os


def test_upload_template(client):
    test_pdf = os.path.join(os.path.dirname(__file__), "test_invoice.pdf")
    with open(test_pdf, "rb") as f:
        resp = client.post(
            "/api/v1/template/upload-template?tg_id=tg_unittest",
            files={"file": ("test_invoice.pdf", f, "application/pdf")}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "fonts" in data
    assert "parsed_data" in data
