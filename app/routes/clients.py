from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional, List
from datetime import date, timedelta

from app.database.db import get_db

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_review_count() -> int:
    db = get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()
    db.close()
    return row["cnt"]


def get_all_tags(db=None):
    close = False
    if db is None:
        db = get_db()
        close = True
    tags = db.execute("SELECT * FROM dietary_tags ORDER BY display_name").fetchall()
    if close:
        db.close()
    return tags


# ───────────────────────────────────────
# Client Management
# ───────────────────────────────────────

@router.get("/clients", response_class=HTMLResponse)
async def client_list(request: Request):
    db = get_db()
    clients = db.execute("""
        SELECT c.*,
               COUNT(md.id) as meal_count,
               MAX(md.delivery_date) as last_delivery,
               GROUP_CONCAT(dt.display_name, '||') as tag_names,
               GROUP_CONCAT(dt.color, '||') as tag_colors
        FROM clients c
        LEFT JOIN meal_deliveries md ON c.id = md.client_id
        LEFT JOIN client_tags ct ON c.id = ct.client_id
        LEFT JOIN dietary_tags dt ON ct.tag_id = dt.id
        WHERE c.status = 'active'
        GROUP BY c.id
        ORDER BY c.last_name, c.first_name
    """).fetchall()
    review_count = get_review_count()
    db.close()

    return templates.TemplateResponse("clients/list.html", {
        "request": request,
        "clients": clients,
        "client_count": len(clients),
        "review_count": review_count,
    })


@router.get("/clients/add", response_class=HTMLResponse)
async def add_client_form(request: Request):
    db = get_db()
    all_tags = get_all_tags(db)
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("clients/form.html", {
        "request": request,
        "client": None,
        "all_tags": all_tags,
        "selected_tag_ids": [],
        "review_count": review_count,
    })


@router.post("/clients/add")
async def add_client(request: Request):
    form = await request.form()
    db = get_db()
    try:
        cursor = db.execute("""
            INSERT INTO clients (first_name, last_name, dietary_notes)
            VALUES (?, ?, ?)
        """, (
            form.get("first_name", "").strip(),
            form.get("last_name", "").strip(),
            form.get("dietary_notes", "").strip() or None,
        ))
        client_id = cursor.lastrowid

        tag_ids = form.getlist("tags")
        for tag_id in tag_ids:
            db.execute("INSERT INTO client_tags (client_id, tag_id) VALUES (?, ?)",
                       (client_id, int(tag_id)))
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ? AND status = 'active'", (client_id,)).fetchone()
    if not client:
        db.close()
        return RedirectResponse(url="/clients", status_code=302)

    tags = db.execute("""
        SELECT dt.* FROM dietary_tags dt
        JOIN client_tags ct ON dt.id = ct.tag_id
        WHERE ct.client_id = ?
        ORDER BY dt.display_name
    """, (client_id,)).fetchall()

    deliveries = db.execute("""
        SELECT md.*, r.title as recipe_title, r.id as recipe_id,
               GROUP_CONCAT(dt.display_name, '||') as recipe_tags,
               GROUP_CONCAT(dt.color, '||') as recipe_tag_colors
        FROM meal_deliveries md
        JOIN recipes r ON md.recipe_id = r.id
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE md.client_id = ?
        GROUP BY md.id
        ORDER BY md.delivery_date DESC, md.created_at DESC
    """, (client_id,)).fetchall()

    # Group deliveries by month
    months = {}
    for d in deliveries:
        month_key = d["delivery_date"][:7]  # "2026-03"
        if month_key not in months:
            months[month_key] = []
        months[month_key].append(d)

    total_meals = len(deliveries)
    first_delivery = deliveries[-1]["delivery_date"] if deliveries else None

    review_count = get_review_count()
    db.close()

    return templates.TemplateResponse("clients/detail.html", {
        "request": request,
        "client": client,
        "tags": tags,
        "months": months,
        "total_meals": total_meals,
        "first_delivery": first_delivery,
        "review_count": review_count,
    })


