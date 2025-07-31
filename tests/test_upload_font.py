import os


def test_upload_font(client):
    font_path = os.path.join(os.path.dirname(__file__), "test_font.ttf")
    with open(font_path, "rb") as f:
        resp = client.post(
            "/api/v1/template/upload-font?tg_id=tg_unittest",
            files={"ttf_file": ("test_font.ttf", f, "font/ttf")}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["font_name"].endswith(".ttf")
