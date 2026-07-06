from __future__ import annotations

import csv
import math
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Jsonb
from psycopg.errors import UniqueViolation
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.security import (
    bootstrap_admin,
    can,
    csrf_token,
    hash_password,
    login_user,
    logout_user,
    require_user,
    verify_csrf,
    verify_password,
)
from app.serper import check_keyword_rank
from app.search_volume import fetch_keyword_volumes

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["can"] = can


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.ensure_schema()
    bootstrap_admin()
    yield


app = FastAPI(title="Google Rank Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"),
    same_site="lax",
    https_only=os.getenv("RENDER") == "true",
    max_age=None,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def render(request: Request, template: str, context: dict[str, Any] | None = None, status_code: int = 200):
    context = context or {}
    user_or_redirect = require_user(request)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    context.update(
        {
            "request": request,
            "current_user": user_or_redirect,
            "csrf_token": csrf_token(request),
            "projects_nav": project_options(),
        }
    )
    return templates.TemplateResponse(template, context, status_code=status_code)


def redirect_to(path: str, **params: Any) -> RedirectResponse:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    query = f"?{urlencode(clean)}" if clean else ""
    return RedirectResponse(f"{path}{query}", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.fetch_one("select * from users where lower(email) = lower(%s) and active = true", (email.strip(),))
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid email or password"},
            status_code=401,
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.post("/register")
def register(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Password must be at least 8 characters"},
            status_code=400,
        )
    try:
        user = db.execute(
            """
            insert into users (email, name, password_hash, role)
            values (%s, %s, %s, 'manager')
            returning *
            """,
            (email.strip().lower(), name.strip(), hash_password(password)),
        )
    except UniqueViolation:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "An account with this email already exists"},
            status_code=400,
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token_value: str = Form(..., alias="csrf_token")):
    verify_csrf(request, csrf_token_value)
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@app.post("/theme")
def set_theme(request: Request, theme: str = Form(...), csrf_token_value: str = Form(..., alias="csrf_token")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    request.session["theme"] = theme if theme in {"dark", "red", "blue"} else "dark"
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, project_id: str | None = None, message: str | None = None):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    projects = project_cards()
    current_project = select_project(projects, project_id)
    metrics = empty_metrics()
    history = []
    keywords = []
    if current_project:
        detail = load_project_dashboard(str(current_project["id"]), limit=12)
        metrics = detail["metrics"]
        history = detail["history"]
        keywords = detail["keywords"]
    return render(
        request,
        "dashboard.html",
        {
            "active_nav": "dashboard",
            "projects": projects,
            "project": current_project,
            "metrics": metrics,
            "history": history,
            "keywords": keywords,
            "message": message,
        },
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, q: str = "", message: str | None = None):
    pattern = f"%{q.strip()}%"
    projects = db.fetch_all(
        """
        select p.*,
          count(k.id)::int as keyword_count,
          count(k.id) filter (where k.active)::int as active_keyword_count
        from projects p
        left join keywords k on k.project_id = p.id
        where %s = '' or p.name ilike %s or p.domain ilike %s
        group by p.id
        order by p.created_at desc
        """,
        (q.strip(), pattern, pattern),
    )
    return render(request, "projects.html", {"active_nav": "projects", "projects": projects, "q": q, "message": message})


