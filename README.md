# Blood Donation

A web platform that connects blood donors with hospitals and patients. Hospitals raise SOS blood requests, nearby donors are found on a map, and matching donors are alerted by email.

Built for the Ignite IT 7.0 hackathon.

## Features

- **Donor and hospital accounts:** separate sign-up/login flows and dashboards
- **SOS requests:** hospitals create urgent blood requests; donors can view and accept them
- **Proximity search:** finds registered donors within 2 km of a hospital on a Leaflet map
- **Route map:** shows the route between a donor and the hospital (OpenRouteService)
- **Email alerts:** sends urgent-request emails to nearby donors via Gmail SMTP
- **Rewards:** a reward page for active donors
- **Info pages:** How it works, For Donors, For Patients, FAQ, About Us, Terms & Conditions

## Tech Stack

| Layer    | Tech                                              |
| -------- | ------------------------------------------------- |
| Backend  | Python, Flask, Flask-CORS                         |
| Database | Supabase (accessed from the browser via supabase-js) |
| Frontend | HTML, Tailwind CSS, JavaScript                    |
| Maps     | Leaflet, OpenStreetMap, OpenRouteService          |
| Email    | Gmail SMTP                                        |

## Project Structure

```
Blood-Donation/
├── app.py              # Main Flask app (port 5000); serves all pages and starts proximity.py
├── proximity.py        # Email alert service (port 5001); POST /send
├── match.py            # Standalone CLI blood-group compatibility checker
├── templates/          # Jinja/HTML pages
├── requirements.txt
├── .env.example        # Template for required environment variables
└── .gitignore
```

## Getting Started

### Prerequisites

- Python 3.10+
- A [Supabase](https://supabase.com) project with `donors`, `hospitals` and `sos_requests` tables
- A Gmail account with an [App Password](https://support.google.com/accounts/answer/185833)
- An [OpenRouteService](https://openrouteservice.org) API key

### Setup

```bash
git clone https://github.com/RashiShah08/Blood-Donation.git
cd Blood-Donation

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env` and fill in your SMTP credentials and secret key.
2. In the files under `templates/`, replace the `<Your_supabase_url_here>` and `<Your_supabase_key_here>` placeholders with your Supabase project URL and anon key.
3. In `templates/map.html`, replace `<Your_ors_api_key_here>` with your OpenRouteService key.

### Run

```bash
python app.py
```

This starts the site at http://127.0.0.1:5000 and launches the email service on port 5001.

To try the blood-group compatibility checker on its own:

```bash
python match.py
```
