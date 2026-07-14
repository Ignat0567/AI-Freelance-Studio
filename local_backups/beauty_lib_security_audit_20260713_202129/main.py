from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./beauty_lip.db")
DB_PATH = DB_URL.replace("sqlite:///", "", 1) if DB_URL.startswith("sqlite:///") else "beauty_lip.db"
ACTIVE_STATUSES = {"pending", "confirmed", "in_progress"}
STATUS_FLOW = {
    "pending": {"confirmed", "cancelled_by_customer", "cancelled_by_salon"},
    "confirmed": {"in_progress", "completed", "cancelled_by_customer", "cancelled_by_salon", "no_show"},
    "in_progress": {"completed", "no_show"},
}

app = FastAPI(title="Beauty Lip API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return secrets.compare_digest(candidate, digest)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 first_name TEXT NOT NULL, last_name TEXT NOT NULL, phone TEXT, role TEXT NOT NULL CHECK(role IN ('customer','professional','manager','admin')),
 avatar_url TEXT, preferred_city TEXT, language TEXT DEFAULT 'en', notification_preferences TEXT DEFAULT '{}', is_active INTEGER DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS salons (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, description TEXT, phone TEXT, email TEXT,
 address TEXT NOT NULL, city TEXT NOT NULL, latitude REAL, longitude REAL, logo_url TEXT, cover_url TEXT,
 rating_average REAL DEFAULT 0, rating_count INTEGER DEFAULT 0, approval_status TEXT DEFAULT 'approved', is_active INTEGER DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS salon_branches (id INTEGER PRIMARY KEY, salon_id INTEGER NOT NULL REFERENCES salons(id), name TEXT NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL, latitude REAL, longitude REAL, phone TEXT, timezone TEXT DEFAULT 'UTC', is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS specialists (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id), display_name TEXT NOT NULL, title TEXT, biography TEXT, experience_years INTEGER DEFAULT 0, avatar_url TEXT, rating_average REAL DEFAULT 0, rating_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS service_categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, icon TEXT, display_order INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY, salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id), category_id INTEGER REFERENCES service_categories(id), name TEXT NOT NULL, description TEXT, duration_minutes INTEGER NOT NULL, price REAL NOT NULL, currency TEXT DEFAULT 'EUR', buffer_before_minutes INTEGER DEFAULT 0, buffer_after_minutes INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS specialist_services (specialist_id INTEGER REFERENCES specialists(id), service_id INTEGER REFERENCES services(id), custom_price REAL, custom_duration INTEGER, is_active INTEGER DEFAULT 1, PRIMARY KEY(specialist_id, service_id));
CREATE TABLE IF NOT EXISTS working_schedules (id INTEGER PRIMARY KEY, specialist_id INTEGER NOT NULL REFERENCES specialists(id), weekday INTEGER NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS schedule_exceptions (id INTEGER PRIMARY KEY, specialist_id INTEGER NOT NULL REFERENCES specialists(id), date TEXT NOT NULL, start_time TEXT, end_time TEXT, exception_type TEXT NOT NULL CHECK(exception_type IN ('day_off','custom_hours','blocked','break')), reason TEXT);
CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES users(id), salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id), specialist_id INTEGER NOT NULL REFERENCES specialists(id), service_id INTEGER NOT NULL REFERENCES services(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT DEFAULT 'UTC', price REAL NOT NULL, currency TEXT DEFAULT 'EUR', status TEXT NOT NULL DEFAULT 'pending', payment_status TEXT DEFAULT 'not_required', customer_note TEXT, salon_note TEXT, cancellation_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_appointments_slot ON appointments(specialist_id, start_at, end_at) WHERE status IN ('pending','confirmed','in_progress');
CREATE TABLE IF NOT EXISTS favorite_salons (customer_id INTEGER REFERENCES users(id), salon_id INTEGER REFERENCES salons(id), created_at TEXT NOT NULL, PRIMARY KEY(customer_id, salon_id));
CREATE TABLE IF NOT EXISTS favorite_specialists (customer_id INTEGER REFERENCES users(id), specialist_id INTEGER REFERENCES specialists(id), created_at TEXT NOT NULL, PRIMARY KEY(customer_id, specialist_id));
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, appointment_id INTEGER UNIQUE NOT NULL REFERENCES appointments(id), customer_id INTEGER NOT NULL REFERENCES users(id), salon_id INTEGER NOT NULL REFERENCES salons(id), specialist_id INTEGER NOT NULL REFERENCES specialists(id), rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), text TEXT, moderation_status TEXT DEFAULT 'visible', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY, actor_user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id INTEGER, metadata TEXT, created_at TEXT NOT NULL);
"""


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True) if Path(DB_PATH).parent != Path(".") else None
    with get_db() as db:
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        now = utc_now()
        users = [
            ("customer@beautylip.local", "Demo", "Customer", "customer"),
            ("manager@beautylip.local", "Mira", "Manager", "manager"),
            ("admin@beautylip.local", "Ada", "Admin", "admin"),
            ("lina@beautylip.local", "Lina", "Rose", "professional"),
            ("sofia@beautylip.local", "Sofia", "Velvet", "professional"),
        ]
        for email, first, last, role in users:
            db.execute("INSERT INTO users(email,password_hash,first_name,last_name,role,preferred_city,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (email, hash_password("BeautyLip123!"), first, last, role, "Berlin", now, now))
        db.execute("INSERT INTO salons(owner_id,name,description,phone,email,address,city,latitude,longitude,logo_url,cover_url,rating_average,rating_count,created_at,updated_at) VALUES(2,'Velvet Room Beauty','Premium brows, lips, hair styling and nail care in a calm studio.','+49 30 100000','hello@velvet.example','Rosenstrasse 12','Berlin',52.52,13.405,'/assets/logo-velvet.svg','/assets/cover-velvet.jpg',4.8,42,?,?)", (now, now))
        db.execute("INSERT INTO salon_branches(salon_id,name,address,city,latitude,longitude,phone,timezone) VALUES(1,'Velvet Room Mitte','Rosenstrasse 12','Berlin',52.52,13.405,'+49 30 100000','Europe/Berlin')")
        cats = [('Hair','scissors',1),('Nails','nail',2),('Brows and Lashes','eye',3),('Lips and Makeup','lip',4),('Wellness','leaf',5)]
        db.executemany("INSERT INTO service_categories(name,icon,display_order) VALUES(?,?,?)", cats)
        services = [(1,1,'Signature Hair Gloss','Tone and gloss treatment',60,89),(1,3,'Brow Design','Shape and tint',45,49),(1,4,'Beauty Lip Care','Hydrating lip care and makeup finish',40,55),(1,2,'Soft Rose Manicure','Classic manicure with premium polish',50,62)]
        db.executemany("INSERT INTO services(salon_branch_id,category_id,name,description,duration_minutes,price) VALUES(?,?,?,?,?,?)", services)
        specs = [(4,1,'Lina Rose','Senior brow and lip artist','Ten years creating soft, polished beauty looks.',10),(5,1,'Sofia Velvet','Hair and nail specialist','Color, gloss and detail-led nail work.',8)]
        db.executemany("INSERT INTO specialists(user_id,salon_branch_id,display_name,title,biography,experience_years) VALUES(?,?,?,?,?,?)", specs)
        db.executemany("INSERT INTO specialist_services(specialist_id,service_id,is_active) VALUES(?,?,1)", [(1,2),(1,3),(2,1),(2,4)])
        for spec in (1, 2):
            for weekday in range(0, 6):
                db.execute("INSERT INTO working_schedules(specialist_id,weekday,start_time,end_time) VALUES(?,?,?,?)", (spec, weekday, "09:00", "18:00"))
            today = date.today().isoformat()
            db.execute("INSERT INTO schedule_exceptions(specialist_id,date,start_time,end_time,exception_type,reason) VALUES(?,?,?,?,?,?)", (spec, today, "13:00", "14:00", "break", "Lunch"))


@app.on_event("startup")
def startup() -> None:
    init_db()


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    first_name: str
    last_name: str
    phone: str | None = None


class LoginIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str


class AppointmentIn(BaseModel):
    salon_branch_id: int
    specialist_id: int
    service_id: int
    start_at: datetime
    customer_note: str | None = None


class RescheduleIn(BaseModel):
    start_at: datetime


class StatusIn(BaseModel):
    status: str
    note: str | None = None


class ReviewIn(BaseModel):
    appointment_id: int
    rating: int = Field(ge=1, le=5)
    text: str | None = None


def current_user(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.removeprefix("Bearer ")
    with get_db() as db:
        row = db.execute("SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND users.is_active=1", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or expired session")
    return dict(row)


def require_role(*roles: str):
    def dep(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return dep


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def service_for(db: sqlite3.Connection, specialist_id: int, service_id: int) -> dict[str, Any]:
    row = db.execute("""SELECT s.*, COALESCE(ss.custom_duration, s.duration_minutes) AS actual_duration, COALESCE(ss.custom_price, s.price) AS actual_price FROM services s JOIN specialist_services ss ON ss.service_id=s.id WHERE s.id=? AND ss.specialist_id=? AND s.is_active=1 AND ss.is_active=1""", (service_id, specialist_id)).fetchone()
    if not row:
        raise HTTPException(404, "Service is not available for this specialist")
    return dict(row)


def ensure_booking_resources(db: sqlite3.Connection, salon_branch_id: int, specialist_id: int, service_id: int) -> dict[str, Any]:
    service = service_for(db, specialist_id, service_id)
    specialist = db.execute("SELECT salon_branch_id FROM specialists WHERE id=? AND is_active=1", (specialist_id,)).fetchone()
    if not specialist or specialist["salon_branch_id"] != salon_branch_id or service["salon_branch_id"] != salon_branch_id:
        raise HTTPException(400, "Booking resources do not belong to requested branch")
    return service


def ensure_can_change_appointment(user: dict[str, Any], appt: sqlite3.Row) -> None:
    if user["role"] == "customer" and appt["customer_id"] == user["id"]:
        return
    if user["role"] in {"manager", "admin"}:
        return
    raise HTTPException(403, "Cannot change this appointment")


def is_available(db: sqlite3.Connection, specialist_id: int, service_id: int, start_at: datetime, exclude_appointment_id: int | None = None) -> tuple[bool, str, datetime]:
    service = service_for(db, specialist_id, service_id)
    start_at = start_at.astimezone(timezone.utc)
    end_at = start_at + timedelta(minutes=service["actual_duration"] + service["buffer_after_minutes"])
    check_start = start_at - timedelta(minutes=service["buffer_before_minutes"])
    if start_at < datetime.now(timezone.utc) + timedelta(minutes=30):
        return False, "Booking lead time is 30 minutes", end_at
    sched = db.execute("SELECT * FROM working_schedules WHERE specialist_id=? AND weekday=? AND is_active=1", (specialist_id, start_at.weekday())).fetchall()
    if not sched:
        return False, "Specialist is not working that day", end_at
    local_start = start_at.time()
    local_end = end_at.time()
    if not any(time.fromisoformat(s["start_time"]) <= local_start and local_end <= time.fromisoformat(s["end_time"]) for s in sched):
        return False, "Requested time is outside working hours", end_at
    exceptions = db.execute("SELECT * FROM schedule_exceptions WHERE specialist_id=? AND date=?", (specialist_id, start_at.date().isoformat())).fetchall()
    for exc in exceptions:
        if exc["exception_type"] == "day_off":
            return False, "Specialist has a day off", end_at
        exc_start = datetime.combine(start_at.date(), time.fromisoformat(exc["start_time"]), tzinfo=timezone.utc)
        exc_end = datetime.combine(start_at.date(), time.fromisoformat(exc["end_time"]), tzinfo=timezone.utc)
        if overlaps(check_start, end_at, exc_start, exc_end):
            return False, f"Time overlaps {exc['exception_type']}", end_at
    params: list[Any] = [specialist_id]
    sql = "SELECT * FROM appointments WHERE specialist_id=? AND status IN ('pending','confirmed','in_progress')"
    if exclude_appointment_id:
        sql += " AND id<>?"
        params.append(exclude_appointment_id)
    for appt in db.execute(sql, params).fetchall():
        if overlaps(check_start, end_at, parse_dt(appt["start_at"]), parse_dt(appt["end_at"])):
            return False, "Time overlaps an existing appointment", end_at
    return True, "available", end_at


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "product": "Beauty Lip"}


@app.post("/api/auth/register")
def register(payload: RegisterIn) -> dict[str, Any]:
    init_db()
    now = utc_now()
    with get_db() as db:
        try:
            cur = db.execute("INSERT INTO users(email,password_hash,first_name,last_name,phone,role,created_at,updated_at) VALUES(?,?,?,?,?,'customer',?,?)", (payload.email.lower(), hash_password(payload.password), payload.first_name, payload.last_name, payload.phone, now, now))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Email already registered") from exc
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, cur.lastrowid, now))
    return {"token": token, "user": {"id": cur.lastrowid, "email": payload.email.lower(), "role": "customer"}}


@app.post("/api/auth/login")
def login(payload: LoginIn) -> dict[str, Any]:
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email=? AND is_active=1", (payload.email.lower(),)).fetchone()
        if not row or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, row["id"], utc_now()))
    return {"token": token, "user": {"id": row["id"], "email": row["email"], "role": row["role"], "first_name": row["first_name"]}}


@app.get("/api/users/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {k: v for k, v in user.items() if k != "password_hash"}


@app.get("/api/salons")
def salons(q: str = "", city: str = "", category: str = "", sort: str = "recommended") -> dict[str, Any]:
    init_db()
    clauses = ["salons.is_active=1", "salons.approval_status='approved'"]
    params: list[Any] = []
    if q:
        clauses.append("(salons.name LIKE ? OR salons.description LIKE ? OR services.name LIKE ? OR specialists.display_name LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if city:
        clauses.append("salons.city LIKE ?")
        params.append(f"%{city}%")
    if category:
        clauses.append("service_categories.name LIKE ?")
        params.append(f"%{category}%")
    order = "salons.rating_average DESC" if sort in {"recommended", "highest_rated"} else "salons.name ASC"
    sql = f"""SELECT DISTINCT salons.* FROM salons JOIN salon_branches ON salon_branches.salon_id=salons.id LEFT JOIN services ON services.salon_branch_id=salon_branches.id LEFT JOIN service_categories ON service_categories.id=services.category_id LEFT JOIN specialists ON specialists.salon_branch_id=salon_branches.id WHERE {' AND '.join(clauses)} ORDER BY {order}"""
    with get_db() as db:
        return {"items": [dict(r) for r in db.execute(sql, params).fetchall()]}


@app.get("/api/salons/{salon_id}")
def salon_detail(salon_id: int) -> dict[str, Any]:
    init_db()
    with get_db() as db:
        salon = row_to_dict(db.execute("SELECT * FROM salons WHERE id=?", (salon_id,)).fetchone())
        if not salon:
            raise HTTPException(404, "Salon not found")
        branches = [dict(r) for r in db.execute("SELECT * FROM salon_branches WHERE salon_id=?", (salon_id,)).fetchall()]
        services = [dict(r) for r in db.execute("SELECT services.*, service_categories.name AS category FROM services LEFT JOIN service_categories ON service_categories.id=services.category_id WHERE salon_branch_id IN (SELECT id FROM salon_branches WHERE salon_id=?)", (salon_id,)).fetchall()]
        specialists = [dict(r) for r in db.execute("SELECT * FROM specialists WHERE salon_branch_id IN (SELECT id FROM salon_branches WHERE salon_id=?) AND is_active=1", (salon_id,)).fetchall()]
    return {"salon": salon, "branches": branches, "services": services, "specialists": specialists}


@app.get("/api/services")
def services() -> dict[str, Any]:
    init_db()
    with get_db() as db:
        return {"items": [dict(r) for r in db.execute("SELECT services.*, service_categories.name AS category FROM services LEFT JOIN service_categories ON service_categories.id=services.category_id WHERE services.is_active=1").fetchall()]}


@app.get("/api/availability")
def availability(specialist_id: int, service_id: int, day: date = Query(...)) -> dict[str, Any]:
    init_db()
    slots = []
    with get_db() as db:
        service_for(db, specialist_id, service_id)
        current = datetime.combine(day, time(9, 0), tzinfo=timezone.utc)
        end = datetime.combine(day, time(18, 0), tzinfo=timezone.utc)
        while current < end:
            ok, reason, _ = is_available(db, specialist_id, service_id, current)
            slots.append({"start_at": current.isoformat(), "available": ok, "reason": reason if not ok else None})
            current += timedelta(minutes=30)
    return {"items": slots}


@app.post("/api/appointments")
def create_appointment(payload: AppointmentIn, user: dict[str, Any] = Depends(require_role("customer"))) -> dict[str, Any]:
    init_db()
    start = payload.start_at.astimezone(timezone.utc)
    with get_db() as db:
        service = ensure_booking_resources(db, payload.salon_branch_id, payload.specialist_id, payload.service_id)
        db.execute("BEGIN IMMEDIATE")
        ok, reason, end = is_available(db, payload.specialist_id, payload.service_id, start)
        if not ok:
            db.execute("ROLLBACK")
            raise HTTPException(409, reason)
        now = utc_now()
        cur = db.execute("INSERT INTO appointments(customer_id,salon_branch_id,specialist_id,service_id,start_at,end_at,price,currency,status,payment_status,customer_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (user["id"], payload.salon_branch_id, payload.specialist_id, payload.service_id, start.isoformat(), end.isoformat(), service["actual_price"], service["currency"], "pending", "not_required", payload.customer_note, now, now))
        db.execute("INSERT INTO notifications(user_id,type,title,body,created_at) VALUES(?,?,?,?,?)", (user["id"], "booking_created", "Booking requested", "Your Beauty Lip appointment request was created.", now))
        db.execute("COMMIT")
        return {"id": cur.lastrowid, "status": "pending", "start_at": start.isoformat(), "end_at": end.isoformat(), "payment_status": "not_required"}


@app.get("/api/appointments")
def list_appointments(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    init_db()
    if user["role"] == "customer":
        where, params = "appointments.customer_id=?", [user["id"]]
    elif user["role"] in {"manager", "admin"}:
        where, params = "1=1", []
    else:
        where, params = "specialists.user_id=?", [user["id"]]
    sql = f"""SELECT appointments.*, salons.name AS salon_name, specialists.display_name AS specialist_name, services.name AS service_name, salon_branches.address FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id JOIN salons ON salons.id=salon_branches.salon_id JOIN specialists ON specialists.id=appointments.specialist_id JOIN services ON services.id=appointments.service_id WHERE {where} ORDER BY start_at DESC"""
    with get_db() as db:
        return {"items": [dict(r) for r in db.execute(sql, params).fetchall()]}


@app.patch("/api/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: int, payload: RescheduleIn, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    init_db()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        appt = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt:
            db.execute("ROLLBACK")
            raise HTTPException(404, "Appointment not found")
        try:
            ensure_can_change_appointment(user, appt)
        except HTTPException:
            db.execute("ROLLBACK")
            raise
        ok, reason, end = is_available(db, appt["specialist_id"], appt["service_id"], payload.start_at, appointment_id)
        if not ok:
            db.execute("ROLLBACK")
            raise HTTPException(409, reason)
        db.execute("UPDATE appointments SET start_at=?, end_at=?, updated_at=? WHERE id=?", (payload.start_at.astimezone(timezone.utc).isoformat(), end.isoformat(), utc_now(), appointment_id))
        db.execute("COMMIT")
    return {"id": appointment_id, "status": "rescheduled", "start_at": payload.start_at.astimezone(timezone.utc).isoformat()}


@app.patch("/api/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    status = "cancelled_by_customer" if user["role"] == "customer" else "cancelled_by_salon"
    with get_db() as db:
        appt = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt:
            raise HTTPException(404, "Appointment not found")
        ensure_can_change_appointment(user, appt)
        db.execute("UPDATE appointments SET status=?, updated_at=? WHERE id=?", (status, utc_now(), appointment_id))
    return {"id": appointment_id, "status": status}


@app.patch("/api/manager/appointments/{appointment_id}/status")
def manager_status(appointment_id: int, payload: StatusIn, user: dict[str, Any] = Depends(require_role("manager", "admin"))) -> dict[str, Any]:
    with get_db() as db:
        appt = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt:
            raise HTTPException(404, "Appointment not found")
        if payload.status not in STATUS_FLOW.get(appt["status"], set()):
            raise HTTPException(409, "Invalid appointment status transition")
        db.execute("UPDATE appointments SET status=?, salon_note=?, updated_at=? WHERE id=?", (payload.status, payload.note, utc_now(), appointment_id))
        db.execute("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,metadata,created_at) VALUES(?,?,?,?,?,?)", (user["id"], "appointment.status", "appointment", appointment_id, payload.status, utc_now()))
    return {"id": appointment_id, "status": payload.status}


@app.post("/api/favorites/salons/{salon_id}")
def favorite_salon(salon_id: int, user: dict[str, Any] = Depends(require_role("customer"))) -> dict[str, Any]:
    with get_db() as db:
        if not db.execute("SELECT 1 FROM salons WHERE id=? AND is_active=1", (salon_id,)).fetchone():
            raise HTTPException(404, "Salon not found")
        db.execute("INSERT OR IGNORE INTO favorite_salons(customer_id,salon_id,created_at) VALUES(?,?,?)", (user["id"], salon_id, utc_now()))
    return {"salon_id": salon_id, "favorited": True}


@app.get("/api/favorites")
def favorites(user: dict[str, Any] = Depends(require_role("customer"))) -> dict[str, Any]:
    with get_db() as db:
        salons = [dict(r) for r in db.execute("SELECT salons.* FROM favorite_salons JOIN salons ON salons.id=favorite_salons.salon_id WHERE customer_id=?", (user["id"],)).fetchall()]
    return {"salons": salons, "specialists": []}


@app.post("/api/reviews")
def review(payload: ReviewIn, user: dict[str, Any] = Depends(require_role("customer"))) -> dict[str, Any]:
    with get_db() as db:
        appt = db.execute("SELECT appointments.*, salon_branches.salon_id FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id WHERE appointments.id=?", (payload.appointment_id,)).fetchone()
        if not appt or appt["customer_id"] != user["id"]:
            raise HTTPException(403, "Review is limited to your own appointment")
        if appt["status"] != "completed":
            raise HTTPException(409, "Review is available after completion")
        now = utc_now()
        try:
            cur = db.execute("INSERT INTO reviews(appointment_id,customer_id,salon_id,specialist_id,rating,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (payload.appointment_id, user["id"], appt["salon_id"], appt["specialist_id"], payload.rating, payload.text, now, now))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Appointment already has a review") from exc
    return {"id": cur.lastrowid, "rating": payload.rating, "moderation_status": "visible"}


@app.get("/api/notifications")
def notifications(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with get_db() as db:
        return {"items": [dict(r) for r in db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()], "email_available": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_PASSWORD"))}


@app.get("/api/manager/dashboard")
def manager_dashboard(user: dict[str, Any] = Depends(require_role("manager", "admin"))) -> dict[str, Any]:
    today = date.today().isoformat()
    with get_db() as db:
        rows = [dict(r) for r in db.execute("SELECT status, COUNT(*) count, COALESCE(SUM(price),0) revenue FROM appointments WHERE substr(start_at,1,10)=? GROUP BY status", (today,)).fetchall()]
        upcoming = [dict(r) for r in db.execute("SELECT appointments.*, services.name service_name, specialists.display_name specialist_name FROM appointments JOIN services ON services.id=appointments.service_id JOIN specialists ON specialists.id=appointments.specialist_id ORDER BY start_at LIMIT 20").fetchall()]
    return {"today": rows, "upcoming_schedule": upcoming, "estimated_revenue_note": "Revenue is an estimate, not a settled financial record."}


@app.get("/api/admin/stats")
def admin_stats(user: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    with get_db() as db:
        return {"users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0], "salons": db.execute("SELECT COUNT(*) FROM salons").fetchone()[0], "appointments": db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0], "audit_logs": db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]}


init_db()
