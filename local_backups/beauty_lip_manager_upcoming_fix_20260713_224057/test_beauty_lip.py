import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["BEAUTY_LIP_DB_PATH"] = str(Path(__file__).with_name("test_beauty_lip.db"))
os.environ["BEAUTY_LIP_ENABLE_DEMO_SEED"] = "true"

from backend.app import DB_PATH, app, init_db


@pytest.fixture(autouse=True)
def fresh_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db(seed=True)
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture()
def client():
    return TestClient(app)


def login(client, email, password="Demo12345!"):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def first_slot(client, token):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    slots = client.get("/api/availability", params={"specialist_id": 1, "service_id": 1, "date": tomorrow}, headers=auth(token)).json()["items"]
    assert slots
    return slots[0]["start_at"]


def test_registration_login_and_salon_search(client):
    created = client.post("/api/auth/register", json={"email": "new@beautylip.dev", "password": "Strong123!", "first_name": "Nina", "last_name": "Nova"})
    assert created.status_code == 200
    token = created.json()["token"]
    assert client.get("/api/auth/me", headers=auth(token)).json()["user"]["role"] == "customer"
    salons = client.get("/api/salons", params={"q": "Velvet"}).json()["items"]
    assert salons and salons[0]["name"] == "Velvet Lip Studio"


def test_customer_booking_e2e_and_manager_confirmation(client):
    customer = login(client, "customer@beautylip.dev")
    manager = login(client, "manager@beautylip.dev")
    start_at = first_slot(client, customer)
    booking = client.post("/api/appointments", json={"specialist_id": 1, "service_id": 1, "start_at": start_at}, headers=auth(customer))
    assert booking.status_code == 200, booking.text
    appointment_id = booking.json()["appointment"]["id"]
    customer_list = client.get("/api/appointments", headers=auth(customer)).json()["items"]
    assert any(item["id"] == appointment_id for item in customer_list)
    dashboard = client.get("/api/manager/dashboard", headers=auth(manager)).json()
    assert any(item["id"] == appointment_id for item in dashboard["today_appointments"])
    confirmed = client.patch(f"/api/manager/appointments/{appointment_id}/status", json={"status": "confirmed"}, headers=auth(manager))
    assert confirmed.status_code == 200
    assert confirmed.json()["appointment"]["status"] == "confirmed"


def test_double_booking_is_rejected_and_reschedule_works(client):
    c1 = login(client, "customer@beautylip.dev")
    client.post("/api/auth/register", json={"email": "second@beautylip.dev", "password": "Strong123!", "first_name": "Second", "last_name": "User"})
    c2 = login(client, "second@beautylip.dev", "Strong123!")
    start_at = first_slot(client, c1)
    first = client.post("/api/appointments", json={"specialist_id": 1, "service_id": 1, "start_at": start_at}, headers=auth(c1))
    assert first.status_code == 200
    duplicate = client.post("/api/appointments", json={"specialist_id": 1, "service_id": 1, "start_at": start_at}, headers=auth(c2))
    assert duplicate.status_code == 409
    new_start = (datetime.fromisoformat(start_at.replace("Z", "+00:00")) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    moved = client.patch(f"/api/appointments/{first.json()['appointment']['id']}/reschedule", json={"start_at": new_start}, headers=auth(c1))
    assert moved.status_code == 200, moved.text
    assert moved.json()["appointment"]["start_at"] == new_start


def test_role_restrictions_and_review_permissions(client):
    customer = login(client, "customer@beautylip.dev")
    manager = login(client, "manager@beautylip.dev")
    admin = login(client, "admin@beautylip.dev")
    assert client.get("/api/manager/dashboard", headers=auth(customer)).status_code == 403
    assert client.get("/api/admin/salons", headers=auth(manager)).status_code == 403
    assert client.get("/api/admin/salons", headers=auth(admin)).status_code == 200
    start_at = first_slot(client, customer)
    appointment = client.post("/api/appointments", json={"specialist_id": 1, "service_id": 1, "start_at": start_at}, headers=auth(customer)).json()["appointment"]
    early_review = client.post("/api/reviews", json={"appointment_id": appointment["id"], "rating": 5}, headers=auth(customer))
    assert early_review.status_code == 409
