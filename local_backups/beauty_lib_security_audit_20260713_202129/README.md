# Beauty Lip

Beauty Lip is a functional MVP for a beauty salon marketplace and booking product. It includes a FastAPI backend, SQLite persistence for local development, transactional appointment creation, role-based access control, a responsive React/Vite customer and manager interface, seed data, and automated tests for critical booking behavior.

## Technology Stack

- Backend: FastAPI, Pydantic, SQLite through Python's standard `sqlite3` module.
- Frontend: React with Vite, implemented as a mobile-first responsive interface that can later share API contracts with Expo React Native.
- Database: SQLite for local MVP development. The schema is normalized and documented in `schema.sql`; production should use PostgreSQL with an exclusion constraint for appointment overlap protection.
- Authentication: Email/password login with PBKDF2 password hashing and bearer sessions stored in the database.

This stack was selected because it runs on Windows with simple commands, supports Android/iOS-facing API clients, and keeps the MVP small enough to verify locally.

## Local Setup On Windows

```powershell
python -m pip install -r requirements.txt
npm install
```

Optional configuration can be copied from `.env.example`. Do not put real secrets in source control.

## Run Backend

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

The API documentation is available at `http://127.0.0.1:8000/docs`.

## Run Frontend

```powershell
npm run dev
```

Open the Vite URL shown in the terminal. If the backend uses a different port, set `VITE_API_URL` before starting Vite.

## Build Frontend

```powershell
npm run build
```

## Run Tests

```powershell
python -m pytest -q
```

## Development Accounts

These accounts are seeded for local development only when the database is empty. They are not suitable for production configuration.

- Customer: `customer@beautylip.local` / `BeautyLip123!`
- Salon manager: `manager@beautylip.local` / `BeautyLip123!`
- Administrator: `admin@beautylip.local` / `BeautyLip123!`

## Implemented MVP Workflow

- Customer registers or logs in.
- Customer searches salons by salon, service, specialist, city, or category.
- Customer opens salon profile and sees services, specialists, pricing, address, and ratings.
- Customer requests server-calculated available slots.
- Customer books an appointment with a final transactional availability check.
- Customer sees appointments, can reschedule, cancel, save favorite salons, and review completed appointments.
- Salon manager logs in, sees appointments, and confirms or completes bookings through protected endpoints.
- Administrator can inspect system statistics through a protected endpoint.

## Key API Groups

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/salons`
- `GET /api/salons/{salon_id}`
- `GET /api/services`
- `GET /api/availability`
- `POST /api/appointments`
- `PATCH /api/appointments/{id}/reschedule`
- `PATCH /api/appointments/{id}/cancel`
- `GET /api/appointments`
- `POST /api/favorites/salons/{salon_id}`
- `POST /api/reviews`
- `GET /api/notifications`
- `GET /api/manager/dashboard`
- `PATCH /api/manager/appointments/{id}/status`
- `GET /api/admin/stats`

## Database And Booking Protection

The local schema includes users, salons, branches, specialists, service categories, services, specialist-service assignments, working schedules, schedule exceptions, appointments, favorites, reviews, notifications, and audit logs.

Appointment creation uses `BEGIN IMMEDIATE`, checks service assignment, working schedule, breaks, day-off exceptions, existing active appointments, lead time, buffers, and a partial unique index on active appointment slots before commit. SQLite cannot express full time-range exclusion constraints; PostgreSQL should add a range exclusion constraint for production.

## Notifications And Payments

In-app notifications are implemented for booking creation. Email is reported as available only when SMTP environment variables are configured. Payment provider integration is intentionally not enabled; appointment payment status defaults to `not_required`, and no card data is stored.

## Mobile And Platform Notes

The React UI is responsive and mobile-first for MVP validation. The API, auth separation, localization keys, and screen structure are prepared for an Expo React Native client targeting Android and iOS. Android builds can be added through Expo/EAS. iOS production builds require Apple tooling and cannot be produced locally on Windows without macOS or a cloud build service.

## Known Limitations

- The current UI is a responsive web implementation rather than a packaged native Expo app.
- Map rendering is represented by stored coordinates and API-ready salon data; provider credentials are optional and not hardcoded.
- Image upload storage abstraction is documented in the schema/API direction but not exposed as an upload endpoint in this MVP.
- Production deployment should use PostgreSQL, managed object storage, HTTPS, stricter token expiry, rate limiting at the edge, and legal review for GDPR operations.

## Final Conclusion

BEAUTY LIP MVP FAILED until the complete customer booking and salon-management E2E workflow is executed successfully in the target environment. The included automated tests exercise the core E2E booking path locally.