@app.post("/projects")
def create_project(
    request: Request,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    name: str = Form(...),
    domain: str = Form(...),
    country: str = Form("India"),
    location: str = Form("India"),
    gl: str = Form("in"),
    hl: str = Form("en"),
    device: str = Form("desktop"),
    check_frequency: str = Form("manual"),
    competitors: str = Form(""),
):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    competitor_list = clean_lines(competitors)
    project = db.execute(
        """
        insert into projects (name, domain, country, location, gl, hl, device, check_frequency, competitors)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            name.strip(),
            domain.strip(),
            country.strip(),
            location.strip(),
            gl.strip().lower(),
            hl.strip().lower(),
            device,
            check_frequency,
            competitor_list,
        ),
    )
    return redirect_to(f"/projects/{project['id']}", message="Project added")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    request: Request,
    project_id: str,
    q: str = "",
    page: int = 1,
    per_page: int = 25,
    check_range: str = "last_check",
    serp_check_id: str | None = None,
    message: str | None = None,
):
    page = max(page, 1)
    per_page = min(max(per_page, 10), 100)
    detail = load_project_dashboard(project_id, q=q, page=page, per_page=per_page, check_range=check_range, serp_check_id=serp_check_id)
    if not detail["project"]:
        raise HTTPException(status_code=404, detail="Project not found")
    return render(
        request,
        "project_detail.html",
        {
            "active_nav": "projects",
            "message": message,
            "q": q,
            "page": page,
            "per_page": per_page,
            "check_range": check_range,
            **detail,
        },
    )


@app.post("/projects/{project_id}/edit")
def edit_project(
    request: Request,
    project_id: str,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    name: str = Form(...),
    domain: str = Form(...),
    country: str = Form("India"),
    location: str = Form("India"),
    gl: str = Form("in"),
    hl: str = Form("en"),
    device: str = Form("desktop"),
    check_frequency: str = Form("manual"),
    competitors: str = Form(""),
):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    db.execute(
        """
        update projects
        set name = %s, domain = %s, country = %s, location = %s, gl = %s, hl = %s,
            device = %s, check_frequency = %s, competitors = %s, updated_at = now()
        where id = %s
        returning id
        """,
        (
            name.strip(),
            domain.strip(),
            country.strip(),
            location.strip(),
            gl.strip().lower(),
            hl.strip().lower(),
            device,
            check_frequency,
            clean_lines(competitors),
            project_id,
        ),
    )
    return redirect_to(f"/projects/{project_id}", message="Project updated")


@app.post("/projects/{project_id}/keywords")
def create_keyword(
    request: Request,
    project_id: str,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    phrases: str = Form(...),
    tags: str = Form(""),
    search_volume: str = Form(""),
):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    unique_phrases = list(dict.fromkeys(clean_lines(phrases)))
    if not unique_phrases:
        return redirect_to(f"/projects/{project_id}", message="Add at least one keyword")
    project = db.fetch_one("select gl, device from projects where id = %s", (project_id,))
    manual_volume = int(search_volume) if search_volume.strip().isdigit() else None
    volume_map = {}
    volume_provider = None
    if manual_volume is None and project:
        volume_map, volume_provider = fetch_keyword_volumes(unique_phrases, project["gl"], project["device"])
    with db.connect() as conn:
        with conn.transaction():
            for phrase in unique_phrases:
                phrase_volume = manual_volume if manual_volume is not None else volume_map.get(phrase.lower())
                conn.execute(
                    """
                    insert into keywords (project_id, phrase, tags, search_volume)
                    values (%s, %s, %s, %s)
                    on conflict (project_id, phrase) do update
                    set active = true, tags = excluded.tags, search_volume = coalesce(excluded.search_volume, keywords.search_volume)
                    """,
                    (project_id, phrase, tag_list, phrase_volume),
                )
    label = "keyword" if len(unique_phrases) == 1 else "keywords"
    volume_message = f" with {len(volume_map)} {volume_provider} volumes" if volume_map and volume_provider else ""
    return redirect_to(f"/projects/{project_id}", message=f"Added {len(unique_phrases)} {label}{volume_message}")


@app.post("/projects/{project_id}/search-volumes")
def refresh_search_volumes(request: Request, project_id: str, csrf_token_value: str = Form(..., alias="csrf_token")):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    updated, provider = update_project_search_volumes(project_id)
    provider_label = f" from {provider}" if provider else ""
    return redirect_to(f"/projects/{project_id}", message=f"Updated {updated} search volumes{provider_label}")


@app.post("/projects/{project_id}/notes")
def add_note(request: Request, project_id: str, csrf_token_value: str = Form(..., alias="csrf_token"), note: str = Form(...)):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    db.execute("insert into project_notes (project_id, note) values (%s, %s) returning id", (project_id, note.strip()))
    return redirect_to(f"/projects/{project_id}", message="Note added")


@app.post("/keywords/{keyword_id}/delete")
def delete_keyword(request: Request, keyword_id: str, csrf_token_value: str = Form(..., alias="csrf_token"), project_id: str = Form(...)):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    db.execute("delete from keywords where id = %s returning id", (keyword_id,))
    return redirect_to(f"/projects/{project_id}", message="Keyword deleted")


@app.post("/keywords/{keyword_id}/check")
def check_keyword(request: Request, keyword_id: str, csrf_token_value: str = Form(..., alias="csrf_token"), project_id: str = Form(...)):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    check_id = run_keyword_check(keyword_id)
    return redirect_to(f"/projects/{project_id}", message="Keyword checked", serp_check_id=check_id)


@app.post("/projects/{project_id}/check-all")
def check_all_keywords(request: Request, project_id: str, csrf_token_value: str = Form(..., alias="csrf_token")):
    user = require_user(request, "manager")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    keywords = db.fetch_all("select id from keywords where project_id = %s and active = true order by created_at asc", (project_id,))
    last_check_id = None
    for keyword in keywords:
        last_check_id = run_keyword_check(str(keyword["id"]))
    return redirect_to(f"/projects/{project_id}", message=f"Checked {len(keywords)} keywords", serp_check_id=last_check_id)


@app.get("/keywords", response_class=HTMLResponse)
def keywords_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return RedirectResponse("/", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return RedirectResponse("/", status_code=303)


@app.get("/reports/export.csv")
def export_csv(request: Request, project_id: str, date_from: date | None = None, date_to: date | None = None):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    rows = db.fetch_all(
        """
        with latest as (
          select distinct on (keyword_id) keyword_id, position, previous_position, change, matched_url, checked_at
          from rank_checks
          where (%s::date is null or checked_at::date >= %s::date)
            and (%s::date is null or checked_at::date <= %s::date)
          order by keyword_id, checked_at desc
        ),
        bests as (
          select keyword_id, min(position) as best_position from rank_checks where position is not null group by keyword_id
        ),
        firsts as (
          select distinct on (keyword_id) keyword_id, position as first_position
          from rank_checks where position is not null order by keyword_id, checked_at asc
        )
        select p.name as project, p.domain, k.phrase, latest.position, latest.previous_position,
          latest.change, bests.best_position, firsts.first_position, k.search_volume,
          latest.checked_at, latest.matched_url
        from keywords k
        join projects p on p.id = k.project_id
        left join latest on latest.keyword_id = k.id
        left join bests on bests.keyword_id = k.id
        left join firsts on firsts.keyword_id = k.id
        where p.id = %s
        order by k.phrase
        """,
        (date_from, date_from, date_to, date_to, project_id),
    )
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "project",
            "domain",
            "phrase",
            "position",
            "previous_position",
            "change",
            "best_position",
            "first_position",
            "search_volume",
            "checked_at",
            "matched_url",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rank-report.csv"},
    )


@app.get("/competitors", response_class=HTMLResponse)
def competitors_page(request: Request):
    rows = db.fetch_all("select id, name, domain, competitors from projects order by name")
    return render(request, "competitors.html", {"active_nav": "competitors", "projects": rows})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, message: str | None = None):
    return render(request, "settings.html", {"active_nav": "settings", "message": message})


@app.post("/settings/password")
def change_password(
    request: Request,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    if not verify_password(current_password, user["password_hash"]):
        return redirect_to("/settings", message="Current password is incorrect")
    if len(new_password) < 8 or new_password != confirm_password:
        return redirect_to("/settings", message="New passwords must match and be at least 8 characters")
    db.execute("update users set password_hash = %s, updated_at = now() where id = %s returning id", (hash_password(new_password), user["id"]))
    return redirect_to("/settings", message="Password changed")


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, message: str | None = None):
    user = require_user(request, "admin")
    if isinstance(user, RedirectResponse):
        return user
    rows = db.fetch_all("select id, email, name, role, active, created_at, last_login_at from users order by created_at desc")
    return render(request, "users.html", {"active_nav": "users", "users": rows, "message": message})


@app.post("/users")
def create_user(
    request: Request,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    email: str = Form(...),
    name: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
):
    user = require_user(request, "admin")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    if role not in {"admin", "manager", "viewer"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(password) < 8:
        return redirect_to("/users", message="Password must be at least 8 characters")
    db.execute(
        """
        insert into users (email, name, role, password_hash)
        values (%s, %s, %s, %s)
        returning id
        """,
        (email.strip().lower(), name.strip(), role, hash_password(password)),
    )
    return redirect_to("/users", message="User created")


@app.post("/users/{user_id}/edit")
def edit_user(
    request: Request,
    user_id: str,
    csrf_token_value: str = Form(..., alias="csrf_token"),
    email: str = Form(...),
    name: str = Form(...),
    role: str = Form(...),
    active: str = Form("false"),
    password: str = Form(""),
):
    user = require_user(request, "admin")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    if role not in {"admin", "manager", "viewer"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    is_active = active == "true"
    if password:
        db.execute(
            """
            update users set email = %s, name = %s, role = %s, active = %s, password_hash = %s, updated_at = now()
            where id = %s returning id
            """,
            (email.strip().lower(), name.strip(), role, is_active, hash_password(password), user_id),
        )
    else:
        db.execute(
            """
            update users set email = %s, name = %s, role = %s, active = %s, updated_at = now()
            where id = %s returning id
            """,
            (email.strip().lower(), name.strip(), role, is_active, user_id),
        )
    return redirect_to("/users", message="User updated")


@app.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: str, csrf_token_value: str = Form(..., alias="csrf_token")):
    user = require_user(request, "admin")
    if isinstance(user, RedirectResponse):
        return user
    verify_csrf(request, csrf_token_value)
    if str(user["id"]) == user_id:
        return redirect_to("/users", message="You cannot delete your own account")
    db.execute("delete from users where id = %s returning id", (user_id,))
    return redirect_to("/users", message="User deleted")


def clean_lines(value: str) -> list[str]:
    return [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]


def project_options() -> list[dict[str, Any]]:
    return db.fetch_all("select id, name, domain from projects order by name")


def project_cards() -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        select p.*,
          count(k.id)::int as keyword_count,
          count(k.id) filter (where k.active)::int as active_keyword_count,
          count(k.id) filter (where latest.checked_at is not null)::int as checked_count,
          count(k.id) filter (where latest.checked_at is null)::int as unchecked_count,
          count(k.id) filter (where latest.position is not null)::int as positioned_count,
          round(avg(latest.position)::numeric, 2)::float as average_position,
          count(k.id) filter (where latest.position <= 3)::int as top3_count,
          count(k.id) filter (where latest.position <= 10)::int as top10_count,
          count(k.id) filter (where latest.position <= 20)::int as top20_count,
          count(k.id) filter (where latest.checked_at is not null and latest.position is null)::int as not_found_count,
          count(k.id) filter (where latest.checked_at is not null and (latest.position is null or latest.position > 20))::int as outside_top20_count,
          max(latest.checked_at) as latest_checked_at
        from projects p
        left join keywords k on k.project_id = p.id
        left join lateral (
          select rc.position, rc.checked_at from rank_checks rc where rc.keyword_id = k.id order by rc.checked_at desc limit 1
        ) latest on true
        group by p.id
        order by p.created_at desc
        """
    )


