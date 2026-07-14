# Beauty Lip MVP

Beauty Lip is a runnable cross-platform-oriented salon booking MVP with a FastAPI backend, SQLite persistence for local development, and a responsive React/Vite client that can serve customer, salon-manager, and administrator workflows from one codebase.

## Stack

- Backend: FastAPI with SQLite fallback when `DATABASE_URL` is not set.
- Client: React with Vite, responsive for mobile and web management use.
- Database: normalized SQLite schema for local MVP; production can replace it with PostgreSQL through the documented `DATABASE_URL` boundary.
- Auth: password hashing with PBKDF2 and signed bearer tokens; set `JWT_SECRET` in production.

## Windows Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm install
```

## Run Backend

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open API docs at `http://127.0.0.1:8000/docs`.

## Run Client

```powershell
npm run dev
```

The Vite client proxies API calls to the backend when both are running locally. A production static build is created with:

```powershell
npm run build
```

## Tests

```powershell
python -m pytest -q
```

## Demo Accounts

Development seed data is created automatically only when the local database is empty.

- Customer: `customer@beautylip.dev` / `BeautyLip123!`
- Salon manager: `manager@beautylip.dev` / `BeautyLip123!`
- Professional: `pro@beautylip.dev` / `BeautyLip123!`
- Administrator: `admin@beautylip.dev` / `BeautyLip123!`

These credentials are development-only and must not be enabled in production deployments.

## Implemented Workflow

- Register and log in with email/password.
- Browse and search salons, branches, specialists, services, and prices from backend data.
- Calculate real server-side availability from working schedules, breaks, day-off/blocked exceptions, existing appointments, service duration, buffers, lead time, and booking window.
- Create, reschedule, cancel, confirm, complete, and no-show appointments with status-transition validation.
- Prevent double booking through a write lock, transaction, overlap query, and final availability check before commit.
- Persist favorites, reviews, notifications, audit logs, users, salons, branches, specialists, schedules, exceptions, services, and appointments.
- Provide manager dashboard/calendar/appointment/service/specialist/customer endpoints with role-based access.
- Provide administrator salon approval, user listing, review moderation, audit-log, category, and statistics endpoints with role-based access.
- Optional payments remain disabled with `payment_status = not_required`; no card data is stored.
- Notification abstraction creates in-app notifications and logs email-provider unavailability when SMTP is not configured.

## Important API Groups

- `/api/auth/register`, `/api/auth/login`, `/api/auth/me`
- `/api/salons`, `/api/salons/{id}`
- `/api/specialists/{id}`
- `/api/services`
- `/api/availability`
- `/api/appointments`
- `/api/favorites/salons`, `/api/favorites/specialists`
- `/api/reviews`
- `/api/notifications`
- `/api/manager/*`
- `/api/admin/*`

## Environment

Copy `.env.example` to `.env` for local overrides. Required production values include `DATABASE_URL`, `JWT_SECRET`, optional Stripe keys, and optional SMTP settings. Missing Stripe/SMTP credentials do not block booking.

## iOS and Android Notes

The current client is a responsive React MVP structured for mobile-first flows. To package native iOS/Android apps next, move the API/data/session/domain modules into an Expo React Native shell. Android builds can be done on Windows with Android Studio and Expo/EAS. iOS local builds require macOS and Apple tooling; Windows can use cloud EAS builds.

## Known Limitations

- Map rendering is configuration-ready but not bound to a paid provider; the UI shows a clear map configuration message.
- Payments, push, SMS, and WhatsApp are documented extension points and not active payment/communication integrations.
- SQLite is intended for local MVP testing; production should use PostgreSQL with equivalent constraints/migrations.

Final conclusion for this local delivery depends on the verification commands below and the executed end-to-end workflow.
