from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "beauty_lip_test.db"))
    main.init_db()
    return TestClient(main.app)


def login(client, email="customer@beautylip.local"):
    res = client.post("/api/auth/login", json={"email": email, "password": "BeautyLip123!"})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def next_available_slot(client, specialist_id=1, service_id=2):
    for offset in range(1, 7):
        day = date.today() + timedelta(days=offset)
        res = client.get(f"/api/availability?specialist_id={specialist_id}&service_id={service_id}&day={day.isoformat()}")
        assert res.status_code == 200, res.text
        for slot in res.json()["items"]:
            if slot["available"]:
                return slot["start_at"]
    raise AssertionError("No available seeded slot found")


def test_registration_login_and_role_restriction(client):
    reg = client.post("/api/auth/register", json={"email": "new@beautylip.local", "password": "StrongPass123", "first_name": "New", "last_name": "Customer"})
    assert reg.status_code == 200, reg.text
    customer_token = reg.json()["token"]
    denied = client.get("/api/manager/dashboard", headers=headers(customer_token))
    assert denied.status_code == 403
    manager_token = login(client, "manager@beautylip.local")
    allowed = client.get("/api/manager/dashboard", headers=headers(manager_token))
    assert allowed.status_code == 200


def test_salon_search_and_service_retrieval(client):
    res = client.get("/api/salons?q=brow&city=Berlin")
    assert res.status_code == 200
    assert any("Velvet" in item["name"] for item in res.json()["items"])
    detail = client.get("/api/salons/1")
    assert detail.status_code == 200
    assert detail.json()["services"]
    assert detail.json()["specialists"]


def test_booking_double_booking_reschedule_cancel_and_persistence(client):
    token = login(client)
    start_at = next_available_slot(client)
    payload = {"salon_branch_id": 1, "specialist_id": 1, "service_id": 2, "start_at": start_at}
    created = client.post("/api/appointments", json=payload, headers=headers(token))
    assert created.status_code == 200, created.text
    duplicate = client.post("/api/appointments", json=payload, headers=headers(token))
    assert duplicate.status_code == 409
    appointment_id = created.json()["id"]
    new_start = next_available_slot(client, specialist_id=1, service_id=2)
    if new_start == start_at:
        new_start = (datetime.fromisoformat(start_at) + timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    moved = client.patch(f"/api/appointments/{appointment_id}/reschedule", json={"start_at": new_start}, headers=headers(token))
    assert moved.status_code == 200, moved.text
    cancelled = client.patch(f"/api/appointments/{appointment_id}/cancel", headers=headers(token))
    assert cancelled.status_code == 200
    listed = client.get("/api/appointments", headers=headers(token))
    assert any(item["id"] == appointment_id for item in listed.json()["items"])


def test_manager_status_flow_and_review_permission(client):
    customer = login(client)
    manager = login(client, "manager@beautylip.local")
    start_at = next_available_slot(client)
    created = client.post("/api/appointments", json={"salon_branch_id": 1, "specialist_id": 1, "service_id": 2, "start_at": start_at}, headers=headers(customer))
    appt_id = created.json()["id"]
    early_review = client.post("/api/reviews", json={"appointment_id": appt_id, "rating": 5, "text": "Lovely"}, headers=headers(customer))
    assert early_review.status_code == 409
    confirmed = client.patch(f"/api/manager/appointments/{appt_id}/status", json={"status": "confirmed"}, headers=headers(manager))
    assert confirmed.status_code == 200
    completed = client.patch(f"/api/manager/appointments/{appt_id}/status", json={"status": "completed"}, headers=headers(manager))
    assert completed.status_code == 200
    review = client.post("/api/reviews", json={"appointment_id": appt_id, "rating": 5, "text": "Calm premium service"}, headers=headers(customer))
    assert review.status_code == 200, review.text
    repeated = client.post("/api/reviews", json={"appointment_id": appt_id, "rating": 4}, headers=headers(customer))
    assert repeated.status_code == 409


def test_admin_stats_requires_admin(client):
    customer = login(client)
    assert client.get("/api/admin/stats", headers=headers(customer)).status_code == 403
    admin = login(client, "admin@beautylip.local")
    stats = client.get("/api/admin/stats", headers=headers(admin))
    assert stats.status_code == 200
    assert stats.json()["users"] >= 5