def select_project(projects: list[dict[str, Any]], project_id: str | None) -> dict[str, Any] | None:
    if not projects:
        return None
    return next((project for project in projects if str(project["id"]) == str(project_id)), projects[0])


def load_project_dashboard(
    project_id: str,
    q: str = "",
    page: int = 1,
    per_page: int = 25,
    limit: int | None = None,
    check_range: str = "last_check",
    serp_check_id: str | None = None,
) -> dict[str, Any]:
    project = db.fetch_one("select * from projects where id = %s", (project_id,))
    pattern = f"%{q.strip()}%"
    cutoff = range_cutoff(check_range)
    total = db.fetch_one(
        """
        select count(*)::int as count
        from keywords
        where project_id = %s and (%s = '' or phrase ilike %s)
        """,
        (project_id, q.strip(), pattern),
    )["count"]
    fetch_limit = limit or per_page
    offset = 0 if limit else (page - 1) * per_page
    keywords = db.fetch_all(keyword_summary_sql() + " limit %s offset %s", (cutoff, cutoff, project_id, q.strip(), pattern, fetch_limit, offset))
    all_keywords = db.fetch_all(keyword_summary_sql(), (cutoff, cutoff, project_id, "", "%%"))
    history = project_history(project_id, None, None)
    notes = db.fetch_all("select * from project_notes where project_id = %s order by created_at desc limit 20", (project_id,))
    serp_results = []
    serp_keyword = None
    if serp_check_id:
        serp_results = db.fetch_all("select * from serp_results where check_id = %s order by position asc limit 20", (serp_check_id,))
        serp_keyword = db.fetch_one(
            """
            select k.phrase
            from rank_checks rc
            join keywords k on k.id = rc.keyword_id
            where rc.id = %s
            """,
            (serp_check_id,),
        )
    pages = max(math.ceil(total / per_page), 1)
    return {
        "project": project,
        "keywords": keywords,
        "metrics": build_metrics(all_keywords),
        "history": history,
        "notes": notes,
        "serp_results": serp_results,
        "serp_keyword": serp_keyword,
        "total_keywords": total,
        "pages": pages,
    }


