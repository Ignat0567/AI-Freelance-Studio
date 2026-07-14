import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_beauty_lip.db"
os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient
import main


def setup_module():
    if os.path.exists("test_beauty_lip.db"):
        os.remove("test_beauty_lip.db")
    main.DB_PATH = "./test_beauty_lip.db"
    main.exec_schema()
    main.seed()


client = TestClient(main.app)


def auth(email):
    r = client.post("/api/auth/login", json={"email": email, "password": "BeautyLip123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def first_slot():
    day = date.today()
    for _ in range(7):
        r = client.get(f"/api/availability?specialist_id=1&service_id=1&date={day.isoformat()}")
        assert r.status_code == 200
        slots = r.json()["slots"]
        if slots:
            return slots[0]["start_at"]
        day += timedelta(days=1)
    raise AssertionError("No available seed slot found")


def test_registration_login_and_salon_search():
    r = client.post("/api/auth/register", json={"email": "new@beautylip.dev", "password": "BeautyLip123!", "first_name": "New", "last_name": "Guest"})
    assert r.status_code == 200
    salons = client.get("/api/salons?q=Plum").json()["items"]
    assert salons and salons[0]["services"] and salons[0]["specialists"]


def test_role_authorization_blocks_customer_from_manager_and_admin():
    h = auth("customer@beautylip.dev")
    assert client.get("/api/manager/dashboard", headers=h).status_code == 403
    assert client.get("/api/admin/statistics", headers=h).status_code == 403


def test_booking_double_booking_reschedule_cancel_and_manager_confirmation():
    customer = auth("customer@beautylip.dev")
    manager = auth("manager@beautylip.dev")
    slot = first_slot()
    payload = {"salon_branch_id": 1, "specialist_id": 1, "service_id": 1, "start_at": slot}
    created = client.post("/api/appointments", json=payload, headers=customer)
    assert created.status_code == 200, created.text
    appt_id = created.json()["id"]
    duplicate = client.post("/api/appointments", json=payload, headers=customer)
    assert duplicate.status_code == 409
    assert client.patch(f"/api/manager/appointments/{appt_id}/status", json={"status": "confirmed"}, headers=manager).status_code == 200
    new_slot = first_slot()
    rescheduled = client.patch(f"/api/appointments/{appt_id}/reschedule", json={"start_at": new_slot}, headers=customer)
    assert rescheduled.status_code == 200, rescheduled.text
    cancelled = client.patch(f"/api/appointments/{appt_id}/cancel", headers=customer)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled_by_customer"


def test_review_permission_and_notifications():
    customer = auth("customer@beautylip.dev")
    manager = auth("manager@beautylip.dev")
    slot = first_slot()
    appt = client.post("/api/appointments", json={"salon_branch_id": 1, "specialist_id": 1, "service_id": 1, "start_at": slot}, headers=customer).json()
    assert client.post("/api/reviews", json={"appointment_id": appt["id"], "rating": 5}, headers=customer).status_code == 403
    assert client.patch(f"/api/manager/appointments/{appt['id']}/status", json={"status": "confirmed"}, headers=manager).status_code == 200
    assert client.patch(f"/api/manager/appointments/{appt['id']}/status", json={"status": "completed"}, headers=manager).status_code == 200
    review = client.post("/api/reviews", json={"appointment_id": appt["id"], "rating": 5, "text": "Excellent."}, headers=customer)
    assert review.status_code == 200, review.text
    assert client.post("/api/reviews", json={"appointment_id": appt["id"], "rating": 4}, headers=customer).status_code == 409
    assert client.get("/api/notifications", headers=customer).json()


def test_admin_and_manager_reporting():
    assert client.get("/api/admin/statistics", headers=auth("admin@beautylip.dev")).json()["users"] >= 4
    dashboard = client.get("/api/manager/dashboard", headers=auth("manager@beautylip.dev"))
    assert dashboard.status_code == 200
    assert "expected_revenue" in dashboard.json()
