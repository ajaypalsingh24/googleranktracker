# Google Rank Tracker

FastAPI rank tracking app for Google keywords using Serper, Neon Postgres, Render, and GitHub auto deploys.

## Stack

- Python + FastAPI
- Jinja server-rendered HTML
- Neon Postgres
- Serper API
- Render web service

## Local Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Edit `.env`:

```text
DATABASE_URL=your_neon_connection_string
SERPER_API_KEY=your_serper_api_key
APP_PASSWORD=your_dashboard_password
SESSION_SECRET=a_long_random_secret
PORT=8000
```

Run the app:

```bash
uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Render Setup

1. Push this folder to `https://github.com/ajaypalsingh24/googleranktracker.git`.
2. Create a new Render Web Service from that GitHub repo.
3. Use:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

4. Add Render environment variables:

```text
DATABASE_URL=your_neon_connection_string
SERPER_API_KEY=your_serper_api_key
APP_PASSWORD=your_dashboard_password
SESSION_SECRET=a_long_random_secret
```

The app creates database tables automatically on startup.

## Features

- Projects for domains
- Keyword tracking
- Manual keyword check
- Refresh all keywords
- Rank history storage
- SERP result snapshots
- Average position and Top 3/10/30/100 cards
- Project notes
- Simple password protection

## Important

Do not commit `.env` to GitHub. It contains private database credentials and API keys. Add those values inside Render's Environment tab instead.