def keyword_summary_sql() -> str:
    return """
    with latest as (
      select distinct on (keyword_id)
        keyword_id, id as check_id, position, previous_position, change, matched_url, result_count, checked_at
      from rank_checks
      where (%s::timestamptz is null or checked_at >= %s::timestamptz)
      order by keyword_id, checked_at desc
    ),
    bests as (
      select keyword_id, min(position) as best_position
      from rank_checks
      where position is not null
      group by keyword_id
    ),
    firsts as (
      select distinct on (keyword_id) keyword_id, position as first_position
      from rank_checks
      where position is not null
      order by keyword_id, checked_at asc
    )
    select k.*, latest.check_id, latest.position, latest.previous_position, latest.change,
      latest.matched_url, latest.result_count, latest.checked_at, bests.best_position, firsts.first_position
    from keywords k
    left join latest on latest.keyword_id = k.id
    left join bests on bests.keyword_id = k.id
    left join firsts on firsts.keyword_id = k.id
    where k.project_id = %s and (%s = '' or k.phrase ilike %s)
    order by k.created_at asc
    """


def project_history(project_id: str, date_from: date | None, date_to: date | None) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        select date_trunc('day', rc.checked_at) as day,
          round(avg(coalesce(rc.position, 101))::numeric, 2)::float as average_position,
          count(*)::int as checks
        from rank_checks rc
        join keywords k on k.id = rc.keyword_id
        where k.project_id = %s
          and (%s::date is null or rc.checked_at::date >= %s::date)
          and (%s::date is null or rc.checked_at::date <= %s::date)
        group by 1
        order by 1 asc
        """,
        (project_id, date_from, date_from, date_to, date_to),
    )


def run_keyword_check(keyword_id: str) -> str:
    keyword = db.fetch_one(
        """
        select k.*, p.domain, p.location, p.gl, p.hl
        from keywords k
        join projects p on p.id = k.project_id
        where k.id = %s
        """,
        (keyword_id,),
    )
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")
    previous = db.fetch_one("select position from rank_checks where keyword_id = %s order by checked_at desc limit 1", (keyword_id,))
    previous_position = previous["position"] if previous else None
    rank = check_keyword_rank(keyword)
    change = previous_position - rank["position"] if previous_position and rank["position"] else None
    with db.connect() as conn:
        with conn.transaction():
            check = conn.execute(
                """
                insert into rank_checks (keyword_id, position, matched_url, previous_position, change, result_count, raw_response)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    keyword_id,
                    rank["position"],
                    rank["matched_url"],
                    previous_position,
                    change,
                    rank["result_count"],
                    Jsonb(rank["raw"]),
                ),
            ).fetchone()
            for item in rank["organic"]:
                conn.execute(
                    """
                    insert into serp_results (check_id, position, title, link, display_link, snippet)
                    values (%s, %s, %s, %s, %s, %s)
                    """,
                    (check["id"], item["position"], item["title"], item["link"], item["display_link"], item["snippet"]),
                )
    return str(check["id"])