@router.get("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_form(request: Request, client_id: int):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        db.close()
        return RedirectResponse(url="/clients", status_code=302)

    all_tags = get_all_tags(db)
    selected_tag_ids = [row["tag_id"] for row in
                        db.execute("SELECT tag_id FROM client_tags WHERE client_id = ?", (client_id,)).fetchall()]
    review_count = get_review_count()
    db.close()

    return templates.TemplateResponse("clients/form.html", {
        "request": request,
        "client": client,
        "all_tags": all_tags,
        "selected_tag_ids": selected_tag_ids,
        "review_count": review_count,
    })


@router.post("/clients/{client_id}/edit")
async def edit_client(request: Request, client_id: int):
    form = await request.form()
    db = get_db()
    try:
        db.execute("""
            UPDATE clients SET first_name = ?, last_name = ?, dietary_notes = ?
            WHERE id = ?
        """, (
            form.get("first_name", "").strip(),
            form.get("last_name", "").strip(),
            form.get("dietary_notes", "").strip() or None,
            client_id,
        ))

        db.execute("DELETE FROM client_tags WHERE client_id = ?", (client_id,))
        tag_ids = form.getlist("tags")
        for tag_id in tag_ids:
            db.execute("INSERT INTO client_tags (client_id, tag_id) VALUES (?, ?)",
                       (client_id, int(tag_id)))
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/delete")
async def delete_client(request: Request, client_id: int):
    db = get_db()
    db.execute("UPDATE clients SET status = 'inactive' WHERE id = ?", (client_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/clients", status_code=303)


# ───────────────────────────────────────
# Meal Delivery Logging
# ───────────────────────────────────────

@router.get("/clients/{client_id}/log", response_class=HTMLResponse)
async def log_meal_form(request: Request, client_id: int, recipe_id: Optional[int] = None, servings: Optional[int] = None):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ? AND status = 'active'", (client_id,)).fetchone()
    if not client:
        db.close()
        return RedirectResponse(url="/clients", status_code=302)

    recipes = db.execute("""
        SELECT id, title FROM recipes WHERE status = 'approved'
        ORDER BY title
    """).fetchall()

    review_count = get_review_count()
    db.close()

    return templates.TemplateResponse("clients/log.html", {
        "request": request,
        "client": client,
        "recipes": recipes,
        "review_count": review_count,
        "prefill_recipe_id": recipe_id,
        "prefill_servings": servings or 1,
        "today": date.today().isoformat(),
    })


@router.post("/clients/{client_id}/log")
async def log_meal_save(request: Request, client_id: int):
    form = await request.form()
    db = get_db()

    try:
        delivery_date = form.get("delivery_date", date.today().isoformat())
        recipe_ids = form.getlist("recipe_id")
        serving_list = form.getlist("servings")
        note_list = form.getlist("delivery_notes")

        for i, rid in enumerate(recipe_ids):
            if not rid:
                continue
            s = 1
            if i < len(serving_list) and serving_list[i]:
                try:
                    s = int(serving_list[i])
                except ValueError:
                    s = 1
            n = note_list[i].strip() if i < len(note_list) and note_list[i].strip() else None

            db.execute("""
                INSERT INTO meal_deliveries (client_id, recipe_id, delivery_date, servings, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (client_id, int(rid), delivery_date, s, n))

        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.get("/log", response_class=HTMLResponse)
async def quick_log(request: Request, recipe_id: Optional[int] = None, servings: Optional[int] = None):
    db = get_db()
    clients = db.execute("""
        SELECT * FROM clients WHERE status = 'active'
        ORDER BY last_name, first_name
    """).fetchall()

    recipes = db.execute("""
        SELECT id, title FROM recipes WHERE status = 'approved'
        ORDER BY title
    """).fetchall()

    review_count = get_review_count()
    db.close()

    return templates.TemplateResponse("clients/quick_log.html", {
        "request": request,
        "clients": clients,
        "recipes": recipes,
        "review_count": review_count,
        "prefill_recipe_id": recipe_id,
        "prefill_servings": servings or 1,
        "today": date.today().isoformat(),
    })


@router.post("/log")
async def quick_log_save(request: Request):
    form = await request.form()
    client_id = form.get("client_id")
    if not client_id:
        return RedirectResponse(url="/log", status_code=303)

    db = get_db()
    try:
        delivery_date = form.get("delivery_date", date.today().isoformat())
        recipe_ids = form.getlist("recipe_id")
        serving_list = form.getlist("servings")
        note_list = form.getlist("delivery_notes")

        for i, rid in enumerate(recipe_ids):
            if not rid:
                continue
            s = 1
            if i < len(serving_list) and serving_list[i]:
                try:
                    s = int(serving_list[i])
                except ValueError:
                    s = 1
            n = note_list[i].strip() if i < len(note_list) and note_list[i].strip() else None

            db.execute("""
                INSERT INTO meal_deliveries (client_id, recipe_id, delivery_date, servings, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (int(client_id), int(rid), delivery_date, s, n))

        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/deliveries/{delivery_id}/delete")
async def delete_delivery(request: Request, delivery_id: int):
    db = get_db()
    row = db.execute("SELECT client_id FROM meal_deliveries WHERE id = ?", (delivery_id,)).fetchone()
    client_id = row["client_id"] if row else None
    db.execute("DELETE FROM meal_deliveries WHERE id = ?", (delivery_id,))
    db.commit()
    db.close()

    if client_id:
        return RedirectResponse(url=f"/clients/{client_id}", status_code=303)
    return RedirectResponse(url="/clients", status_code=303)


# ───────────────────────────────────────
# Reports
# ───────────────────────────────────────

@router.get("/clients/{client_id}/report", response_class=HTMLResponse)
async def client_report(request: Request, client_id: int):
    # Redirect to detail page — report view is the same
    return RedirectResponse(url=f"/clients/{client_id}", status_code=302)


@router.get("/clients/{client_id}/report/print", response_class=HTMLResponse)
async def client_report_print(request: Request, client_id: int,
                                from_date: Optional[str] = None, to_date: Optional[str] = None):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        db.close()
        return RedirectResponse(url="/clients", status_code=302)

    tags = db.execute("""
        SELECT dt.* FROM dietary_tags dt
        JOIN client_tags ct ON dt.id = ct.tag_id
        WHERE ct.client_id = ?
        ORDER BY dt.display_name
    """, (client_id,)).fetchall()

    # Default: last 30 days
    if not to_date:
        to_date = date.today().isoformat()
    if not from_date:
        from_date = (date.today() - timedelta(days=30)).isoformat()

    deliveries = db.execute("""
        SELECT md.*, r.title as recipe_title,
               GROUP_CONCAT(dt.display_name, '||') as recipe_tags
        FROM meal_deliveries md
        JOIN recipes r ON md.recipe_id = r.id
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE md.client_id = ?
          AND md.delivery_date BETWEEN ? AND ?
        GROUP BY md.id
        ORDER BY md.delivery_date DESC, md.created_at DESC
    """, (client_id, from_date, to_date)).fetchall()

    # Group by date
    dates = {}
    for d in deliveries:
        dk = d["delivery_date"]
        if dk not in dates:
            dates[dk] = []
        dates[dk].append(d)

    # Dietary category summary
    tag_summary = db.execute("""
        SELECT dt.display_name, COUNT(DISTINCT md.id) as meal_count
        FROM meal_deliveries md
        JOIN recipes r ON md.recipe_id = r.id
        JOIN recipe_tags rt ON r.id = rt.recipe_id
        JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE md.client_id = ?
          AND md.delivery_date BETWEEN ? AND ?
        GROUP BY dt.id
        ORDER BY meal_count DESC
    """, (client_id, from_date, to_date)).fetchall()

    settings = {}
    for row in db.execute("SELECT key, value FROM settings").fetchall():
        settings[row["key"]] = row["value"]

    db.close()

    return templates.TemplateResponse("clients/report_print.html", {
        "request": request,
        "client": client,
        "tags": tags,
        "dates": dates,
        "tag_summary": tag_summary,
        "total_meals": len(deliveries),
        "visit_count": len(dates),
        "from_date": from_date,
        "to_date": to_date,
        "settings": settings,
    })
