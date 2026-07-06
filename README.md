# Google Rank Tracker

FastAPI + Jinja rank tracking app using Serper, Neon Postgres, Render, and GitHub auto deploys.

## Features

- User login with bcrypt password hashing
- Roles: `admin`, `manager`, `viewer`
- Admin user management
- Manager project, keyword, note, and refresh actions
- Viewer read-only dashboards and reports
- CSRF-protected forms
- Session expiry after inactivity
- Project dashboard, project detail pages, keyword search, pagination
- Ranking trend reports, keyword history, SERP snapshots, CSV export
- Semrush search-volume lookup on keyword add and refresh

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Create `.env` in the project root:

```text
DATABASE_URL=your_neon_connection_string
SERPER_API_KEY=your_serper_api_key
SEMRUSH_API_KEY=your_semrush_api_key
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=choose-a-strong-admin-password
ADMIN_NAME=Admin
SESSION_SECRET=a-long-random-secret
SESSION_TIMEOUT_SECONDS=1800
PORT=8000
```

Open:

```text
http://127.0.0.1:8000/login
```

The app creates database tables and migrations automatically on startup. The first admin user is created from `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and `ADMIN_NAME` only if no user with that email exists.

## Render Settings

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Environment variables:

```text
DATABASE_URL=your_neon_connection_string
SERPER_API_KEY=your_serper_api_key
SEMRUSH_API_KEY=your_semrush_api_key
ADMIN_EMAIL=your_admin_email
ADMIN_PASSWORD=your_first_admin_password
ADMIN_NAME=Admin
SESSION_SECRET=a-long-random-secret
SESSION_TIMEOUT_SECONDS=1800
PYTHON_VERSION=3.12.10
```

Do not set `PORT`; Render supplies it automatically.

## Roles

- `admin`: manage users plus all manager permissions
- `manager`: create/edit projects, add keywords, run rank checks, add notes
- `viewer`: view dashboards, projects, keywords, reports, competitors

## Database Changes

Migrations live in `migrations/`. The app records applied migrations in `schema_migrations`, so existing project, keyword, rank check, and SERP data are preserved.