def update_project_search_volumes(project_id: str) -> tuple[int, str | None]:
    project = db.fetch_one("select gl, device from projects where id = %s", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    keywords = db.fetch_all("select id, phrase from keywords where project_id = %s and active = true order by created_at asc", (project_id,))
    volume_map, provider = fetch_keyword_volumes([keyword["phrase"] for keyword in keywords], project["gl"], project["device"])
    if not volume_map:
        return 0, None
    updated = 0
    with db.connect() as conn:
        with conn.transaction():
            for keyword in keywords:
                volume = volume_map.get(keyword["phrase"].lower())
                if volume is None:
                    continue
                conn.execute("update keywords set search_volume = %s where id = %s", (volume, keyword["id"]))
                updated += 1
    return updated, provider


def empty_metrics() -> dict[str, Any]:
    return {
        "total": 0,
        "checked": 0,
        "average_position": None,
        "improved": 0,
        "declined": 0,
        "top3": 0,
        "top10": 0,
        "top20": 0,
        "outside_top20": 0,
    }


def build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if row["active"]]
    positions = [row["position"] for row in active if isinstance(row["position"], int)]
    metrics = empty_metrics()
    metrics["total"] = len(active)
    metrics["checked"] = len(positions)
    metrics["average_position"] = round(sum(positions) / len(positions), 2) if positions else None
    metrics["improved"] = len([row for row in active if row["change"] and row["change"] > 0])
    metrics["declined"] = len([row for row in active if row["change"] and row["change"] < 0])
    metrics["top3"] = len([position for position in positions if position <= 3])
    metrics["top10"] = len([position for position in positions if position <= 10])
    metrics["top20"] = len([position for position in positions if position <= 20])
    metrics["outside_top20"] = len([row for row in active if not isinstance(row["position"], int) or row["position"] > 20])
    return metrics


def range_cutoff(check_range: str) -> datetime | None:
    ranges = {
        "last_24_hours": timedelta(hours=24),
        "last_7_days": timedelta(days=7),
        "last_30_days": timedelta(days=30),
        "last_60_days": timedelta(days=60),
        "last_3_months": timedelta(days=90),
        "last_6_months": timedelta(days=180),
        "last_12_months": timedelta(days=365),
    }
    delta = ranges.get(check_range)
    if not delta:
        return None
    return datetime.now(timezone.utc) - delta
