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
- Five failed logins for one account lock it for 15 minutes. The count lives in the database, so it holds across
  every server process, and it works the same for unregistered emails, so it never reveals who has an account.
- Hospitals must be verified before they can email donors, so a stranger can't sign up and spam them.
- Security headers on every response: HSTS over HTTPS, frame blocking (only the public information pages can be shown inside other sites), no MIME sniffing, a strict referrer policy,
  and cross-origin isolation. Pages are never cached; versioned static files are cached for a year.
- The database connection is encrypted and its certificate verified whenever the URL asks for `sslmode=require`.
- Strict Content Security Policy: no inline scripts, no third-party scripts or styles, Leaflet served from the repo, and no CDNs.
- Only the server connects to PostgreSQL. The browser never talks to the database, and no database credentials reach the page.
- API keys and tokens (Gmail API, Brevo, SMTP, OpenRouteService) live in environment variables on the server and never
  reach the browser. The Gmail API token is send-only: it cannot read the mailbox.
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

## Deploy for free

The Flask app serves both the pages and the API, so one deployment runs everything.

| Part | Service | Free tier |
|------|---------|-----------|
| App (pages + API) | [Vercel](https://vercel.com) Hobby | 1M requests and 4 CPU-hours a month; personal, non-commercial use |
| Database | [Neon](https://neon.com) PostgreSQL | 0.5 GB, permanent; pauses after 5 minutes idle |
| Email alerts | [Gmail API](https://developers.google.com/workspace/gmail/api) with a send-only token | about 500 emails a day |

`vercel.json` runs the app in Singapore (`sin1`), next to Neon's Singapore region, the closest to India.

1. **Neon:** create a project in *AWS Asia Pacific (Singapore)*. The pooled connection string (host
   contains `-pooler`, ends in `?sslmode=require`) is the one to use.
2. **Gmail API** (free, official, send-only): in the [Google Cloud console](https://console.cloud.google.com)
   create a project, enable the **Gmail API**, set up the **OAuth consent screen** (External, add the
   `gmail.send` scope, then **Publish app** so the token doesn't expire after 7 days), and create an
   **OAuth client ID** of type **Desktop app**. Then run, in your own terminal:

   ```bash
   python scripts/google_gmail_token.py
   ```

   It opens Google's consent page (for an unpublished-to-Google app, choose *Advanced → Go to
   BloodConnect*) and prints the refresh token. The token can only send mail: it can't read the inbox.
3. **Vercel:** Add New → Project → import this repository. No build settings are needed. Add these
   environment variables for Production:

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | Neon's pooled connection string |
   | `FLASK_SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `SESSION_COOKIE_SECURE` | `1` |
   | `TRUST_PROXY_HOPS` | `1` |
   | `REQUIRE_HOSPITAL_VERIFICATION` | `1` |
   | `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` | the Desktop app OAuth client |
   | `GMAIL_REFRESH_TOKEN` | printed by `scripts/google_gmail_token.py` |
   | `GMAIL_SENDER` | the Gmail address you authorised |
   | `PUBLIC_BASE_URL` | your `https://….vercel.app` address |
   | `ORS_API_KEY` | optional, for road directions |

4. Deploy. Tables are created automatically on the first request; `/readyz` confirms the database.

Hospitals must be verified before they can alert donors. Verify one from your own computer, with
`DATABASE_URL` in `.env` pointing at Neon:

```bash
flask --app app verify-hospital hospital@example.org
```

`seed-demo` refuses to write to a database that isn't on your machine, because demo accounts share a
published password.

**Alternative, Render:** `render.yaml` deploys the same app as a long-running server (New → Blueprint).
Render's free tier sleeps after 15 idle minutes (about a minute to wake) and blocks SMTP, so use the
Gmail API (HTTPS) there too. Use Neon rather than Render's free database, which is deleted after 30 days.

## Medical note

The eligibility check follows India's National Blood Transfusion Council donor selection guidelines. It's a screening aid only: the medical officer at the blood bank always decides. Blood donation in India is voluntary and unpaid, so BloodConnect never offers money, coupons or gift cards.
