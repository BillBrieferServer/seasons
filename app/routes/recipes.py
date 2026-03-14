from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from app.database.db import get_db
from app.config import AISLE_CATEGORIES

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


@router.get("/recipes", response_class=HTMLResponse)
async def recipe_library(request: Request, q: str = "", tag: str = ""):
    db = get_db()
    all_tags = get_all_tags(db)

    query = """
        SELECT r.*, GROUP_CONCAT(dt.display_name, '||') as tag_names,
               GROUP_CONCAT(dt.color, '||') as tag_colors
        FROM recipes r
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE r.status = 'approved'
    """
    params = []

    if q:
        query += " AND (r.title LIKE ? OR r.description LIKE ?)"
        params.extend(["%" + q + "%", "%" + q + "%"])

    if tag:
        query += " AND r.id IN (SELECT rt2.recipe_id FROM recipe_tags rt2 JOIN dietary_tags dt2 ON rt2.tag_id = dt2.id WHERE dt2.name = ?)"
        params.append(tag)

    query += " GROUP BY r.id ORDER BY r.title"
    recipes = db.execute(query, params).fetchall()

    tag_counts = db.execute("""
        SELECT dt.name, dt.display_name, dt.color, COUNT(rt.recipe_id) as cnt
        FROM dietary_tags dt
        LEFT JOIN recipe_tags rt ON dt.id = rt.tag_id
        LEFT JOIN recipes r ON rt.recipe_id = r.id AND r.status = 'approved'
        GROUP BY dt.id
        ORDER BY dt.display_name
    """).fetchall()

    total_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='approved'").fetchone()["cnt"]
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("recipes/list.html", {
        "request": request,
        "recipes": recipes,
        "all_tags": all_tags,
        "tag_counts": tag_counts,
        "total_count": total_count,
        "review_count": review_count,
        "search_query": q,
        "active_tag": tag,
    })


