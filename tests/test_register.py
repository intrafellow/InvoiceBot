def test_register_user(client):
    resp = client.post("/api/v1/user/register", json={"tg_id": "tg_unittest3", "full_name": "Test User"})
    assert resp.status_code == 200
    assert "message" in resp.json()
