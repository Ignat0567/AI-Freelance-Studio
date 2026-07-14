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


def test_inactive_user_cannot_login():
    with main.db() as conn:
        ts = main.now_utc()
        conn.execute("INSERT OR IGNORE INTO users(email,password_hash,first_name,last_name,role,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("inactive@beautylip.dev", main.hash_password("BeautyLip123!"), "Inactive", "User", "customer", 0, ts, ts))
    r = client.post("/api/auth/login", json={"email": "inactive@beautylip.dev", "password": "BeautyLip123!"})
    assert r.status_code == 401


def test_cors_rejects_unconfigured_origin():
    r = client.options("/api/salons", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"


def test_public_endpoints_do_not_expose_internal_owner_or_user_ids():
    salons = client.get("/api/salons").json()["items"]
    assert salons
    assert "owner_id" not in salons[0]
    specialist = client.get("/api/specialists/1").json()
    assert "user_id" not in specialist
    h = auth("customer@beautylip.dev")
    assert client.post("/api/favorites/salons/1", headers=h).status_code == 200
    assert client.post("/api/favorites/specialists/1", headers=h).status_code == 200
    favorites = client.get("/api/favorites", headers=h).json()
    assert "owner_id" not in favorites["salons"][0]
    assert "user_id" not in favorites["specialists"][0]


def test_customer_note_length_is_limited():
    customer = auth("customer@beautylip.dev")
    payload = {"salon_branch_id": 1, "specialist_id": 1, "service_id": 1, "start_at": first_slot(), "customer_note": "x" * 501}
    assert client.post("/api/appointments", json=payload, headers=customer).status_code == 422


def test_invalid_dates_and_pagination_are_rejected():
    customer = auth("customer@beautylip.dev")
    assert client.get("/api/availability?specialist_id=1&service_id=1&date=not-a-date").status_code == 422
    assert client.get("/api/salons?offset=-1").status_code == 422
    payload = {"salon_branch_id": 1, "specialist_id": 1, "service_id": 1, "start_at": "not-a-datetime"}
    assert client.post("/api/appointments", json=payload, headers=customer).status_code == 422


def test_favorites_reject_missing_entities():
    customer = auth("customer@beautylip.dev")
    assert client.post("/api/favorites/salons/999999", headers=customer).status_code == 404
    assert client.post("/api/favorites/specialists/999999", headers=customer).status_code == 404


def test_booking_rejects_mismatched_branch():
    customer = auth("customer@beautylip.dev")
    slot = first_slot()
    payload = {"salon_branch_id": 999, "specialist_id": 1, "service_id": 1, "start_at": slot}
    created = client.post("/api/appointments", json=payload, headers=customer)
    assert created.status_code == 400


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


def test_manager_and_professional_cannot_update_unowned_appointment():
    customer = auth("customer@beautylip.dev")
    manager = auth("manager@beautylip.dev")
    slot = first_slot()
    appt = client.post("/api/appointments", json={"salon_branch_id": 1, "specialist_id": 1, "service_id": 1, "start_at": slot}, headers=customer).json()
    with main.db() as conn:
        ts = main.now_utc()
        outsider_manager_id = conn.execute("INSERT INTO users(email,password_hash,first_name,last_name,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("outsider-manager@beautylip.dev", main.hash_password("BeautyLip123!"), "Out", "Manager", "manager", ts, ts)).lastrowid
        outsider_pro_id = conn.execute("INSERT INTO users(email,password_hash,first_name,last_name,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("outsider-pro@beautylip.dev", main.hash_password("BeautyLip123!"), "Out", "Pro", "professional", ts, ts)).lastrowid
        salon_id = conn.execute("INSERT INTO salons(owner_id,name,city,created_at,updated_at) VALUES(?,?,?,?,?)", (outsider_manager_id, "Other Salon", "Berlin", ts, ts)).lastrowid
        branch_id = conn.execute("INSERT INTO salon_branches(salon_id,name,city) VALUES(?,?,?)", (salon_id, "Other Branch", "Berlin")).lastrowid
        conn.execute("INSERT INTO specialists(user_id,salon_branch_id,display_name) VALUES(?,?,?)", (outsider_pro_id, branch_id, "Other Pro"))
    outsider_manager = auth("outsider-manager@beautylip.dev")
    outsider_pro = auth("outsider-pro@beautylip.dev")
    assert client.patch(f"/api/manager/appointments/{appt['id']}/status", json={"status": "confirmed"}, headers=outsider_manager).status_code == 403
    assert client.patch(f"/api/manager/appointments/{appt['id']}/status", json={"status": "confirmed"}, headers=outsider_pro).status_code == 403
    assert client.patch(f"/api/manager/appointments/{appt['id']}/status", json={"status": "confirmed"}, headers=manager).status_code == 200


def test_admin_and_manager_reporting():
    assert client.get("/api/admin/statistics", headers=auth("admin@beautylip.dev")).json()["users"] >= 4
    dashboard = client.get("/api/manager/dashboard", headers=auth("manager@beautylip.dev"))
    assert dashboard.status_code == 200
    assert "expected_revenue" in dashboard.json()
