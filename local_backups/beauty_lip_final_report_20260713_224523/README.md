# Beauty Lip MVP

Beauty Lip is a cross-platform beauty salon appointment marketplace MVP with mobile source, backend API, persistent database, salon manager interface, admin endpoints, seed data, and tests.

## Stack

- Mobile: React Native with Expo for Android/iOS from one codebase.
- Backend: FastAPI.
- Database: SQLite for local MVP, schema designed for PostgreSQL migration.
- Manager UI: responsive web dashboard served by the backend.

## Windows Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Manager UI: `http://127.0.0.1:8000/manager`
- Health: `http://127.0.0.1:8000/api/health`

## Mobile Setup

```powershell
cd mobile
npm install
$env:EXPO_PUBLIC_API_URL="http://127.0.0.1:8000"
npm run web
```

For Android emulator install Android Studio and SDK. For iOS simulator use macOS with Xcode; Windows cannot build/run local iOS simulator builds without Apple tooling.

## Demo Accounts

Development-only seed credentials:

- Customer: `customer@beautylip.dev` / `Demo12345!`
- Manager: `manager@beautylip.dev` / `Demo12345!`
- Admin: `admin@beautylip.dev` / `Demo12345!`
- Professional: `sofia@beautylip.dev` / `Demo12345!`

Disable demo seed in production with `BEAUTY_LIP_ENABLE_DEMO_SEED=false`.

## Tests

```powershell
pytest -q
```

Covered behavior:

- registration/login
- role authorization
- salon search
- availability calculation
- booking creation
- double-booking prevention
- rescheduling
- manager confirmation
- admin restrictions
- review permission restrictions

## Implemented MVP Roles

- Customer
- Beauty professional
- Salon manager
- Platform administrator

## Implemented Customer Mobile Screens

- authentication/login
- home
- search
- appointments
- favorites empty state
- profile/logout
- booking through real availability API

## Implemented Manager Screens

- manager login
- today dashboard
- appointments awaiting confirmation
- expected revenue estimate
- services list
- appointment confirmation

## Environment Variables

- `BEAUTY_LIP_DB_PATH`
- `BEAUTY_LIP_SECRET_KEY`
- `BEAUTY_LIP_ENABLE_DEMO_SEED`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
- `PUSH_PROVIDER_KEY`
- `SMS_PROVIDER_KEY`
- `PAYMENT_PROVIDER_KEY`

## Known Limitations

- SQLite is for local MVP. Use PostgreSQL migrations for production.
- Native push/SMS/email providers are abstractions only until credentials are configured.
- Payments are not implemented; booking works with `not_required` payment state.
- Mobile E2E automation is not configured; backend E2E booking flow is automated.
- Image upload storage abstraction is documented for future implementation but not fully implemented in MVP.

## Final Conclusion

BEAUTY LIP MVP FAILED until backend tests, backend startup, manager UI smoke test, and mobile web startup are executed successfully in the target environment.