@router.get("/recipes/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    db = get_db()
    recipes = db.execute("""
        SELECT r.*, GROUP_CONCAT(dt.display_name, '||') as tag_names,
               GROUP_CONCAT(dt.color, '||') as tag_colors
        FROM recipes r
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE r.status = 'review'
        GROUP BY r.id
        ORDER BY r.created_at DESC
    """).fetchall()
    review_count = len(recipes)
    db.close()

    return templates.TemplateResponse("recipes/review.html", {
        "request": request,
        "recipes": recipes,
        "review_count": review_count,
    })


@router.get("/recipes/add", response_class=HTMLResponse)
async def add_recipe_form(request: Request):
    db = get_db()
    all_tags = get_all_tags(db)
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("recipes/form.html", {
        "request": request,
        "recipe": None,
        "all_tags": all_tags,
        "aisle_categories": AISLE_CATEGORIES,
        "review_count": review_count,
        "selected_tag_ids": [],
        "ingredients": [],
        "steps": [],
    })


@router.post("/recipes/add")
async def add_recipe(request: Request):
    form = await request.form()
    db = get_db()

    try:
        cursor = db.execute("""
            INSERT INTO recipes (title, description, base_servings, prep_time_minutes,
                                 cook_time_minutes, source, source_url, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            form.get("title", "").strip(),
            form.get("description", "").strip() or None,
            int(form.get("base_servings", 4)),
            int(form.get("prep_time_minutes")) if form.get("prep_time_minutes") else None,
            int(form.get("cook_time_minutes")) if form.get("cook_time_minutes") else None,
            form.get("source", "").strip() or None,
            form.get("source_url", "").strip() or None,
            form.get("status", "review"),
            form.get("notes", "").strip() or None,
        ))
        recipe_id = cursor.lastrowid

        tag_ids = form.getlist("tags")
        for tag_id in tag_ids:
            db.execute("INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                       (recipe_id, int(tag_id)))

        ing_names = form.getlist("ing_name")
        ing_amounts = form.getlist("ing_amount")
        ing_units = form.getlist("ing_unit")
        ing_aisles = form.getlist("ing_aisle")
        ing_notes = form.getlist("ing_notes")

        for i, name in enumerate(ing_names):
            if not name.strip():
                continue
            amount = None
            if i < len(ing_amounts) and ing_amounts[i].strip():
                try:
                    amount = float(ing_amounts[i])
                except ValueError:
                    pass
            db.execute("""
                INSERT INTO recipe_ingredients (recipe_id, name, amount, unit, aisle_category, sort_order, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                recipe_id,
                name.strip(),
                amount,
                ing_units[i].strip() if i < len(ing_units) and ing_units[i].strip() else None,
                ing_aisles[i] if i < len(ing_aisles) else "other",
                i,
                ing_notes[i].strip() if i < len(ing_notes) and ing_notes[i].strip() else None,
            ))

        step_instructions = form.getlist("step_instruction")
        for i, instruction in enumerate(step_instructions):
            if not instruction.strip():
                continue
            db.execute("""
                INSERT INTO recipe_steps (recipe_id, step_number, instruction)
                VALUES (?, ?, ?)
            """, (recipe_id, i + 1, instruction.strip()))

        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/recipes/" + str(recipe_id), status_code=303)


@router.get("/recipes/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int):
    db = get_db()
    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        db.close()
        return RedirectResponse(url="/recipes", status_code=302)

    tags = db.execute("""
        SELECT dt.* FROM dietary_tags dt
        JOIN recipe_tags rt ON dt.id = rt.tag_id
        WHERE rt.recipe_id = ?
        ORDER BY dt.display_name
    """, (recipe_id,)).fetchall()

    ingredients = db.execute("""
        SELECT * FROM recipe_ingredients WHERE recipe_id = ?
        ORDER BY sort_order
    """, (recipe_id,)).fetchall()

    steps = db.execute("""
        SELECT * FROM recipe_steps WHERE recipe_id = ?
        ORDER BY step_number
    """, (recipe_id,)).fetchall()

    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("recipes/detail.html", {
        "request": request,
        "recipe": recipe,
        "tags": tags,
        "ingredients": ingredients,
        "steps": steps,
        "review_count": review_count,
    })


@router.get("/recipes/{recipe_id}/edit", response_class=HTMLResponse)
async def edit_recipe_form(request: Request, recipe_id: int):
    db = get_db()
    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        db.close()
        return RedirectResponse(url="/recipes", status_code=302)

    all_tags = get_all_tags(db)
    selected_tag_ids = [row["tag_id"] for row in
                        db.execute("SELECT tag_id FROM recipe_tags WHERE recipe_id = ?", (recipe_id,)).fetchall()]

    ingredients = db.execute("""
        SELECT * FROM recipe_ingredients WHERE recipe_id = ?
        ORDER BY sort_order
    """, (recipe_id,)).fetchall()

    steps = db.execute("""
        SELECT * FROM recipe_steps WHERE recipe_id = ?
        ORDER BY step_number
    """, (recipe_id,)).fetchall()

    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("recipes/form.html", {
        "request": request,
        "recipe": recipe,
        "all_tags": all_tags,
        "aisle_categories": AISLE_CATEGORIES,
        "review_count": review_count,
        "selected_tag_ids": selected_tag_ids,
        "ingredients": ingredients,
        "steps": steps,
    })


@router.post("/recipes/{recipe_id}/edit")
async def edit_recipe(request: Request, recipe_id: int):
    form = await request.form()
    db = get_db()

    try:
        db.execute("""
            UPDATE recipes SET
                title = ?, description = ?, base_servings = ?, prep_time_minutes = ?,
                cook_time_minutes = ?, source = ?, source_url = ?, status = ?, notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            form.get("title", "").strip(),
            form.get("description", "").strip() or None,
            int(form.get("base_servings", 4)),
            int(form.get("prep_time_minutes")) if form.get("prep_time_minutes") else None,
            int(form.get("cook_time_minutes")) if form.get("cook_time_minutes") else None,
            form.get("source", "").strip() or None,
            form.get("source_url", "").strip() or None,
            form.get("status", "review"),
            form.get("notes", "").strip() or None,
            recipe_id,
        ))

        db.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        tag_ids = form.getlist("tags")
        for tag_id in tag_ids:
            db.execute("INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                       (recipe_id, int(tag_id)))

        db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        ing_names = form.getlist("ing_name")
        ing_amounts = form.getlist("ing_amount")
        ing_units = form.getlist("ing_unit")
        ing_aisles = form.getlist("ing_aisle")
        ing_notes = form.getlist("ing_notes")

        for i, name in enumerate(ing_names):
            if not name.strip():
                continue
            amount = None
            if i < len(ing_amounts) and ing_amounts[i].strip():
                try:
                    amount = float(ing_amounts[i])
                except ValueError:
                    pass
            db.execute("""
                INSERT INTO recipe_ingredients (recipe_id, name, amount, unit, aisle_category, sort_order, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                recipe_id,
                name.strip(),
                amount,
                ing_units[i].strip() if i < len(ing_units) and ing_units[i].strip() else None,
                ing_aisles[i] if i < len(ing_aisles) else "other",
                i,
                ing_notes[i].strip() if i < len(ing_notes) and ing_notes[i].strip() else None,
            ))

        db.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (recipe_id,))
        step_instructions = form.getlist("step_instruction")
        for i, instruction in enumerate(step_instructions):
            if not instruction.strip():
                continue
            db.execute("""
                INSERT INTO recipe_steps (recipe_id, step_number, instruction)
                VALUES (?, ?, ?)
            """, (recipe_id, i + 1, instruction.strip()))

        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/recipes/" + str(recipe_id), status_code=303)


@router.post("/recipes/{recipe_id}/approve")
async def approve_recipe(request: Request, recipe_id: int):
    db = get_db()
    db.execute("UPDATE recipes SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (recipe_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/recipes/review", status_code=303)


@router.post("/recipes/{recipe_id}/reject")
async def reject_recipe(request: Request, recipe_id: int):
    db = get_db()
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/recipes/review", status_code=303)


@router.post("/recipes/{recipe_id}/delete")
async def delete_recipe(request: Request, recipe_id: int):
    db = get_db()
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/recipes", status_code=303)
