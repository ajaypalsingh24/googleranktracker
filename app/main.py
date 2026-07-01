from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Jsonb
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.serper import check_keyword_rank

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.ensure_schema()
    yield


app = FastAPI(title="Google Rank Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"),
    same_site="lax",
    https_only=os.getenv("RENDER") == "true",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def is_logged_in(request: Request) -> bool:
    return not os.getenv("APP_PASSWORD") or request.session.get("logged_in") is True


def require_login(request: Request) -> RedirectResponse | None:
    if is_logged_in(request):
        return None
    return RedirectResponse("/login", status_code=303)


def redirect_home(project_id: str | None = None, message: str | None = None, serp_check_id: str | None = None) -> RedirectResponse:
    parts = []
    if project_id:
        parts.append(f"project_id={project_id}")
    if message:
        parts.append(f"message={message}")
    if serp_check_id:
        parts.append(f"serp_check_id={serp_check_id}")
    query = f"?{'&'.join(parts)}" if parts else ""
    return RedirectResponse(f"/{query}", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if password != os.getenv("APP_PASSWORD"):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"}, status_code=401)
    request.session["logged_in"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, project_id: str | None = None, message: str | None = None, serp_check_id: str | None = None):
    if redirect := require_login(request):
        return redirect

    projects = db.fetch_all(
        """
        select p.*,
          count(k.id)::int as keyword_count,
          count(k.id) filter (where k.active)::int as active_keyword_count
        from projects p
        left join keywords k on k.project_id = p.id
        group by p.id
        order by p.created_at desc
        """
    )
    current_project = None
    if projects:
        current_project = next((project for project in projects if str(project["id"]) == str(project_id)), projects[0])

    context: dict[str, Any] = {
        "request": request,
        "projects": projects,
        "project": current_project,
        "keywords": [],
        "metrics": empty_metrics(),
        "history": [],
        "notes": [],
        "serp_results": [],
        "serp_keyword": None,
        "message": message,
    }

    if current_project:
        context.update(load_project_dashboard(str(current_project["id"]), serp_check_id))

    return templates.TemplateResponse("dashboard.html", context)


@app.post("/projects")
def create_project(
    request: Request,
    name: str = Form(...),
    domain: str = Form(...),
    location: str = Form("India"),
    gl: str = Form("in"),
    hl: str = Form("en"),
):
    if redirect := require_login(request):
        return redirect
    project = db.execute(
        """
        insert into projects (name, domain, location, gl, hl)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (name.strip(), domain.strip(), location.strip(), gl.strip().lower(), hl.strip().lower()),
    )
    return redirect_home(str(project["id"]), "Project added")


@app.post("/projects/{project_id}/keywords")
def create_keyword(request: Request, project_id: str, phrases: str = Form(...), tags: str = Form("")):
    if redirect := require_login(request):
        return redirect
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    keyword_lines = [line.strip() for line in phrases.replace(",", "\n").splitlines() if line.strip()]
    unique_phrases = list(dict.fromkeys(keyword_lines))
    if not unique_phrases:
        return redirect_home(project_id, "Add at least one keyword")

    with db.connect() as conn:
        with conn.transaction():
            for phrase in unique_phrases:
                conn.execute(
                    """
                    insert into keywords (project_id, phrase, tags)
                    values (%s, %s, %s)
                    on conflict (project_id, phrase) do update set active = true, tags = excluded.tags
                    """,
                    (project_id, phrase, tag_list),
                )
    label = "keyword" if len(unique_phrases) == 1 else "keywords"
    return redirect_home(project_id, f"Added {len(unique_phrases)} {label}")


@app.post("/projects/{project_id}/notes")
def add_note(request: Request, project_id: str, note: str = Form(...)):
    if redirect := require_login(request):
        return redirect
    db.execute("insert into project_notes (project_id, note) values (%s, %s) returning id", (project_id, note.strip()))
    return redirect_home(project_id, "Note added")


@app.post("/keywords/{keyword_id}/delete")
def delete_keyword(request: Request, keyword_id: str, project_id: str = Form(...)):
    if redirect := require_login(request):
        return redirect
    db.execute("delete from keywords where id = %s returning id", (keyword_id,))
    return redirect_home(project_id, "Keyword deleted")


@app.post("/keywords/{keyword_id}/check")
def check_keyword(request: Request, keyword_id: str, project_id: str = Form(...)):
    if redirect := require_login(request):
        return redirect
    check_id = run_keyword_check(keyword_id)
    return redirect_home(project_id, "Keyword checked", check_id)


@app.post("/projects/{project_id}/check-all")
def check_all_keywords(request: Request, project_id: str):
    if redirect := require_login(request):
        return redirect
    keywords = db.fetch_all("select id from keywords where project_id = %s and active = true order by created_at asc", (project_id,))
    last_check_id = None
    for keyword in keywords:
        last_check_id = run_keyword_check(str(keyword["id"]))
    return redirect_home(project_id, f"Checked {len(keywords)} keywords", last_check_id)


def load_project_dashboard(project_id: str, serp_check_id: str | None = None) -> dict[str, Any]:
    project = db.fetch_one("select * from projects where id = %s", (project_id,))
    keywords = db.fetch_all(
        """
        with latest as (
          select distinct on (keyword_id)
            keyword_id, id as check_id, position, previous_position, change, matched_url, result_count, checked_at
          from rank_checks
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
        where k.project_id = %s
        order by k.created_at asc
        """,
        (project_id,),
    )
    history = db.fetch_all(
        """
        select date_trunc('day', rc.checked_at) as day,
          round(avg(coalesce(rc.position, 101))::numeric, 2)::float as average_position,
          count(*)::int as checks
        from rank_checks rc
        join keywords k on k.id = rc.keyword_id
        where k.project_id = %s
        group by 1
        order by 1 asc
        """,
        (project_id,),
    )
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

    return {
        "project": project,
        "keywords": keywords,
        "metrics": build_metrics(keywords),
        "history": history,
        "notes": notes,
        "serp_results": serp_results,
        "serp_keyword": serp_keyword,
    }


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


def empty_metrics() -> dict[str, Any]:
    return {
        "total": 0,
        "checked": 0,
        "average_position": None,
        "improved": 0,
        "top3": 0,
        "top10": 0,
        "top30": 0,
        "top100": 0,
        "not_found": 0,
    }


def build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if row["active"]]
    positions = [row["position"] for row in active if isinstance(row["position"], int)]
    metrics = empty_metrics()
    metrics["total"] = len(active)
    metrics["checked"] = len(positions)
    metrics["average_position"] = round(sum(positions) / len(positions), 2) if positions else None
    metrics["improved"] = len([row for row in active if row["change"] and row["change"] > 0])
    metrics["top3"] = len([position for position in positions if position <= 3])
    metrics["top10"] = len([position for position in positions if position <= 10])
    metrics["top30"] = len([position for position in positions if position <= 30])
    metrics["top100"] = len([position for position in positions if position <= 100])
    metrics["not_found"] = len(active) - len(positions)
    return metrics
