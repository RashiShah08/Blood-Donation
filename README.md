# BloodConnect

BloodConnect connects hospitals that urgently need blood with nearby, compatible, voluntary donors.
A hospital posts a request, the right donors are alerted, and a donor checks their eligibility, pledges a unit and follows live directions to the hospital.

Built for the Ignite IT 7.0 hackathon, then rebuilt to be secure, tested and production-ready.

## Features

**For hospitals**
- Create blood requests with blood group, units needed and urgency. Patient details stay private to the hospital.
- Search for available, compatible donors within 2, 5, 10 or 25 km on a map. Donors appear at approximate positions only, to protect their privacy.
- Alert matching donors by email. Each donor is alerted at most once per request.
- See who pledged, with their contact details, record whether they donated or didn't arrive, and close requests.
- Dashboard with open requests, units still needed, donors on the way, median time from alert to pledge, and fulfilment rate.

**For donors**
- Register with blood group, date of birth, weight and home area (tap the map or use GPS).
- See open requests your blood group can help with, most urgent first.
- An 11-question eligibility check based on India's NBTC guidelines. Age, weight and the gap since your last donation are also checked on the server.
- Pledge a unit. Double pledges and over-pledging are prevented in the database.
- Live directions to the hospital, with road routes via OpenRouteService through the server and arrival detection.
- Recognition without payment: donation history, badges and a printable certificate.
- Edit your profile, pause alerts, or delete your account and data.

## Security and privacy

- Passwords are salted hashes (Werkzeug scrypt). Logins use server-side sessions with HttpOnly, SameSite cookies.
- Every page and API checks the logged-in account. A hospital can't read or change another hospital's data.
- CSRF protection on all forms and JSON APIs, and rate limits on login, registration, password reset, alerts and routing.
- Strict Content Security Policy: no inline scripts, no third-party scripts or styles, Leaflet served from the repo, and no CDNs.
- Only the server connects to PostgreSQL. The browser never talks to the database, and no database credentials reach the page.
- API keys (SMTP, OpenRouteService) live in `.env` on the server and never reach the browser.
- Generic error messages for users, with details logged on the server.

## Tech stack

| Layer | Tech |
| --- | --- |
| Backend | Python 3.11+, Flask 3, Flask-SQLAlchemy, Flask-WTF (CSRF), Flask-Limiter |
| Database | PostgreSQL 16 (Docker Compose for local development) |
| Frontend | Server-rendered Jinja templates, one hand-written CSS file, plain JavaScript |
| Maps | Leaflet 1.9.4 (vendored), OpenStreetMap tiles, OpenRouteService (optional) |
| Email | Any SMTP server (e.g. Gmail with an App Password) |
| Tests | pytest (unit, API, integration), Playwright (browser end-to-end) |

## Project structure

```
Blood-Donation/
├── app.py                    # Entry point: python app.py
├── bloodconnect/
│   ├── __init__.py           # App factory
│   ├── config.py             # Settings from environment variables
│   ├── models.py             # Donor, Hospital, BloodRequest, Pledge, Notification
│   ├── security.py           # Sessions, access control, security headers
│   ├── validation.py         # Server-side form validation
│   ├── main.py auth.py donor.py hospital.py   # Blueprints (pages + JSON APIs)
│   ├── errors.py cli.py
│   ├── domain/               # Pure rules: blood compatibility, eligibility, distance, badges
│   ├── services/             # Matching, pledges, email, routing
│   ├── templates/            # base.html + pages
│   └── static/               # css, js, icons, vendored Leaflet
├── db/schema.sql             # PostgreSQL schema
├── db/init/                  # creates the test database when the Docker volume is first set up
├── docker-compose.yml        # local PostgreSQL
├── tests/                    # 400+ tests
├── requirements.txt / requirements-dev.txt
└── .env.example
```

## Getting started

