from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

APP_NAME = "Beauty Lip"
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./beauty_lip.db").replace("sqlite:///", "")
JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(32)
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()]
TOKEN_TTL_HOURS = 24 * 14
BOOKING_LOCK = threading.Lock()
ACTIVE_APPOINTMENT_STATUSES = ("pending", "confirmed", "in_progress")

app = FastAPI(title=f"{APP_NAME} API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid datetime format") from exc


def parse_day(value: str) -> date:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid date format") from exc


def parse_clock(value: str) -> time:
    hour, minute = [int(part) for part in value.split(":")[:2]]
    return time(hour=hour, minute=minute)


def combine(day: date, clock: str) -> datetime:
    return datetime.combine(day, parse_clock(clock), tzinfo=timezone.utc)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$", 2)
    return hmac.compare_digest(hash_password(password, salt).split("$", 2)[2], digest)


def sign_token(user_id: int, role: str) -> str:
    payload = {"sub": user_id, "role": role, "exp": (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str) -> dict[str, Any]:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["exp"] < datetime.now(timezone.utc).timestamp():
            raise ValueError
        return payload
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc


@contextmanager
def db() -> Any:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def current_user(request: Request) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = read_token(auth.split(" ", 1)[1])
    with db() as conn:
        user = row_to_dict(conn.execute("SELECT id,email,first_name,last_name,phone,role,avatar_url,is_active FROM users WHERE id=?", (payload["sub"],)).fetchone())
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User is inactive")
    return user


def require_roles(*roles: str):
    def dep(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Role is not allowed for this action")
        return user
    return dep


def exec_schema() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,first_name TEXT,last_name TEXT,phone TEXT,role TEXT NOT NULL CHECK(role IN ('customer','professional','manager','admin')),avatar_url TEXT,is_active INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS salons(id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL,description TEXT,phone TEXT,email TEXT,address TEXT,city TEXT,latitude REAL,longitude REAL,logo_url TEXT,cover_url TEXT,rating_average REAL DEFAULT 0,rating_count INTEGER DEFAULT 0,approval_status TEXT DEFAULT 'approved',is_active INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS salon_branches(id INTEGER PRIMARY KEY,salon_id INTEGER NOT NULL REFERENCES salons(id),name TEXT,address TEXT,city TEXT,latitude REAL,longitude REAL,phone TEXT,timezone TEXT DEFAULT 'UTC',is_active INTEGER DEFAULT 1,opening_start TEXT DEFAULT '09:00',opening_end TEXT DEFAULT '19:00',booking_lead_minutes INTEGER DEFAULT 0,max_booking_days INTEGER DEFAULT 30);
    CREATE TABLE IF NOT EXISTS specialists(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id),display_name TEXT,title TEXT,biography TEXT,experience_years INTEGER DEFAULT 0,avatar_url TEXT,rating_average REAL DEFAULT 0,rating_count INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS service_categories(id INTEGER PRIMARY KEY,name TEXT UNIQUE,icon TEXT,display_order INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY,salon_branch_id INTEGER NOT NULL REFERENCES salon_branches(id),category_id INTEGER REFERENCES service_categories(id),name TEXT,description TEXT,duration_minutes INTEGER NOT NULL,price REAL NOT NULL,currency TEXT DEFAULT 'EUR',buffer_before_minutes INTEGER DEFAULT 0,buffer_after_minutes INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS specialist_services(specialist_id INTEGER REFERENCES specialists(id),service_id INTEGER REFERENCES services(id),custom_price REAL,custom_duration INTEGER,is_active INTEGER DEFAULT 1,PRIMARY KEY(specialist_id,service_id));
    CREATE TABLE IF NOT EXISTS working_schedules(id INTEGER PRIMARY KEY,specialist_id INTEGER REFERENCES specialists(id),weekday INTEGER,start_time TEXT,end_time TEXT,is_active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS schedule_exceptions(id INTEGER PRIMARY KEY,specialist_id INTEGER REFERENCES specialists(id),date TEXT,start_time TEXT,end_time TEXT,exception_type TEXT CHECK(exception_type IN ('day_off','custom_hours','blocked','break')),reason TEXT);
    CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES users(id),salon_branch_id INTEGER REFERENCES salon_branches(id),specialist_id INTEGER REFERENCES specialists(id),service_id INTEGER REFERENCES services(id),start_at TEXT,end_at TEXT,timezone TEXT DEFAULT 'UTC',price REAL,currency TEXT DEFAULT 'EUR',status TEXT,customer_note TEXT,salon_note TEXT,cancellation_reason TEXT,payment_status TEXT DEFAULT 'not_required',created_at TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS favorite_salons(customer_id INTEGER REFERENCES users(id),salon_id INTEGER REFERENCES salons(id),created_at TEXT,PRIMARY KEY(customer_id,salon_id));
    CREATE TABLE IF NOT EXISTS favorite_specialists(customer_id INTEGER REFERENCES users(id),specialist_id INTEGER REFERENCES specialists(id),created_at TEXT,PRIMARY KEY(customer_id,specialist_id));
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,appointment_id INTEGER UNIQUE REFERENCES appointments(id),customer_id INTEGER REFERENCES users(id),salon_id INTEGER REFERENCES salons(id),specialist_id INTEGER REFERENCES specialists(id),rating INTEGER CHECK(rating BETWEEN 1 AND 5),text TEXT,moderation_status TEXT DEFAULT 'visible',created_at TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),type TEXT,title TEXT,body TEXT,read_at TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,entity_type TEXT,entity_id INTEGER,metadata TEXT,created_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_appointments_specialist_time ON appointments(specialist_id,start_at,end_at,status);
    CREATE INDEX IF NOT EXISTS idx_salons_city ON salons(city,approval_status,is_active);
    """
    with db() as conn:
        conn.executescript(schema)


def seed() -> None:
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        ts = now_utc()
        users = [("customer@beautylip.dev", "Ada", "Customer", "customer"), ("manager@beautylip.dev", "Mira", "Manager", "manager"), ("pro@beautylip.dev", "Lina", "Pro", "professional"), ("admin@beautylip.dev", "Nora", "Admin", "admin")]
        ids = {}
        for email, first, last, role in users:
            cur = conn.execute("INSERT INTO users(email,password_hash,first_name,last_name,phone,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (email, hash_password("BeautyLip123!"), first, last, "+491111111", role, ts, ts))
            ids[role] = cur.lastrowid
        conn.execute("INSERT INTO service_categories(name,icon,display_order) VALUES('Hair','scissors',1),('Nails','spark',2),('Brows and Lashes','eye',3),('Wellness','leaf',4)")
        salon_id = conn.execute("INSERT INTO salons(owner_id,name,description,phone,email,address,city,latitude,longitude,logo_url,cover_url,rating_average,rating_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ids['manager'], 'Maison Plum Beauty', 'Warm premium salon for hair, nails, brows, lashes, and wellness.', '+4930123000', 'hello@maisonplum.dev', 'Rosenstrasse 12', 'Berlin', 52.52, 13.405, '', '', 4.8, 128, ts, ts)).lastrowid
        branch_id = conn.execute("INSERT INTO salon_branches(salon_id,name,address,city,latitude,longitude,phone,timezone,opening_start,opening_end,booking_lead_minutes,max_booking_days) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (salon_id, 'Maison Plum Mitte', 'Rosenstrasse 12', 'Berlin', 52.52, 13.405, '+4930123000', 'UTC', '09:00', '18:00', 0, 30)).lastrowid
        specialist_id = conn.execute("INSERT INTO specialists(user_id,salon_branch_id,display_name,title,biography,experience_years,rating_average,rating_count) VALUES(?,?,?,?,?,?,?,?)", (ids['professional'], branch_id, 'Lina Meyer', 'Senior Colorist and Brow Artist', 'Specialist in elegant hair color, brow shaping, and event styling.', 8, 4.9, 92)).lastrowid
        service_id = conn.execute("INSERT INTO services(salon_branch_id,category_id,name,description,duration_minutes,price,currency,buffer_after_minutes) VALUES(?,?,?,?,?,?,?,?)", (branch_id, 1, 'Signature Cut and Finish', 'Consultation, cut, styling, and aftercare guidance.', 60, 72, 'EUR', 0)).lastrowid
        conn.execute("INSERT INTO services(salon_branch_id,category_id,name,description,duration_minutes,price,currency) VALUES(?,?,?,?,?,?,?)", (branch_id, 3, 'Brow Shape Ritual', 'Brow mapping, shaping, and calming finish.', 30, 39, 'EUR'))
        conn.execute("INSERT INTO specialist_services(specialist_id,service_id,is_active) VALUES(?,?,1)", (specialist_id, service_id))
        for weekday in range(5):
            conn.execute("INSERT INTO working_schedules(specialist_id,weekday,start_time,end_time,is_active) VALUES(?,?,?,?,1)", (specialist_id, weekday, '09:00', '18:00'))
        today = date.today().isoformat()
        conn.execute("INSERT INTO schedule_exceptions(specialist_id,date,start_time,end_time,exception_type,reason) VALUES(?,?,?,?,?,?)", (specialist_id, today, '13:00', '14:00', 'break', 'Lunch'))


@app.on_event("startup")
def startup() -> None:
    exec_schema()
    seed()


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AppointmentIn(BaseModel):
    salon_branch_id: int
    specialist_id: int
    service_id: int
    start_at: str
    customer_note: str | None = Field(default=None, max_length=500)


class RescheduleIn(BaseModel):
    start_at: str = Field(min_length=1, max_length=40)


class StatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class ReviewIn(BaseModel):
    appointment_id: int
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(default=None, max_length=1000)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {k: user[k] for k in user if k != "password_hash"}


def pick(row: sqlite3.Row | dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields}


PUBLIC_SALON_FIELDS = ("id", "name", "description", "phone", "email", "address", "city", "latitude", "longitude", "logo_url", "cover_url", "rating_average", "rating_count", "branch_id", "branch_address")
PUBLIC_SPECIALIST_FIELDS = ("id", "display_name", "title", "biography", "experience_years", "avatar_url", "rating_average", "rating_count", "salon_name")


def audit(conn: sqlite3.Connection, actor: int, action: str, entity: str, entity_id: int, metadata: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,metadata,created_at) VALUES(?,?,?,?,?,?)", (actor, action, entity, entity_id, json.dumps(metadata or {}), now_utc()))


def notify(conn: sqlite3.Connection, user_id: int, typ: str, title: str, body: str) -> None:
    conn.execute("INSERT INTO notifications(user_id,type,title,body,created_at) VALUES(?,?,?,?,?)", (user_id, typ, title, body, now_utc()))


def appointment_view(conn: sqlite3.Connection, appointment_id: int) -> dict[str, Any]:
    row = conn.execute("""SELECT a.*, s.name service_name, sp.display_name specialist_name, sal.name salon_name, b.address
                          FROM appointments a JOIN services s ON s.id=a.service_id JOIN specialists sp ON sp.id=a.specialist_id
                          JOIN salon_branches b ON b.id=a.salon_branch_id JOIN salons sal ON sal.id=b.salon_id WHERE a.id=?""", (appointment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return dict(row)


def service_for(conn: sqlite3.Connection, service_id: int, specialist_id: int) -> sqlite3.Row:
    row = conn.execute("""SELECT s.*, COALESCE(ss.custom_duration,s.duration_minutes) effective_duration, COALESCE(ss.custom_price,s.price) effective_price
                          FROM services s JOIN specialist_services ss ON ss.service_id=s.id AND ss.specialist_id=? AND ss.is_active=1
                          WHERE s.id=? AND s.is_active=1""", (specialist_id, service_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Service is not available for this specialist")
    return row


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def calculate_slots(conn: sqlite3.Connection, specialist_id: int, service_id: int, day: date, exclude_appointment_id: int | None = None) -> list[dict[str, str]]:
    service = service_for(conn, service_id, specialist_id)
    branch = conn.execute("SELECT b.* FROM salon_branches b JOIN specialists sp ON sp.salon_branch_id=b.id WHERE sp.id=? AND sp.is_active=1", (specialist_id,)).fetchone()
    if not branch:
        return []
    if day > date.today() + timedelta(days=branch["max_booking_days"]):
        return []
    custom = conn.execute("SELECT * FROM schedule_exceptions WHERE specialist_id=? AND date=? AND exception_type='custom_hours'", (specialist_id, day.isoformat())).fetchone()
    schedules = [custom] if custom else conn.execute("SELECT * FROM working_schedules WHERE specialist_id=? AND weekday=? AND is_active=1", (specialist_id, day.weekday())).fetchall()
    if conn.execute("SELECT 1 FROM schedule_exceptions WHERE specialist_id=? AND date=? AND exception_type='day_off'", (specialist_id, day.isoformat())).fetchone():
        return []
    busy = []
    for row in conn.execute("SELECT start_at,end_at FROM appointments WHERE specialist_id=? AND status IN ('pending','confirmed','in_progress') AND date(start_at)=date(?) AND (? IS NULL OR id<>?)", (specialist_id, day.isoformat(), exclude_appointment_id, exclude_appointment_id)):
        busy.append((parse_dt(row["start_at"]), parse_dt(row["end_at"])))
    for ex in conn.execute("SELECT start_time,end_time FROM schedule_exceptions WHERE specialist_id=? AND date=? AND exception_type IN ('blocked','break')", (specialist_id, day.isoformat())):
        busy.append((combine(day, ex["start_time"]), combine(day, ex["end_time"])))
    slots = []
    duration = timedelta(minutes=service["effective_duration"] + service["buffer_before_minutes"] + service["buffer_after_minutes"])
    lead = datetime.now(timezone.utc) + timedelta(minutes=branch["booking_lead_minutes"])
    for sched in schedules:
        cursor = max(combine(day, sched["start_time"]), combine(day, branch["opening_start"]))
        limit = min(combine(day, sched["end_time"]), combine(day, branch["opening_end"]))
        while cursor + duration <= limit:
            slot_start = cursor
            slot_end = slot_start + timedelta(minutes=service["effective_duration"])
            padded_end = slot_start + duration
            if slot_start >= lead and not any(overlaps(slot_start, padded_end, b0, b1) for b0, b1 in busy):
                slots.append({"start_at": slot_start.isoformat(), "end_at": slot_end.isoformat()})
            cursor += timedelta(minutes=15)
    return slots


def ensure_available(conn: sqlite3.Connection, specialist_id: int, service_id: int, start_at: datetime, exclude: int | None = None) -> tuple[datetime, float, str]:
    service = service_for(conn, service_id, specialist_id)
    end_at = start_at + timedelta(minutes=service["effective_duration"])
    slots = calculate_slots(conn, specialist_id, service_id, start_at.date(), exclude)
    if start_at.isoformat() not in {s["start_at"] for s in slots}:
        raise HTTPException(status_code=409, detail="Selected time is not available")
    conflict = conn.execute("SELECT 1 FROM appointments WHERE specialist_id=? AND status IN ('pending','confirmed','in_progress') AND (? IS NULL OR id<>?) AND start_at < ? AND end_at > ?", (specialist_id, exclude, exclude, end_at.isoformat(), start_at.isoformat())).fetchone()
    if conflict:
        raise HTTPException(status_code=409, detail="Specialist already has an overlapping appointment")
    return end_at, service["effective_price"], service["currency"]


def can_manage_appointment(conn: sqlite3.Connection, appointment_id: int, user: dict[str, Any]) -> bool:
    row = conn.execute("""SELECT 1 FROM appointments a
                          JOIN salon_branches b ON b.id=a.salon_branch_id
                          JOIN salons sal ON sal.id=b.salon_id
                          JOIN specialists sp ON sp.id=a.specialist_id
                          WHERE a.id=? AND (sal.owner_id=? OR sp.user_id=?)""", (appointment_id, user["id"], user["id"])).fetchone()
    return row is not None


@app.post("/api/auth/register")
def register(data: RegisterIn) -> dict[str, Any]:
    with db() as conn:
        try:
            ts = now_utc()
            user_id = conn.execute("INSERT INTO users(email,password_hash,first_name,last_name,phone,role,created_at,updated_at) VALUES(?,?,?,?,?,'customer',?,?)", (data.email.lower(), hash_password(data.password), data.first_name, data.last_name, data.phone, ts, ts)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Email is already registered") from exc
        user = row_to_dict(conn.execute("SELECT id,email,first_name,last_name,phone,role,avatar_url,is_active FROM users WHERE id=?", (user_id,)).fetchone())
    return {"access_token": sign_token(user_id, "customer"), "token_type": "bearer", "user": user}


@app.post("/api/auth/login")
def login(data: LoginIn) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (data.email.lower(),)).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not row["is_active"]:
        raise HTTPException(status_code=401, detail="User is inactive")
    user = row_to_dict(row)
    return {"access_token": sign_token(user["id"], user["role"]), "token_type": "bearer", "user": public_user(user)}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return user


@app.get("/api/salons")
def salons(q: str = "", city: str = "", category: str = "", sort: str = "recommended", limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("""SELECT sal.*, b.id branch_id, b.address branch_address FROM salons sal JOIN salon_branches b ON b.salon_id=sal.id
                               WHERE sal.is_active=1 AND sal.approval_status='approved' AND (?='' OR sal.city LIKE ?) AND (?='' OR sal.name LIKE ? OR sal.description LIKE ?) LIMIT ? OFFSET ?""", (city, f"%{city}%", q, f"%{q}%", f"%{q}%", limit, offset)).fetchall()
        items = []
        for row in rows:
            item = pick(row, PUBLIC_SALON_FIELDS)
            item["services"] = [dict(x) for x in conn.execute("SELECT id,name,duration_minutes,price,currency FROM services WHERE salon_branch_id=? AND is_active=1", (row["branch_id"],))]
            item["specialists"] = [dict(x) for x in conn.execute("SELECT id,display_name,title,rating_average FROM specialists WHERE salon_branch_id=? AND is_active=1", (row["branch_id"],))]
            if category and category.lower() not in json.dumps(item).lower():
                continue
            items.append(item)
    if sort == "highest_rated":
        items.sort(key=lambda x: x["rating_average"], reverse=True)
    return {"items": items, "limit": limit, "offset": offset}


@app.get("/api/salons/{salon_id}")
def salon_detail(salon_id: int) -> dict[str, Any]:
    data = salons(limit=100)["items"]
    for item in data:
        if item["id"] == salon_id:
            return item
    raise HTTPException(status_code=404, detail="Salon not found")


@app.get("/api/specialists/{specialist_id}")
def specialist_detail(specialist_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT sp.*, sal.name salon_name FROM specialists sp JOIN salon_branches b ON b.id=sp.salon_branch_id JOIN salons sal ON sal.id=b.salon_id WHERE sp.id=? AND sp.is_active=1", (specialist_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Specialist not found")
        item = pick(row, PUBLIC_SPECIALIST_FIELDS)
        item["services"] = [dict(x) for x in conn.execute("SELECT s.id,s.name,s.price,s.duration_minutes FROM services s JOIN specialist_services ss ON ss.service_id=s.id WHERE ss.specialist_id=? AND ss.is_active=1", (specialist_id,))]
        return item


@app.get("/api/services")
def services() -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(x) for x in conn.execute("SELECT s.id,s.salon_branch_id,s.category_id,s.name,s.description,s.duration_minutes,s.price,s.currency,c.name category FROM services s LEFT JOIN service_categories c ON c.id=s.category_id WHERE s.is_active=1")]


@app.get("/api/availability")
def availability(specialist_id: int, service_id: int, date: str) -> dict[str, Any]:
    with db() as conn:
        return {"slots": calculate_slots(conn, specialist_id, service_id, parse_day(date))}


@app.post("/api/appointments")
def create_appointment(data: AppointmentIn, user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, Any]:
    with BOOKING_LOCK, db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        start = parse_dt(data.start_at)
        service = service_for(conn, data.service_id, data.specialist_id)
        specialist = conn.execute("SELECT salon_branch_id FROM specialists WHERE id=? AND is_active=1", (data.specialist_id,)).fetchone()
        if not specialist or specialist["salon_branch_id"] != data.salon_branch_id or service["salon_branch_id"] != data.salon_branch_id:
            raise HTTPException(status_code=400, detail="Selected service and specialist do not belong to this branch")
        end, price, currency = ensure_available(conn, data.specialist_id, data.service_id, start)
        ts = now_utc()
        appt_id = conn.execute("INSERT INTO appointments(customer_id,salon_branch_id,specialist_id,service_id,start_at,end_at,timezone,price,currency,status,customer_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (user["id"], data.salon_branch_id, data.specialist_id, data.service_id, start.isoformat(), end.isoformat(), 'UTC', price, currency, 'pending', data.customer_note, ts, ts)).lastrowid
        manager = conn.execute("SELECT sal.owner_id FROM salons sal JOIN salon_branches b ON b.salon_id=sal.id WHERE b.id=?", (data.salon_branch_id,)).fetchone()[0]
        notify(conn, user["id"], "booking_created", "Booking created", "Your Beauty Lip appointment is pending confirmation.")
        notify(conn, manager, "booking_created", "New booking", "A customer booked a new appointment.")
        audit(conn, user["id"], "create", "appointment", appt_id)
        conn.execute("COMMIT")
        return appointment_view(conn, appt_id)


@app.get("/api/appointments")
def list_appointments(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as conn:
        if user["role"] == "customer":
            ids = [r[0] for r in conn.execute("SELECT id FROM appointments WHERE customer_id=? ORDER BY start_at", (user["id"],))]
        elif user["role"] == "manager":
            ids = [r[0] for r in conn.execute("SELECT a.id FROM appointments a JOIN salon_branches b ON b.id=a.salon_branch_id JOIN salons s ON s.id=b.salon_id WHERE s.owner_id=? ORDER BY a.start_at", (user["id"],))]
        elif user["role"] == "professional":
            ids = [r[0] for r in conn.execute("SELECT a.id FROM appointments a JOIN specialists sp ON sp.id=a.specialist_id WHERE sp.user_id=? ORDER BY a.start_at", (user["id"],))]
        else:
            ids = [r[0] for r in conn.execute("SELECT id FROM appointments ORDER BY start_at")]
        return [appointment_view(conn, x) for x in ids]


@app.patch("/api/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: int, data: RescheduleIn, user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, Any]:
    with BOOKING_LOCK, db() as conn:
        appt = conn.execute("SELECT * FROM appointments WHERE id=? AND customer_id=?", (appointment_id, user["id"])).fetchone()
        if not appt or appt["status"] not in ACTIVE_APPOINTMENT_STATUSES:
            raise HTTPException(status_code=404, detail="Active appointment not found")
        conn.execute("BEGIN IMMEDIATE")
        start = parse_dt(data.start_at)
        end, _, _ = ensure_available(conn, appt["specialist_id"], appt["service_id"], start, appointment_id)
        conn.execute("UPDATE appointments SET start_at=?,end_at=?,status='pending',updated_at=? WHERE id=?", (start.isoformat(), end.isoformat(), now_utc(), appointment_id))
        audit(conn, user["id"], "reschedule", "appointment", appointment_id)
        conn.execute("COMMIT")
        return appointment_view(conn, appointment_id)


@app.patch("/api/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, reason: str = Query("customer request", max_length=500), user: dict[str, Any] = Depends(require_roles("customer", "manager"))) -> dict[str, Any]:
    with db() as conn:
        appt = conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        status = "cancelled_by_customer" if user["role"] == "customer" else "cancelled_by_salon"
        if user["role"] == "customer" and appt["customer_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Cannot cancel another customer's appointment")
        if user["role"] == "manager" and not can_manage_appointment(conn, appointment_id, user):
            raise HTTPException(status_code=403, detail="Cannot cancel another salon's appointment")
        conn.execute("UPDATE appointments SET status=?,cancellation_reason=?,updated_at=? WHERE id=?", (status, reason, now_utc(), appointment_id))
        audit(conn, user["id"], "cancel", "appointment", appointment_id)
        return appointment_view(conn, appointment_id)


@app.patch("/api/manager/appointments/{appointment_id}/status")
def manager_status(appointment_id: int, data: StatusIn, user: dict[str, Any] = Depends(require_roles("manager", "professional"))) -> dict[str, Any]:
    allowed = {"pending": {"confirmed", "cancelled_by_salon"}, "confirmed": {"in_progress", "completed", "no_show", "cancelled_by_salon"}, "in_progress": {"completed"}}
    with db() as conn:
        appt = conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not appt or data.status not in allowed.get(appt["status"], set()):
            raise HTTPException(status_code=409, detail="Invalid appointment status transition")
        if not can_manage_appointment(conn, appointment_id, user):
            raise HTTPException(status_code=403, detail="Cannot update another salon's appointment")
        conn.execute("UPDATE appointments SET status=?,salon_note=?,updated_at=? WHERE id=?", (data.status, data.note, now_utc(), appointment_id))
        notify(conn, appt["customer_id"], "booking_changed", "Booking updated", f"Your appointment status is now {data.status}.")
        audit(conn, user["id"], "status_update", "appointment", appointment_id, {"status": data.status})
        return appointment_view(conn, appointment_id)


@app.post("/api/favorites/salons/{salon_id}")
def favorite_salon(salon_id: int, user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, str]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM salons WHERE id=? AND is_active=1 AND approval_status='approved'", (salon_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Salon not found")
        conn.execute("INSERT OR IGNORE INTO favorite_salons(customer_id,salon_id,created_at) VALUES(?,?,?)", (user["id"], salon_id, now_utc()))
    return {"status": "saved"}


@app.post("/api/favorites/specialists/{specialist_id}")
def favorite_specialist(specialist_id: int, user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, str]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM specialists WHERE id=? AND is_active=1", (specialist_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Specialist not found")
        conn.execute("INSERT OR IGNORE INTO favorite_specialists(customer_id,specialist_id,created_at) VALUES(?,?,?)", (user["id"], specialist_id, now_utc()))
    return {"status": "saved"}


@app.get("/api/favorites")
def favorites(user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, Any]:
    with db() as conn:
        salons = [pick(x, ("id", "name", "description", "phone", "email", "address", "city", "latitude", "longitude", "logo_url", "cover_url", "rating_average", "rating_count")) for x in conn.execute("SELECT s.* FROM salons s JOIN favorite_salons f ON f.salon_id=s.id WHERE f.customer_id=? AND s.is_active=1 AND s.approval_status='approved'", (user["id"],))]
        specialists = [pick(x, ("id", "display_name", "title", "biography", "experience_years", "avatar_url", "rating_average", "rating_count")) for x in conn.execute("SELECT sp.* FROM specialists sp JOIN favorite_specialists f ON f.specialist_id=sp.id WHERE f.customer_id=? AND sp.is_active=1", (user["id"],))]
        return {"salons": salons, "specialists": specialists}


@app.post("/api/reviews")
def create_review(data: ReviewIn, user: dict[str, Any] = Depends(require_roles("customer"))) -> dict[str, Any]:
    with db() as conn:
        appt = conn.execute("SELECT a.*, b.salon_id FROM appointments a JOIN salon_branches b ON b.id=a.salon_branch_id WHERE a.id=? AND a.customer_id=?", (data.appointment_id, user["id"])).fetchone()
        if not appt or appt["status"] != "completed":
            raise HTTPException(status_code=403, detail="Only completed own appointments can be reviewed")
        try:
            rid = conn.execute("INSERT INTO reviews(appointment_id,customer_id,salon_id,specialist_id,rating,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (data.appointment_id, user["id"], appt["salon_id"], appt["specialist_id"], data.rating, data.text, now_utc(), now_utc())).lastrowid
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Appointment already has a review") from exc
        return dict(conn.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone())


@app.get("/api/notifications")
def notifications(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(x) for x in conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC", (user["id"],))]


@app.get("/api/manager/dashboard")
def manager_dashboard(user: dict[str, Any] = Depends(require_roles("manager"))) -> dict[str, Any]:
    with db() as conn:
        ids = [x[0] for x in conn.execute("SELECT a.id FROM appointments a JOIN salon_branches b ON b.id=a.salon_branch_id JOIN salons s ON s.id=b.salon_id WHERE s.owner_id=? AND date(a.start_at)=date('now')", (user["id"],))]
        revenue = conn.execute("SELECT COALESCE(SUM(a.price),0) FROM appointments a JOIN salon_branches b ON b.id=a.salon_branch_id JOIN salons s ON s.id=b.salon_id WHERE s.owner_id=? AND a.status IN ('confirmed','completed')", (user["id"],)).fetchone()[0]
        return {"today_appointments": len(ids), "awaiting_confirmation": len([x for x in list_appointments(user) if x["status"] == "pending"]), "completed_appointments": len([x for x in list_appointments(user) if x["status"] == "completed"]), "expected_revenue": revenue, "available_time_gaps": "See /api/availability per specialist"}


@app.get("/api/manager/calendar")
def manager_calendar(user: dict[str, Any] = Depends(require_roles("manager"))) -> list[dict[str, Any]]:
    return list_appointments(user)


@app.get("/api/manager/customers")
def manager_customers(user: dict[str, Any] = Depends(require_roles("manager"))) -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(x) for x in conn.execute("""SELECT u.id,u.first_name,u.last_name,u.email,u.phone,COUNT(a.id) appointment_count,MAX(a.start_at) last_appointment,
                                               SUM(CASE WHEN a.status='no_show' THEN 1 ELSE 0 END) no_show_count FROM users u JOIN appointments a ON a.customer_id=u.id
                                               JOIN salon_branches b ON b.id=a.salon_branch_id JOIN salons s ON s.id=b.salon_id WHERE s.owner_id=? GROUP BY u.id""", (user["id"],))]


@app.get("/api/admin/statistics")
def admin_stats(user: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, Any]:
    with db() as conn:
        return {"users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], "salons": conn.execute("SELECT COUNT(*) FROM salons").fetchone()[0], "appointments": conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0], "reviews": conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]}


@app.get("/api/admin/users")
def admin_users(user: dict[str, Any] = Depends(require_roles("admin"))) -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(x) for x in conn.execute("SELECT id,email,first_name,last_name,role,is_active,created_at FROM users")]


@app.get("/api/admin/audit-logs")
def admin_audit(user: dict[str, Any] = Depends(require_roles("admin"))) -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(x) for x in conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 100")]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_NAME}


if Path("dist").exists():
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        return FileResponse("dist/index.html")
