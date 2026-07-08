from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator


APP_NAME = "Nerva Desk"
STATUSES = ["новая", "в работе", "ожидает клиента", "выполнена", "закрыта"]
PRIORITIES = ["низкий", "обычный", "высокий", "срочный"]
DB_PATH = Path(os.getenv("DATABASE_PATH", "tickets.db"))
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
}


def strip_text(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


def strip_required_text(value: str | None) -> str | None:
    stripped = strip_text(value)
    if stripped == "":
        raise ValueError("Поле не может быть пустым")
    return stripped


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


class TicketIn(BaseModel):
    client_name: str = Field(min_length=1, max_length=120)
    contact: str = Field(min_length=1, max_length=160)
    company: str = Field(default="", max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    priority: str = Field(default="обычный")
    status: str = Field(default="новая")

    @field_validator("client_name", "contact", "description")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return strip_required_text(value)

    @field_validator("company", "priority", "status")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return strip_text(value)

    @model_validator(mode="after")
    def validate_choices(self) -> "TicketIn":
        if self.priority not in PRIORITIES:
            raise ValueError("Недопустимый приоритет")
        if self.status not in STATUSES:
            raise ValueError("Недопустимый статус")
        return self


class TicketUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=120)
    contact: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: str | None = None
    status: str | None = None

    @field_validator("client_name", "contact", "description")
    @classmethod
    def validate_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return strip_required_text(value)

    @field_validator("company", "priority", "status")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return strip_text(value)

    @model_validator(mode="after")
    def validate_choices(self) -> "TicketUpdate":
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError("Недопустимый приоритет")
        if self.status is not None and self.status not in STATUSES:
            raise ValueError("Недопустимый статус")
        return self


class CommentIn(BaseModel):
    author: str = Field(default="Локальный администратор", min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("author", "text")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return strip_required_text(value)


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                contact TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                author TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def serialize_ticket(row: sqlite3.Row, comments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ticket = dict(row)
    ticket["comments"] = comments or []
    return ticket


def get_ticket_row(ticket_id: int, connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return row


def comments_for_ticket(ticket_id: int, connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at ASC, id ASC", (ticket_id,)
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML_PAGE


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "app_name": APP_NAME,
        "admin_name": os.getenv("ADMIN_NAME", "Локальный администратор"),
        "statuses": STATUSES,
        "priorities": PRIORITIES,
    }


@app.get("/api/stats")
def stats() -> dict[str, int]:
    init_db()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'новая' THEN 1 ELSE 0 END) AS new_count,
                SUM(CASE WHEN status = 'в работе' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN priority = 'срочный' THEN 1 ELSE 0 END) AS urgent_count,
                SUM(CASE WHEN status = 'закрыта' AND DATE(closed_at) = DATE('now', 'localtime') THEN 1 ELSE 0 END) AS closed_today_count
            FROM tickets
            """
        ).fetchone()
    return {
        "new": int(row["new_count"] or 0),
        "in_progress": int(row["active_count"] or 0),
        "urgent": int(row["urgent_count"] or 0),
        "closed_today": int(row["closed_today_count"] or 0),
    }


@app.get("/api/tickets")
def list_tickets(
    search: str = Query(default="", max_length=200),
    status: str = Query(default=""),
    priority: str = Query(default=""),
) -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[str] = []
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        clauses.append("status = ?")
        params.append(status)
    if priority:
        if priority not in PRIORITIES:
            raise HTTPException(status_code=400, detail="Недопустимый приоритет")
        clauses.append("priority = ?")
        params.append(priority)
    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            """
            (LOWER(client_name) LIKE ? OR LOWER(contact) LIKE ? OR LOWER(company) LIKE ?
             OR LOWER(description) LIKE ? OR EXISTS (
                SELECT 1 FROM comments c
                WHERE c.ticket_id = tickets.id AND LOWER(c.text) LIKE ?
             ))
            """
        )
        params.extend([like, like, like, like, like])
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM tickets {where_sql} ORDER BY updated_at DESC, id DESC", params
        ).fetchall()
    return [serialize_ticket(row) for row in rows]


@app.post("/api/tickets", status_code=201)
def create_ticket(payload: TicketIn) -> dict[str, Any]:
    init_db()
    timestamp = now_iso()
    closed_at = timestamp if payload.status == "закрыта" else None
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tickets (client_name, contact, company, description, priority, status, created_at, updated_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.client_name.strip(),
                payload.contact.strip(),
                payload.company.strip(),
                payload.description.strip(),
                payload.priority,
                payload.status,
                timestamp,
                timestamp,
                closed_at,
            ),
        )
        row = get_ticket_row(int(cursor.lastrowid), connection)
        return serialize_ticket(row)


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> dict[str, Any]:
    init_db()
    with get_connection() as connection:
        row = get_ticket_row(ticket_id, connection)
        return serialize_ticket(row, comments_for_ticket(ticket_id, connection))


@app.put("/api/tickets/{ticket_id}")
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict[str, Any]:
    init_db()
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет данных для обновления")
    timestamp = now_iso()
    with get_connection() as connection:
        current = get_ticket_row(ticket_id, connection)
        next_status = updates.get("status", current["status"])
        if next_status == "закрыта" and current["closed_at"] is None:
            updates["closed_at"] = timestamp
        elif current["status"] == "закрыта" and next_status != "закрыта":
            updates["closed_at"] = None
        updates["updated_at"] = timestamp
        columns = ", ".join(f"{key} = ?" for key in updates)
        values = [value.strip() if isinstance(value, str) else value for value in updates.values()]
        connection.execute(f"UPDATE tickets SET {columns} WHERE id = ?", [*values, ticket_id])
        row = get_ticket_row(ticket_id, connection)
        return serialize_ticket(row, comments_for_ticket(ticket_id, connection))


@app.post("/api/tickets/{ticket_id}/comments", status_code=201)
def add_comment(ticket_id: int, payload: CommentIn) -> dict[str, Any]:
    init_db()
    timestamp = now_iso()
    with get_connection() as connection:
        get_ticket_row(ticket_id, connection)
        cursor = connection.execute(
            "INSERT INTO comments (ticket_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, payload.author.strip(), payload.text.strip(), timestamp),
        )
        connection.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (timestamp, ticket_id))
        row = connection.execute("SELECT * FROM comments WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
    return dict(row)


@app.delete("/api/tickets/{ticket_id}", status_code=204, response_class=Response)
def delete_ticket(ticket_id: int) -> Response:
    init_db()
    with get_connection() as connection:
        get_ticket_row(ticket_id, connection)
        connection.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    return Response(status_code=204)


HTML_PAGE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nerva Desk</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f172a;
      --panel: #111c33;
      --surface: #1e293b;
      --line: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --primary: #2563eb;
      --accent: #f59e0b;
      --danger: #ef4444;
      --ok: #22c55e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top left, #1e3a8a55, transparent 32rem), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, textarea, select { font: inherit; }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
    .brand h1 { margin: 0; font-size: clamp(28px, 4vw, 44px); letter-spacing: -0.04em; }
    .brand p { margin: 6px 0 0; color: var(--muted); }
    .admin { color: var(--muted); font-size: 14px; text-align: right; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
    .stat, .card { background: linear-gradient(180deg, #1e293bcc, #111827ee); border: 1px solid #334155aa; border-radius: 18px; box-shadow: 0 18px 48px #02061755; }
    .stat { padding: 18px; }
    .stat span { display: block; color: var(--muted); font-size: 14px; }
    .stat strong { display: block; font-size: 34px; margin-top: 6px; }
    .toolbar { display: grid; grid-template-columns: 1fr 180px 180px auto; gap: 12px; margin-bottom: 18px; }
    .card { overflow: hidden; }
    .ticket-list { display: grid; gap: 1px; background: #33415566; }
    .ticket { display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 16px; background: #111827; cursor: pointer; border: 0; color: inherit; text-align: left; }
    .ticket:hover { background: #172033; }
    .ticket h3 { margin: 0 0 6px; font-size: 18px; }
    .ticket p { margin: 0; color: var(--muted); line-height: 1.5; }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; align-content: flex-start; }
    .badge { border-radius: 999px; padding: 5px 10px; font-size: 13px; background: #334155; color: #dbeafe; white-space: nowrap; }
    .urgent { background: #7f1d1d; color: #fecaca; }
    .closed { background: #14532d; color: #bbf7d0; }
    .empty { padding: 28px; color: var(--muted); text-align: center; background: #111827; }
    input, textarea, select { width: 100%; border: 1px solid var(--line); background: #0b1220; color: var(--text); border-radius: 12px; padding: 11px 12px; outline: none; }
    textarea { min-height: 110px; resize: vertical; }
    input:focus, textarea:focus, select:focus { border-color: #60a5fa; box-shadow: 0 0 0 3px #2563eb33; }
    .button { border: 0; border-radius: 12px; padding: 11px 16px; color: white; background: var(--primary); cursor: pointer; font-weight: 700; }
    .button.secondary { background: #334155; }
    .button.danger { background: var(--danger); }
    .button:hover { filter: brightness(1.08); }
    dialog { width: min(760px, calc(100% - 24px)); border: 1px solid var(--line); border-radius: 20px; padding: 0; background: #0f172a; color: var(--text); box-shadow: 0 28px 80px #000a; }
    dialog::backdrop { background: #020617aa; backdrop-filter: blur(4px); }
    .modal-head, .modal-body, .modal-foot { padding: 18px; }
    .modal-head { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); }
    .modal-head h2 { margin: 0; }
    .modal-body { display: grid; gap: 12px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .full { grid-column: 1 / -1; }
    label span { display: block; margin-bottom: 6px; color: var(--muted); font-size: 14px; }
    .modal-foot { display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid var(--line); }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; }
    .comments { display: grid; gap: 10px; }
    .comment { border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #111827; }
    .comment small { color: var(--muted); }
    .error { color: #fecaca; background: #7f1d1d; padding: 10px 12px; border-radius: 12px; display: none; }
    @media (max-width: 820px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
      .toolbar { grid-template-columns: 1fr; }
      header, .ticket, .modal-foot { grid-template-columns: 1fr; flex-direction: column; align-items: stretch; }
      .admin { text-align: left; }
      .badges { justify-content: flex-start; }
    }
    @media (max-width: 560px) {
      .shell { width: min(100% - 20px, 1180px); padding: 18px 0; }
      .grid, .form-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand">
        <h1>Nerva Desk</h1>
        <p>Локальный журнал клиентских заявок для небольшой IT-компании.</p>
      </div>
      <div class="admin" id="adminName">Администратор</div>
    </header>

    <section class="grid" aria-label="Сводка">
      <div class="stat"><span>Новые</span><strong id="statNew">0</strong></div>
      <div class="stat"><span>В работе</span><strong id="statProgress">0</strong></div>
      <div class="stat"><span>Срочные</span><strong id="statUrgent">0</strong></div>
      <div class="stat"><span>Закрыто сегодня</span><strong id="statClosed">0</strong></div>
    </section>

    <section class="toolbar" aria-label="Поиск и фильтры">
      <input id="searchInput" type="search" placeholder="Поиск по клиенту, контакту, компании, описанию или комментариям" />
      <select id="statusFilter"><option value="">Все статусы</option></select>
      <select id="priorityFilter"><option value="">Все приоритеты</option></select>
      <button class="button" id="newTicketButton">Новая заявка</button>
    </section>

    <section class="card" aria-label="Список заявок">
      <div class="ticket-list" id="ticketList"></div>
    </section>
  </main>

  <dialog id="ticketDialog">
    <form id="ticketForm" method="dialog">
      <div class="modal-head">
        <h2 id="dialogTitle">Новая заявка</h2>
        <button class="button secondary" type="button" id="closeDialogButton">Закрыть</button>
      </div>
      <div class="modal-body">
        <div class="error" id="formError"></div>
        <div class="form-grid">
          <label><span>Клиент</span><input name="client_name" required maxlength="120" /></label>
          <label><span>Телефон или email</span><input name="contact" required maxlength="160" /></label>
          <label><span>Компания</span><input name="company" maxlength="160" /></label>
          <label><span>Приоритет</span><select name="priority" required></select></label>
          <label><span>Статус</span><select name="status" required></select></label>
          <label class="full"><span>Описание проблемы</span><textarea name="description" required maxlength="4000"></textarea></label>
        </div>
        <section id="commentSection" class="comments"></section>
      </div>
      <div class="modal-foot">
        <div class="actions">
          <button class="button" type="submit">Сохранить</button>
          <button class="button danger" type="button" id="deleteButton">Удалить</button>
        </div>
        <div class="actions">
          <input id="commentText" placeholder="Добавить комментарий" />
          <button class="button secondary" type="button" id="commentButton">Добавить</button>
        </div>
      </div>
    </form>
  </dialog>

  <script>
    const state = { tickets: [], currentId: null, meta: { statuses: [], priorities: [] } };
    const list = document.querySelector('#ticketList');
    const dialog = document.querySelector('#ticketDialog');
    const form = document.querySelector('#ticketForm');
    const errorBox = document.querySelector('#formError');

    async function request(path, options = {}) {
      const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: 'Ошибка запроса' }));
        throw new Error(Array.isArray(data.detail) ? data.detail[0].msg : data.detail);
      }
      if (response.status === 204) return null;
      return response.json();
    }

    function fillSelect(select, values, emptyLabel) {
      select.replaceChildren();
      if (emptyLabel) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = emptyLabel;
        select.append(option);
      }
      values.forEach(value => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.append(option);
      });
    }

    async function loadMeta() {
      state.meta = await request('/api/meta');
      document.querySelector('#adminName').textContent = state.meta.admin_name;
      fillSelect(document.querySelector('#statusFilter'), state.meta.statuses, 'Все статусы');
      fillSelect(document.querySelector('#priorityFilter'), state.meta.priorities, 'Все приоритеты');
      fillSelect(form.elements.status, state.meta.statuses, '');
      fillSelect(form.elements.priority, state.meta.priorities, '');
    }

    async function refresh() {
      const params = new URLSearchParams({
        search: document.querySelector('#searchInput').value.trim(),
        status: document.querySelector('#statusFilter').value,
        priority: document.querySelector('#priorityFilter').value,
      });
      state.tickets = await request(`/api/tickets?${params}`);
      renderTickets();
      const stats = await request('/api/stats');
      document.querySelector('#statNew').textContent = stats.new;
      document.querySelector('#statProgress').textContent = stats.in_progress;
      document.querySelector('#statUrgent').textContent = stats.urgent;
      document.querySelector('#statClosed').textContent = stats.closed_today;
    }

    function renderTickets() {
      if (!state.tickets.length) {
        list.innerHTML = '<div class="empty">Заявок не найдено. Создайте первую заявку или измените фильтры.</div>';
        return;
      }
      list.innerHTML = state.tickets.map(ticket => `
        <button class="ticket" type="button" data-id="${ticket.id}">
          <div>
            <h3>${escapeHtml(ticket.client_name)} · ${escapeHtml(ticket.contact)}</h3>
            <p>${escapeHtml(ticket.company || 'Без компании')}</p>
            <p>${escapeHtml(ticket.description).slice(0, 180)}</p>
          </div>
          <div class="badges">
            <span class="badge ${ticket.priority === 'срочный' ? 'urgent' : ''}">${ticket.priority}</span>
            <span class="badge ${ticket.status === 'закрыта' ? 'closed' : ''}">${ticket.status}</span>
          </div>
        </button>
      `).join('');
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
    }

    function showError(message) {
      errorBox.textContent = message;
      errorBox.style.display = 'block';
    }

    function clearError() {
      errorBox.textContent = '';
      errorBox.style.display = 'none';
    }

    function openNewTicket() {
      state.currentId = null;
      form.reset();
      form.elements.priority.value = 'обычный';
      form.elements.status.value = 'новая';
      document.querySelector('#dialogTitle').textContent = 'Новая заявка';
      document.querySelector('#deleteButton').style.display = 'none';
      document.querySelector('#commentSection').innerHTML = '';
      document.querySelector('#commentText').value = '';
      document.querySelector('#commentButton').disabled = true;
      clearError();
      dialog.showModal();
    }

    async function openTicket(id) {
      const ticket = await request(`/api/tickets/${id}`);
      state.currentId = ticket.id;
      for (const field of ['client_name', 'contact', 'company', 'description', 'priority', 'status']) {
        form.elements[field].value = ticket[field] || '';
      }
      document.querySelector('#dialogTitle').textContent = `Заявка #${ticket.id}`;
      document.querySelector('#deleteButton').style.display = '';
      document.querySelector('#commentButton').disabled = false;
      renderComments(ticket.comments);
      clearError();
      dialog.showModal();
    }

    function renderComments(comments) {
      const section = document.querySelector('#commentSection');
      section.innerHTML = '<h3>Комментарии</h3>' + (comments.length ? comments.map(comment => `
        <div class="comment"><strong>${escapeHtml(comment.author)}</strong><br><small>${escapeHtml(comment.created_at)}</small><p>${escapeHtml(comment.text)}</p></div>
      `).join('') : '<p class="empty">Комментариев пока нет.</p>');
    }

    form.addEventListener('submit', async event => {
      event.preventDefault();
      clearError();
      const payload = Object.fromEntries(new FormData(form).entries());
      try {
        if (state.currentId) {
          await request(`/api/tickets/${state.currentId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
          const created = await request('/api/tickets', { method: 'POST', body: JSON.stringify(payload) });
          state.currentId = created.id;
        }
        dialog.close();
        await refresh();
      } catch (error) {
        showError(error.message);
      }
    });

    document.querySelector('#newTicketButton').addEventListener('click', openNewTicket);
    document.querySelector('#closeDialogButton').addEventListener('click', () => dialog.close());
    document.querySelector('#searchInput').addEventListener('input', () => refresh());
    document.querySelector('#statusFilter').addEventListener('change', () => refresh());
    document.querySelector('#priorityFilter').addEventListener('change', () => refresh());
    list.addEventListener('click', event => {
      const item = event.target.closest('.ticket');
      if (item) openTicket(item.dataset.id).catch(error => alert(error.message));
    });
    document.querySelector('#deleteButton').addEventListener('click', async () => {
      if (!state.currentId || !confirm('Удалить эту заявку без восстановления?')) return;
      await request(`/api/tickets/${state.currentId}`, { method: 'DELETE' });
      dialog.close();
      await refresh();
    });
    document.querySelector('#commentButton').addEventListener('click', async () => {
      const textInput = document.querySelector('#commentText');
      const text = textInput.value.trim();
      if (!state.currentId || !text) return;
      await request(`/api/tickets/${state.currentId}/comments`, {
        method: 'POST',
        body: JSON.stringify({ author: state.meta.admin_name, text }),
      });
      textInput.value = '';
      await openTicket(state.currentId);
      await refresh();
    });

    loadMeta().then(refresh).catch(error => {
      list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    });
  </script>
</body>
</html>
"""
