# Test-Project-Recovery

Browser-based ticket/request tracker for a small IT company. It includes a dark desktop/tablet interface, one local administrator, SQLite persistence, ticket CRUD, comments, search, filters, dashboard counts, and automated API tests.

## Windows Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a browser.

Default local administrator credentials:

```text
Username: admin
Password: admin123
```

To override the local administrator credentials or database path before running:

```powershell
$env:TICKET_TRACKER_ADMIN_USER="admin"
$env:TICKET_TRACKER_ADMIN_PASSWORD="choose-a-local-password"
$env:TICKET_TRACKER_DB="tickets.db"
```

## Test

```powershell
python -m pytest -q
```

## Data Storage

Tickets and comments are stored in SQLite at `tickets.db` by default. The database is created automatically on startup.
