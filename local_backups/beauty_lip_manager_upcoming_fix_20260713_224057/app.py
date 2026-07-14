from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field


BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("BEAUTY_LIP_DB_PATH", BASE_DIR / "beauty_lip.db"))
SECRET_KEY = os.getenv("BEAUTY_LIP_SECRET_KEY", "dev-secret-change-me")
UTC = timezone.utc

app = FastAPI(title="Beauty Lip API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


@contextmanager
def db(transaction: bool = False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if transaction:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if transaction:
            con.commit()
    except Exception:
        if transaction:
            con.rollback()
        raise
    finally:
        con.close()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$", 2)[2], digest)


def create_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(days=14)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with db(True) as con:
        con.execute("INSERT INTO sessions(token,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token, user_id, expires_at, utcnow()))
    return token


def current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    with db() as con:
        row = con.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id=sessions.user_id
            WHERE sessions.token=? AND sessions.expires_at > ? AND users.is_active=1
            """,
            (token, utcnow()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or expired session")
    return dict(row)


def require_roles(*roles: str):
    def dep(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient role permissions")
        return user
    return dep


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL, phone TEXT DEFAULT '', role TEXT NOT NULL CHECK(role IN ('customer','professional','manager','admin')), avatar_url TEXT DEFAULT '', preferred_city TEXT DEFAULT '', language TEXT DEFAULT 'en', notification_preferences TEXT DEFAULT '{}', is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS salons(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, description TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '', address TEXT DEFAULT '', city TEXT DEFAULT '', latitude REAL, longitude REAL, logo_url TEXT DEFAULT '', cover_url TEXT DEFAULT '', rating_average REAL DEFAULT 0, rating_count INTEGER DEFAULT 0, approval_status TEXT DEFAULT 'pending', is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS salon_branches(id INTEGER PRIMARY KEY, salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE, name TEXT NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL, latitude REAL, longitude REAL, phone TEXT DEFAULT '', timezone TEXT DEFAULT 'Europe/Berlin', is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS specialists(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id) ON DELETE CASCADE, display_name TEXT NOT NULL, title TEXT DEFAULT '', biography TEXT DEFAULT '', experience_years INTEGER DEFAULT 0, avatar_url TEXT DEFAULT '', rating_average REAL DEFAULT 0, rating_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS service_categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, icon TEXT DEFAULT '', display_order INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY, salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES service_categories(id), name TEXT NOT NULL, description TEXT DEFAULT '', duration_minutes INTEGER NOT NULL, price REAL NOT NULL, currency TEXT DEFAULT 'EUR', buffer_before_minutes INTEGER DEFAULT 0, buffer_after_minutes INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS specialist_services(specialist_id INTEGER NOT NULL REFERENCES specialists(id) ON DELETE CASCADE, service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE, custom_price REAL, custom_duration INTEGER, is_active INTEGER DEFAULT 1, PRIMARY KEY(specialist_id, service_id))""",
    """CREATE TABLE IF NOT EXISTS working_schedules(id INTEGER PRIMARY KEY, specialist_id INTEGER NOT NULL REFERENCES specialists(id) ON DELETE CASCADE, weekday INTEGER NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS schedule_exceptions(id INTEGER PRIMARY KEY, specialist_id INTEGER NOT NULL REFERENCES specialists(id) ON DELETE CASCADE, date TEXT NOT NULL, start_time TEXT, end_time TEXT, exception_type TEXT NOT NULL CHECK(exception_type IN ('day_off','custom_hours','blocked','break')), reason TEXT DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES users(id), salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id), specialist_id INTEGER NOT NULL REFERENCES specialists(id), service_id INTEGER NOT NULL REFERENCES services(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT DEFAULT 'Europe/Berlin', price REAL NOT NULL, currency TEXT DEFAULT 'EUR', status TEXT NOT NULL CHECK(status IN ('pending','confirmed','in_progress','completed','cancelled_by_customer','cancelled_by_salon','no_show')), payment_status TEXT DEFAULT 'not_required', customer_note TEXT DEFAULT '', salon_note TEXT DEFAULT '', cancellation_reason TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS favorite_salons(customer_id INTEGER NOT NULL REFERENCES users(id), salon_id INTEGER NOT NULL REFERENCES salons(id), created_at TEXT NOT NULL, PRIMARY KEY(customer_id,salon_id))""",
    """CREATE TABLE IF NOT EXISTS favorite_specialists(customer_id INTEGER NOT NULL REFERENCES users(id), specialist_id INTEGER NOT NULL REFERENCES specialists(id), created_at TEXT NOT NULL, PRIMARY KEY(customer_id,specialist_id))""",
    """CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, appointment_id INTEGER UNIQUE NOT NULL REFERENCES appointments(id), customer_id INTEGER NOT NULL REFERENCES users(id), salon_id INTEGER NOT NULL REFERENCES salons(id), specialist_id INTEGER NOT NULL REFERENCES specialists(id), rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), text TEXT DEFAULT '', moderation_status TEXT DEFAULT 'published', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, actor_user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_salons_city ON salons(city)",
    "CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(customer_id,start_at)",
    "CREATE INDEX IF NOT EXISTS idx_appointments_specialist_time ON appointments(specialist_id,start_at,end_at,status)",
]


def init_db(seed: bool = True) -> None:
    with db(True) as con:
        for statement in SCHEMA:
            con.execute(statement)
        if seed and con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed_data(con)


def insert_user(con, email: str, password: str, first: str, last: str, role: str, phone: str = "") -> int:
    now = utcnow()
    cur = con.execute(
        "INSERT INTO users(email,password_hash,first_name,last_name,phone,role,preferred_city,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (email, hash_password(password), first, last, phone, role, "Berlin", now, now),
    )
    return int(cur.lastrowid)


def seed_data(con) -> None:
    manager = insert_user(con, "manager@beautylip.dev", "Demo12345!", "Mila", "Manager", "manager", "+491111111")
    customer = insert_user(con, "customer@beautylip.dev", "Demo12345!", "Clara", "Customer", "customer", "+492222222")
    admin = insert_user(con, "admin@beautylip.dev", "Demo12345!", "Ada", "Admin", "admin")
    pro_user = insert_user(con, "sofia@beautylip.dev", "Demo12345!", "Sofia", "Rose", "professional")
    now = utcnow()
    con.execute("INSERT INTO service_categories(name,icon,display_order,is_active) VALUES('Lips','lip',1,1),('Nails','nail',2,1),('Brows','brow',3,1)")
    salon_id = con.execute("INSERT INTO salons(owner_id,name,description,phone,email,address,city,latitude,longitude,approval_status,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (manager, "Velvet Lip Studio", "Premium lip, brow and nail appointments in a calm ivory-plum salon.", "+493012345", "hello@velvet.example", "Rosa-Luxemburg-Strasse 10", "Berlin", 52.52, 13.405, "approved", 1, now, now)).lastrowid
    branch_id = con.execute("INSERT INTO salon_branches(salon_id,name,address,city,latitude,longitude,phone,timezone,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (salon_id, "Mitte Atelier", "Rosa-Luxemburg-Strasse 10", "Berlin", 52.52, 13.405, "+493012345", "Europe/Berlin")).lastrowid
    specialist_id = con.execute("INSERT INTO specialists(user_id,salon_branch_id,display_name,title,biography,experience_years,is_active) VALUES(?,?,?,?,?,?,1)", (pro_user, branch_id, "Sofia Rose", "Lip blush artist", "Specialist in natural lip blush and premium aftercare.", 7)).lastrowid
    service_id = con.execute("INSERT INTO services(salon_branch_id,category_id,name,description,duration_minutes,price,currency,buffer_before_minutes,buffer_after_minutes,is_active) VALUES(?,?,?,?,?,?,?,?,?,1)", (branch_id, 1, "Lip blush consultation", "Personal shade planning and treatment consultation.", 60, 79.0, "EUR", 0, 10)).lastrowid
    con.execute("INSERT INTO specialist_services(specialist_id,service_id,is_active) VALUES(?,?,1)", (specialist_id, service_id))
    for weekday in range(0, 5):
        con.execute("INSERT INTO working_schedules(specialist_id,weekday,start_time,end_time,is_active) VALUES(?,?,?,?,1)", (specialist_id, weekday, "09:00", "18:00"))
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    con.execute("INSERT INTO schedule_exceptions(specialist_id,date,start_time,end_time,exception_type,reason) VALUES(?,?,?,?,?,?)", (specialist_id, tomorrow, "13:00", "14:00", "break", "Lunch"))
    con.execute("INSERT INTO notifications(user_id,type,title,body,created_at) VALUES(?,?,?,?,?)", (customer, "welcome", "Welcome to Beauty Lip", "Find trusted salons and book instantly.", now))
    con.execute("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,metadata,created_at) VALUES(?,?,?,?,?,?)", (admin, "seed", "system", "beauty_lip", "{}", now))


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str
    last_name: str
    phone: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AppointmentIn(BaseModel):
    specialist_id: int
    service_id: int
    start_at: str
    customer_note: str = ""


class RescheduleIn(BaseModel):
    start_at: str


class StatusIn(BaseModel):
    status: str
    salon_note: str = ""


class ReviewIn(BaseModel):
    appointment_id: int
    rating: int = Field(ge=1, le=5)
    text: str = ""


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {k: user[k] for k in ("id", "email", "first_name", "last_name", "phone", "role", "preferred_city", "language") if k in user}


@app.on_event("startup")
def on_startup() -> None:
    init_db(seed=os.getenv("BEAUTY_LIP_ENABLE_DEMO_SEED", "true").lower() != "false")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Beauty Lip", "time": utcnow()}


@app.post("/api/auth/register")
def register(payload: RegisterIn):
    now = utcnow()
    with db(True) as con:
        try:
            cur = con.execute("INSERT INTO users(email,password_hash,first_name,last_name,phone,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (payload.email.lower(), hash_password(payload.password), payload.first_name, payload.last_name, payload.phone, "customer", now, now))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Email is already registered")
        user_id = int(cur.lastrowid)
    token = create_token(user_id)
    with db() as con:
        user = row_to_dict(con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    with db() as con:
        user = row_to_dict(con.execute("SELECT * FROM users WHERE email=?", (payload.email.lower(),)).fetchone())
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(user["id"]), "user": public_user(user)}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)):
    return {"user": public_user(user)}


@app.get("/api/salons")
def search_salons(q: str = "", city: str = "", category: str = "", sort: str = "recommended", limit: int = Query(20, le=100), offset: int = 0):
    clauses = ["salons.is_active=1", "salons.approval_status='approved'"]
    params: list[Any] = []
    if q:
        clauses.append("(salons.name LIKE ? OR salons.description LIKE ? OR services.name LIKE ? OR specialists.display_name LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like]
    if city:
        clauses.append("salons.city LIKE ?")
        params.append(f"%{city}%")
    if category:
        clauses.append("service_categories.name LIKE ?")
        params.append(f"%{category}%")
    order = "salons.rating_average DESC, salons.name" if sort in {"recommended", "highest_rated"} else "salons.name"
    sql = f"""
        SELECT DISTINCT salons.* FROM salons
        JOIN salon_branches ON salon_branches.salon_id=salons.id
        LEFT JOIN services ON services.salon_branch_id=salon_branches.id
        LEFT JOIN service_categories ON service_categories.id=services.category_id
        LEFT JOIN specialists ON specialists.salon_branch_id=salon_branches.id
        WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ? OFFSET ?
    """
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, (*params, limit, offset)).fetchall()]
    return {"items": rows, "limit": limit, "offset": offset}


@app.get("/api/salons/{salon_id}")
def salon_detail(salon_id: int):
    with db() as con:
        salon = row_to_dict(con.execute("SELECT * FROM salons WHERE id=? AND is_active=1", (salon_id,)).fetchone())
        if not salon:
            raise HTTPException(404, "Salon not found")
        branches = [dict(r) for r in con.execute("SELECT * FROM salon_branches WHERE salon_id=? AND is_active=1", (salon_id,)).fetchall()]
        branch_ids = [b["id"] for b in branches]
        specialists = [dict(r) for r in con.execute(f"SELECT * FROM specialists WHERE salon_branch_id IN ({','.join('?' for _ in branch_ids)}) AND is_active=1", branch_ids).fetchall()] if branch_ids else []
        services = [dict(r) for r in con.execute(f"SELECT services.*, service_categories.name category_name FROM services JOIN service_categories ON service_categories.id=services.category_id WHERE services.salon_branch_id IN ({','.join('?' for _ in branch_ids)}) AND services.is_active=1", branch_ids).fetchall()] if branch_ids else []
    return {"salon": salon, "branches": branches, "specialists": specialists, "services": services}


@app.get("/api/specialists/{specialist_id}")
def specialist_detail(specialist_id: int):
    with db() as con:
        specialist = row_to_dict(con.execute("SELECT * FROM specialists WHERE id=? AND is_active=1", (specialist_id,)).fetchone())
        if not specialist:
            raise HTTPException(404, "Specialist not found")
        services = [dict(r) for r in con.execute("SELECT services.* FROM services JOIN specialist_services ss ON ss.service_id=services.id WHERE ss.specialist_id=? AND ss.is_active=1", (specialist_id,)).fetchall()]
    return {"specialist": specialist, "services": services}


def service_for_specialist(con, specialist_id: int, service_id: int) -> dict[str, Any]:
    row = con.execute("""
        SELECT services.*, specialists.salon_branch_id specialist_branch_id, ss.custom_duration, ss.custom_price
        FROM services JOIN specialist_services ss ON ss.service_id=services.id
        JOIN specialists ON specialists.id=ss.specialist_id
        WHERE ss.specialist_id=? AND services.id=? AND ss.is_active=1 AND services.is_active=1 AND specialists.is_active=1
    """, (specialist_id, service_id)).fetchone()
    if not row:
        raise HTTPException(404, "Service is not available for this specialist")
    result = dict(row)
    result["effective_duration"] = result["custom_duration"] or result["duration_minutes"]
    result["effective_price"] = result["custom_price"] or result["price"]
    return result


def minutes_of_day(value: str) -> int:
    h, m = [int(x) for x in value.split(":")[:2]]
    return h * 60 + m


def date_at(date_str: str, minutes: int) -> datetime:
    d = datetime.fromisoformat(date_str).date()
    return datetime(d.year, d.month, d.day, minutes // 60, minutes % 60, tzinfo=UTC)


def appointment_overlaps(con, specialist_id: int, start: datetime, end: datetime, exclude_id: int | None = None) -> bool:
    params: list[Any] = [specialist_id, end.isoformat().replace("+00:00", "Z"), start.isoformat().replace("+00:00", "Z")]
    extra = ""
    if exclude_id:
        extra = "AND id<>?"
        params.append(exclude_id)
    row = con.execute(f"""
        SELECT id FROM appointments
        WHERE specialist_id=? AND status NOT IN ('cancelled_by_customer','cancelled_by_salon','no_show')
        AND start_at < ? AND end_at > ? {extra} LIMIT 1
    """, params).fetchone()
    return row is not None


def is_available(con, specialist_id: int, service_id: int, start: datetime, exclude_id: int | None = None) -> tuple[bool, str, datetime, dict[str, Any]]:
    service = service_for_specialist(con, specialist_id, service_id)
    duration = int(service["effective_duration"]) + int(service["buffer_before_minutes"] or 0) + int(service["buffer_after_minutes"] or 0)
    end = start + timedelta(minutes=duration)
    weekday = start.weekday()
    schedule = con.execute("SELECT * FROM working_schedules WHERE specialist_id=? AND weekday=? AND is_active=1", (specialist_id, weekday)).fetchone()
    if not schedule:
        return False, "Specialist does not work on this day", end, service
    day = start.date().isoformat()
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if start_min < minutes_of_day(schedule["start_time"]) or end_min > minutes_of_day(schedule["end_time"]):
        return False, "Requested time is outside working hours", end, service
    exceptions = con.execute("SELECT * FROM schedule_exceptions WHERE specialist_id=? AND date=?", (specialist_id, day)).fetchall()
    for ex in exceptions:
        if ex["exception_type"] == "day_off":
            return False, "Specialist has a day off", end, service
        if ex["start_time"] and ex["end_time"]:
            if start_min < minutes_of_day(ex["end_time"]) and end_min > minutes_of_day(ex["start_time"]):
                return False, f"Time overlaps {ex['exception_type']}", end, service
    if start < datetime.now(UTC) + timedelta(minutes=30):
        return False, "Booking lead time is 30 minutes", end, service
    if start > datetime.now(UTC) + timedelta(days=90):
        return False, "Booking window is limited to 90 days", end, service
    if appointment_overlaps(con, specialist_id, start, end, exclude_id):
        return False, "Time overlaps another appointment", end, service
    return True, "available", end, service


@app.get("/api/availability")
def availability(specialist_id: int, service_id: int, date: str):
    with db() as con:
        service = service_for_specialist(con, specialist_id, service_id)
        schedule = con.execute("SELECT * FROM working_schedules WHERE specialist_id=? AND weekday=? AND is_active=1", (specialist_id, datetime.fromisoformat(date).weekday())).fetchone()
        if not schedule:
            return {"items": [], "reason": "Specialist does not work on this day"}
        slots = []
        step = 30
        cur = minutes_of_day(schedule["start_time"])
        end_limit = minutes_of_day(schedule["end_time"])
        while cur + int(service["duration_minutes"]) <= end_limit:
            start = date_at(date, cur)
            ok, reason, _, _ = is_available(con, specialist_id, service_id, start)
            if ok:
                slots.append({"start_at": start.isoformat().replace("+00:00", "Z"), "label": start.strftime("%H:%M")})
            cur += step
    return {"items": slots}


@app.post("/api/appointments")
def create_appointment(payload: AppointmentIn, user: dict[str, Any] = Depends(require_roles("customer"))):
    start = parse_dt(payload.start_at)
    with db(True) as con:
        ok, reason, end, service = is_available(con, payload.specialist_id, payload.service_id, start)
        if not ok:
            raise HTTPException(409, reason)
        cur = con.execute("""
            INSERT INTO appointments(customer_id,salon_branch_id,specialist_id,service_id,start_at,end_at,price,currency,status,payment_status,customer_note,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (user["id"], service["specialist_branch_id"], payload.specialist_id, payload.service_id, start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), service["effective_price"], service["currency"], "pending", "not_required", payload.customer_note, utcnow(), utcnow()))
        appointment_id = int(cur.lastrowid)
        con.execute("INSERT INTO notifications(user_id,type,title,body,created_at) VALUES(?,?,?,?,?)", (user["id"], "booking_created", "Appointment requested", "Your Beauty Lip appointment request was created.", utcnow()))
    return {"appointment": get_appointment_for_user(appointment_id, user)}


def get_appointment_for_user(appointment_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with db() as con:
        row = con.execute("""
            SELECT appointments.*, salons.name salon_name, salon_branches.address, specialists.display_name specialist_name, services.name service_name
            FROM appointments
            JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id
            JOIN salons ON salons.id=salon_branches.salon_id
            JOIN specialists ON specialists.id=appointments.specialist_id
            JOIN services ON services.id=appointments.service_id
            WHERE appointments.id=?
        """, (appointment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Appointment not found")
    data = dict(row)
    if user["role"] == "customer" and data["customer_id"] != user["id"]:
        raise HTTPException(403, "Cannot access another customer's appointment")
    return data


@app.get("/api/appointments")
def list_appointments(section: str = "all", user: dict[str, Any] = Depends(current_user)):
    where = []
    params: list[Any] = []
    if user["role"] == "customer":
        where.append("appointments.customer_id=?")
        params.append(user["id"])
    elif user["role"] == "professional":
        where.append("specialists.user_id=?")
        params.append(user["id"])
    elif user["role"] == "manager":
        where.append("salons.owner_id=?")
        params.append(user["id"])
    if section == "upcoming":
        where.append("appointments.start_at>=?")
        params.append(utcnow())
    if section == "completed":
        where.append("appointments.status='completed'")
    if section == "cancelled":
        where.append("appointments.status LIKE 'cancelled_%'")
    clause = "WHERE " + " AND ".join(where) if where else ""
    with db() as con:
        rows = [dict(r) for r in con.execute(f"""
            SELECT appointments.*, salons.name salon_name, salon_branches.address, specialists.display_name specialist_name, services.name service_name
            FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id
            JOIN salons ON salons.id=salon_branches.salon_id JOIN specialists ON specialists.id=appointments.specialist_id
            JOIN services ON services.id=appointments.service_id {clause} ORDER BY appointments.start_at
        """, params).fetchall()]
    return {"items": rows}


@app.patch("/api/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: int, payload: RescheduleIn, user: dict[str, Any] = Depends(require_roles("customer"))):
    start = parse_dt(payload.start_at)
    with db(True) as con:
        appt = row_to_dict(con.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone())
        if not appt or appt["customer_id"] != user["id"]:
            raise HTTPException(404, "Appointment not found")
        if appt["status"] not in {"pending", "confirmed"}:
            raise HTTPException(409, "Appointment cannot be rescheduled in current status")
        ok, reason, end, _ = is_available(con, appt["specialist_id"], appt["service_id"], start, exclude_id=appointment_id)
        if not ok:
            raise HTTPException(409, reason)
        con.execute("UPDATE appointments SET start_at=?, end_at=?, updated_at=? WHERE id=?", (start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), utcnow(), appointment_id))
    return {"appointment": get_appointment_for_user(appointment_id, user)}


@app.patch("/api/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, reason: str = "", user: dict[str, Any] = Depends(require_roles("customer", "manager"))):
    with db(True) as con:
        appt = row_to_dict(con.execute("SELECT appointments.*, salons.owner_id FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id JOIN salons ON salons.id=salon_branches.salon_id WHERE appointments.id=?", (appointment_id,)).fetchone())
        if not appt:
            raise HTTPException(404, "Appointment not found")
        if user["role"] == "customer" and appt["customer_id"] != user["id"]:
            raise HTTPException(403, "Cannot cancel another customer's appointment")
        if user["role"] == "manager" and appt["owner_id"] != user["id"]:
            raise HTTPException(403, "Cannot cancel another salon's appointment")
        status = "cancelled_by_customer" if user["role"] == "customer" else "cancelled_by_salon"
        con.execute("UPDATE appointments SET status=?, cancellation_reason=?, updated_at=? WHERE id=?", (status, reason, utcnow(), appointment_id))
    return {"appointment": get_appointment_for_user(appointment_id, user)}


@app.patch("/api/manager/appointments/{appointment_id}/status")
def manager_status(appointment_id: int, payload: StatusIn, user: dict[str, Any] = Depends(require_roles("manager", "professional"))):
    allowed = {
        "pending": {"confirmed", "cancelled_by_salon"},
        "confirmed": {"in_progress", "completed", "cancelled_by_salon", "no_show"},
        "in_progress": {"completed", "no_show"},
    }
    with db(True) as con:
        appt = row_to_dict(con.execute("SELECT appointments.*, salons.owner_id, specialists.user_id professional_user_id FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id JOIN salons ON salons.id=salon_branches.salon_id JOIN specialists ON specialists.id=appointments.specialist_id WHERE appointments.id=?", (appointment_id,)).fetchone())
        if not appt:
            raise HTTPException(404, "Appointment not found")
        if user["role"] == "manager" and appt["owner_id"] != user["id"]:
            raise HTTPException(403, "Cannot manage another salon")
        if user["role"] == "professional" and appt["professional_user_id"] != user["id"]:
            raise HTTPException(403, "Cannot manage another professional's appointment")
        if payload.status not in allowed.get(appt["status"], set()):
            raise HTTPException(409, "Invalid appointment status transition")
        con.execute("UPDATE appointments SET status=?, salon_note=?, updated_at=? WHERE id=?", (payload.status, payload.salon_note, utcnow(), appointment_id))
    return {"appointment": get_appointment_for_user(appointment_id, user)}


@app.post("/api/favorites/salons/{salon_id}")
def favorite_salon(salon_id: int, user: dict[str, Any] = Depends(require_roles("customer"))):
    with db(True) as con:
        con.execute("INSERT OR IGNORE INTO favorite_salons(customer_id,salon_id,created_at) VALUES(?,?,?)", (user["id"], salon_id, utcnow()))
    return {"status": "saved"}


@app.get("/api/favorites")
def favorites(user: dict[str, Any] = Depends(require_roles("customer"))):
    with db() as con:
        salons = [dict(r) for r in con.execute("SELECT salons.* FROM favorite_salons JOIN salons ON salons.id=favorite_salons.salon_id WHERE favorite_salons.customer_id=?", (user["id"],)).fetchall()]
    return {"salons": salons}


@app.post("/api/reviews")
def create_review(payload: ReviewIn, user: dict[str, Any] = Depends(require_roles("customer"))):
    with db(True) as con:
        appt = row_to_dict(con.execute("SELECT appointments.*, salon_branches.salon_id FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id WHERE appointments.id=?", (payload.appointment_id,)).fetchone())
        if not appt or appt["customer_id"] != user["id"]:
            raise HTTPException(404, "Appointment not found")
        if appt["status"] != "completed":
            raise HTTPException(409, "Review is allowed only after completed appointment")
        try:
            con.execute("INSERT INTO reviews(appointment_id,customer_id,salon_id,specialist_id,rating,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (payload.appointment_id, user["id"], appt["salon_id"], appt["specialist_id"], payload.rating, payload.text, utcnow(), utcnow()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Appointment already has a review")
    return {"status": "created"}


@app.get("/api/notifications")
def notifications(user: dict[str, Any] = Depends(current_user)):
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return {"items": rows, "email_available": bool(os.getenv("SMTP_HOST")), "push_available": bool(os.getenv("PUSH_PROVIDER_KEY"))}


@app.get("/api/manager/dashboard")
def manager_dashboard(user: dict[str, Any] = Depends(require_roles("manager"))):
    today = datetime.now(UTC).date().isoformat()
    with db() as con:
        rows = [dict(r) for r in con.execute("""
            SELECT appointments.*, services.name service_name, specialists.display_name specialist_name, users.first_name || ' ' || users.last_name customer_name
            FROM appointments JOIN salon_branches ON salon_branches.id=appointments.salon_branch_id
            JOIN salons ON salons.id=salon_branches.salon_id JOIN services ON services.id=appointments.service_id
            JOIN specialists ON specialists.id=appointments.specialist_id JOIN users ON users.id=appointments.customer_id
            WHERE salons.owner_id=? AND appointments.start_at LIKE ? ORDER BY appointments.start_at
        """, (user["id"], f"{today}%")).fetchall()]
        revenue = sum(float(r["price"]) for r in rows if r["status"] in {"confirmed", "completed"})
    return {"today_appointments": rows, "expected_revenue": revenue, "awaiting_confirmation": [r for r in rows if r["status"] == "pending"]}


@app.get("/api/manager/services")
def manager_services(user: dict[str, Any] = Depends(require_roles("manager"))):
    with db() as con:
        rows = [dict(r) for r in con.execute("""
            SELECT services.* FROM services JOIN salon_branches ON salon_branches.id=services.salon_branch_id
            JOIN salons ON salons.id=salon_branches.salon_id WHERE salons.owner_id=? ORDER BY services.name
        """, (user["id"],)).fetchall()]
    return {"items": rows}


@app.get("/api/admin/salons")
def admin_salons(user: dict[str, Any] = Depends(require_roles("admin"))):
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM salons ORDER BY created_at DESC").fetchall()]
    return {"items": rows}


@app.patch("/api/admin/salons/{salon_id}/status")
def admin_salon_status(salon_id: int, approval_status: str, user: dict[str, Any] = Depends(require_roles("admin"))):
    if approval_status not in {"approved", "suspended", "rejected", "pending"}:
        raise HTTPException(400, "Invalid approval status")
    with db(True) as con:
        con.execute("UPDATE salons SET approval_status=?, updated_at=? WHERE id=?", (approval_status, utcnow(), salon_id))
        con.execute("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,metadata,created_at) VALUES(?,?,?,?,?,?)", (user["id"], "salon_status", "salon", str(salon_id), json.dumps({"approval_status": approval_status}), utcnow()))
    return {"status": "updated"}


@app.get("/manager", response_class=HTMLResponse)
def manager_page():
    path = BASE_DIR / "manager_web" / "index.html"
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