```bash
git clone https://github.com/RashiShah08/Blood-Donation.git
cd Blood-Donation

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Start PostgreSQL (the `DATABASE_URL` in `.env.example` already points at it), then run the app:

```bash
docker compose up -d        # PostgreSQL 16 on localhost:5432, with app and test databases
flask --app app seed-demo   # optional: demo hospitals, donors and a request around Mumbai
python app.py               # http://127.0.0.1:5000
```

No Docker? Install PostgreSQL 16, create a `bloodconnect` user with `bloodconnect` and `bloodconnect_test` databases (or use your own and set `DATABASE_URL` / `TEST_DATABASE_URL`).

Demo logins (password `demo-password`): hospital `citygeneral@example.com`, donor `donor1@example.com`.

Without SMTP credentials, alert and password reset emails are written to the console instead of being sent (demo mode).

## Configuration

All settings are environment variables, documented in [`.env.example`](.env.example). The main ones:

| Variable | Purpose |
| --- | --- |
| `FLASK_SECRET_KEY` | Required in production. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_DEBUG` | `1` for local development only |
| `DATABASE_URL` | PostgreSQL connection string (required) |
| `TEST_DATABASE_URL` | PostgreSQL database for the test suite; emptied on every run, and its name must end in `test` |
| `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `SMTP_HOST`, `SMTP_PORT` | Email delivery |
| `PUBLIC_BASE_URL` | Site URL used in email links |
| `ORS_API_KEY` | OpenRouteService key for road routes (optional) |
| `SESSION_COOKIE_SECURE` | `1` when served over HTTPS |
| `REQUIRE_HOSPITAL_VERIFICATION` | `1` to require `flask --app app verify-hospital EMAIL` before a hospital can alert donors |

### Database

Tables are created automatically on start-up. [`db/schema.sql`](db/schema.sql) documents the schema and can be applied by hand with `psql "$DATABASE_URL" -f db/schema.sql`. In production, give the app its own database role and never expose the PostgreSQL port publicly.

## Testing

```bash
docker compose up -d                 # tests run against PostgreSQL (bloodconnect_test)
pytest -m "not e2e"                  # unit, API and integration tests (fast)
python -m playwright install chromium
pytest -m e2e                        # browser tests against a live server
pytest                               # everything
ruff check .                         # lint
```

What's covered:
- **Domain rules:** the full 8×8 blood compatibility matrix, every eligibility question and age, weight and interval boundary, distance maths, badges.
- **Validation and configuration:** every field rule, database URL handling, and refusing to start without a secret key.
- **Auth:** registration, login, open-redirect protection, logout, and the password reset lifecycle (including expired and reused links).
- **Security:** anonymous and cross-role access, hospital-to-hospital and donor-to-donor isolation, CSRF, rate limiting, security headers, XSS escaping, no patient data shown to donors, no donor identity in the search API, local assets only.
- **Donor and hospital flows:** matching, pledging (duplicates, over-pledging, cancel and re-pledge), directions and arrival, impact, profile, account deletion, request lifecycle, alerts (once only, partial and total failure), outcomes and dashboard statistics.
- **Services:** SMTP batching and failures, OpenRouteService parsing, caching and fallback, pledge concurrency guard.
- **Browser (Playwright):** every public page with no console, CSP or network errors and labelled inputs; mobile menu; skip link; the full journey from registration, request and alert to pledge, live directions, arrival, recorded donation and badge; ineligible answers; cancelling a pledge; markup never executed; CSRF against cross-site calls.

## CLI commands

```bash
flask --app app init-db               # create missing tables
flask --app app seed-demo [--force]   # demo data (debug mode, or --force)
flask --app app verify-hospital EMAIL # mark a hospital as verified
python -m bloodconnect.domain.blood   # interactive compatibility checker
```

## Medical note

The eligibility check follows India's National Blood Transfusion Council donor selection guidelines. It's a screening aid only: the medical officer at the blood bank always decides. Blood donation in India is voluntary and unpaid, so BloodConnect never offers money, coupons or gift cards.
