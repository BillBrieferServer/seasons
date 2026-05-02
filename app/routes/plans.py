from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from datetime import date, timedelta
from app.database.db import get_db
from app.config import AISLE_CATEGORIES
from app.routes.recipes import scale_step_text, _build_ingredient_tokens

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def next_monday():
    today = date.today()
    days_ahead = 0 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


@router.get("/plans", response_class=HTMLResponse)
async def plan_list(request: Request):
    db = get_db()
    plans = db.execute("""
        SELECT mp.*,
               COUNT(mpi.id) as meal_count,
               COALESCE(SUM(mpi.servings), 0) as total_servings
        FROM meal_plans mp
        LEFT JOIN meal_plan_items mpi ON mp.id = mpi.meal_plan_id
        GROUP BY mp.id
        ORDER BY mp.created_at DESC
    """).fetchall()
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("plans/list.html", {
        "request": request,
        "plans": plans,
        "review_count": review_count,
        "default_date": next_monday().isoformat(),
    })


@router.post("/plans/new")
async def create_plan(request: Request):
    form = await request.form()
    db = get_db()
    cursor = db.execute(
        "INSERT INTO meal_plans (name, week_start) VALUES (?, ?)",
        (form.get("name", "").strip(), form.get("week_start"))
    )
    plan_id = cursor.lastrowid
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.get("/plans/{plan_id}", response_class=HTMLResponse)
async def plan_detail(request: Request, plan_id: int, q: str = "", tag: str = ""):
    db = get_db()
    plan = db.execute("SELECT * FROM meal_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        db.close()
        return RedirectResponse(url="/plans", status_code=302)

    # Plan items with recipe details
    items = db.execute("""
        SELECT mpi.*, r.title, r.description, r.base_servings,
               r.prep_time_minutes, r.cook_time_minutes,
               GROUP_CONCAT(dt.display_name, '||') as tag_names,
               GROUP_CONCAT(dt.color, '||') as tag_colors
        FROM meal_plan_items mpi
        JOIN recipes r ON mpi.recipe_id = r.id
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE mpi.meal_plan_id = ?
        GROUP BY mpi.id
        ORDER BY mpi.day_of_week IS NULL, mpi.day_of_week,
                 CASE mpi.meal_type
                   WHEN 'breakfast' THEN 0
                   WHEN 'lunch' THEN 1
                   WHEN 'dinner' THEN 2
                   WHEN 'snack' THEN 3
                 END
    """, (plan_id,)).fetchall()

    # Group items by day
    days = {}
    for i in range(7):
        days[i] = []
    days[None] = []
    for item in items:
        days[item["day_of_week"]].append(item)

    # Recipe browser - approved recipes
    recipe_query = """
        SELECT r.*, GROUP_CONCAT(dt.display_name, '||') as tag_names,
               GROUP_CONCAT(dt.color, '||') as tag_colors
        FROM recipes r
        LEFT JOIN recipe_tags rt ON r.id = rt.recipe_id
        LEFT JOIN dietary_tags dt ON rt.tag_id = dt.id
        WHERE r.status = 'approved'
    """
    params = []
    if q:
        recipe_query += " AND (r.title LIKE ? OR r.description LIKE ?)"
        params.extend(["%" + q + "%", "%" + q + "%"])
    if tag:
        recipe_query += " AND r.id IN (SELECT rt2.recipe_id FROM recipe_tags rt2 JOIN dietary_tags dt2 ON rt2.tag_id = dt2.id WHERE dt2.name = ?)"
        params.append(tag)
    recipe_query += " GROUP BY r.id ORDER BY r.title"
    recipes = db.execute(recipe_query, params).fetchall()

    # Tag counts for filter bar
    tag_counts = db.execute("""
        SELECT dt.name, dt.display_name, dt.color, COUNT(rt.recipe_id) as cnt
        FROM dietary_tags dt
        LEFT JOIN recipe_tags rt ON dt.id = rt.tag_id
        LEFT JOIN recipes r ON rt.recipe_id = r.id AND r.status = 'approved'
        GROUP BY dt.id
        ORDER BY dt.display_name
    """).fetchall()

    # Fetch sides
    sides = db.execute("""
        SELECT * FROM meal_plan_sides
        WHERE meal_plan_id = ?
        ORDER BY day_of_week IS NULL, day_of_week, name
    """, (plan_id,)).fetchall()

    # Group sides by day
    sides_by_day = {}
    for i in range(7):
        sides_by_day[i] = []
    sides_by_day[None] = []
    for side in sides:
        sides_by_day[side["day_of_week"]].append(side)

    total_meals = len(items)
    total_servings = sum(item["servings"] for item in items)
    total_sides = len(sides)
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("plans/detail.html", {
        "request": request,
        "plan": plan,
        "days": days,
        "day_names": DAY_NAMES,
        "meal_types": MEAL_TYPES,
        "recipes": recipes,
        "tag_counts": tag_counts,
        "total_meals": total_meals,
        "total_servings": total_servings,
        "review_count": review_count,
        "search_query": q,
        "active_tag": tag,
        "sides_by_day": sides_by_day,
        "total_sides": total_sides,
        "aisle_categories": [
            ("produce", "Produce"),
            ("dairy", "Dairy & Eggs"),
            ("meat", "Meat & Seafood"),
            ("bakery", "Bakery & Bread"),
            ("frozen", "Frozen"),
            ("canned", "Canned & Jarred"),
            ("grains", "Grains, Pasta & Rice"),
            ("other", "Other"),
        ],
    })


@router.post("/plans/{plan_id}/add-recipe")
async def add_recipe_to_plan(request: Request, plan_id: int):
    form = await request.form()
    recipe_id = int(form.get("recipe_id"))
    db = get_db()
    row = db.execute("SELECT base_servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    servings = row["base_servings"] if row and row["base_servings"] else 2
    if servings > 16:
        servings = 16
    db.execute(
        "INSERT INTO meal_plan_items (meal_plan_id, recipe_id, day_of_week, meal_type, servings) VALUES (?, ?, NULL, 'dinner', ?)",
        (plan_id, recipe_id, servings)
    )
    db.commit()
    db.close()
    # Preserve filter state
    q = form.get("q", "")
    tag = form.get("tag", "")
    url = "/plans/" + str(plan_id)
    qs = []
    if q:
        qs.append("q=" + q)
    if tag:
        qs.append("tag=" + tag)
    if qs:
        url += "?" + "&".join(qs)
    return RedirectResponse(url=url, status_code=303)


@router.post("/plans/{plan_id}/update-item/{item_id}")
async def update_plan_item(request: Request, plan_id: int, item_id: int):
    form = await request.form()
    day = form.get("day_of_week")
    if day == "" or day == "null":
        day = None
    else:
        day = int(day)
    servings = int(form.get("servings", 2))
    if servings < 1:
        servings = 1
    if servings > 16:
        servings = 16
    meal_type = form.get("meal_type", "dinner")
    db = get_db()
    db.execute(
        "UPDATE meal_plan_items SET day_of_week = ?, meal_type = ?, servings = ? WHERE id = ? AND meal_plan_id = ?",
        (day, meal_type, servings, item_id, plan_id)
    )
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.post("/plans/{plan_id}/remove-item/{item_id}")
async def remove_plan_item(request: Request, plan_id: int, item_id: int):
    db = get_db()
    db.execute("DELETE FROM meal_plan_items WHERE id = ? AND meal_plan_id = ?", (item_id, plan_id))
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.post("/plans/{plan_id}/delete")
async def delete_plan(request: Request, plan_id: int):
    db = get_db()
    # Delete associated shopping lists and their items
    for sl in db.execute("SELECT id FROM shopping_lists WHERE meal_plan_id = ?", (plan_id,)).fetchall():
        db.execute("DELETE FROM shopping_list_items WHERE shopping_list_id = ?", (sl["id"],))
    db.execute("DELETE FROM shopping_lists WHERE meal_plan_id = ?", (plan_id,))
    # Delete plan items and sides
    db.execute("DELETE FROM meal_plan_items WHERE meal_plan_id = ?", (plan_id,))
    db.execute("DELETE FROM meal_plan_sides WHERE meal_plan_id = ?", (plan_id,))
    db.execute("DELETE FROM meal_plans WHERE id = ?", (plan_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/plans", status_code=303)


@router.post("/plans/{plan_id}/duplicate")
async def duplicate_plan(request: Request, plan_id: int):
    db = get_db()
    plan = db.execute("SELECT * FROM meal_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        db.close()
        return RedirectResponse(url="/plans", status_code=302)

    cursor = db.execute(
        "INSERT INTO meal_plans (name, week_start) VALUES (?, ?)",
        (plan["name"] + " (copy)", plan["week_start"])
    )
    new_id = cursor.lastrowid

    items = db.execute("SELECT * FROM meal_plan_items WHERE meal_plan_id = ?", (plan_id,)).fetchall()
    for item in items:
        db.execute(
            "INSERT INTO meal_plan_items (meal_plan_id, recipe_id, day_of_week, meal_type, servings) VALUES (?, ?, ?, ?, ?)",
            (new_id, item["recipe_id"], item["day_of_week"], item["meal_type"], item["servings"])
        )

    # Copy sides too
    sides = db.execute("SELECT * FROM meal_plan_sides WHERE meal_plan_id = ?", (plan_id,)).fetchall()
    for side in sides:
        db.execute(
            "INSERT INTO meal_plan_sides (meal_plan_id, day_of_week, name, amount, unit, aisle_category, meal_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, side["day_of_week"], side["name"], side["amount"], side["unit"], side["aisle_category"], side["meal_type"])
        )

    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(new_id), status_code=303)


@router.post("/plans/{plan_id}/edit")
async def edit_plan(request: Request, plan_id: int):
    form = await request.form()
    db = get_db()
    db.execute(
        "UPDATE meal_plans SET name = ?, week_start = ? WHERE id = ?",
        (form.get("name", "").strip(), form.get("week_start"), plan_id)
    )
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)



@router.post("/plans/{plan_id}/add-side")
async def add_side_to_plan(request: Request, plan_id: int):
    form = await request.form()
    name = form.get("side_name", "").strip()
    if not name:
        return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)
    day = form.get("side_day")
    if day == "" or day == "null":
        day = None
    else:
        day = int(day)
    amount = form.get("side_amount", "").strip()
    amount = float(amount) if amount else None
    unit = form.get("side_unit", "").strip() or None
    aisle = form.get("side_aisle", "produce")
    meal_type = form.get("side_meal_type", "dinner")
    db = get_db()
    db.execute(
        "INSERT INTO meal_plan_sides (meal_plan_id, day_of_week, name, amount, unit, aisle_category, meal_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (plan_id, day, name, amount, unit, aisle, meal_type)
    )
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.post("/plans/{plan_id}/update-side/{side_id}")
async def update_side(request: Request, plan_id: int, side_id: int):
    form = await request.form()
    day = form.get("day_of_week")
    if day == "" or day == "null":
        day = None
    else:
        day = int(day)
    meal_type = form.get("meal_type", "dinner")
    db = get_db()
    db.execute(
        "UPDATE meal_plan_sides SET day_of_week = ?, meal_type = ? WHERE id = ? AND meal_plan_id = ?",
        (day, meal_type, side_id, plan_id)
    )
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.post("/plans/{plan_id}/remove-side/{side_id}")
async def remove_side(request: Request, plan_id: int, side_id: int):
    db = get_db()
    db.execute("DELETE FROM meal_plan_sides WHERE id = ? AND meal_plan_id = ?", (side_id, plan_id))
    db.commit()
    db.close()
    return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)


@router.get("/plans/{plan_id}/cook/{item_id}", response_class=HTMLResponse)
async def cooking_mode_from_plan(request: Request, plan_id: int, item_id: int):
    db = get_db()
    item = db.execute("""
        SELECT mpi.*, r.title, r.description, r.base_servings,
               r.prep_time_minutes, r.cook_time_minutes, r.notes as recipe_notes
        FROM meal_plan_items mpi
        JOIN recipes r ON mpi.recipe_id = r.id
        WHERE mpi.id = ? AND mpi.meal_plan_id = ?
    """, (item_id, plan_id)).fetchone()

    if not item:
        db.close()
        return RedirectResponse(url=f"/plans/{plan_id}", status_code=302)

    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (item["recipe_id"],)).fetchone()

    ingredients = db.execute("""
        SELECT * FROM recipe_ingredients WHERE recipe_id = ?
        ORDER BY sort_order
    """, (item["recipe_id"],)).fetchall()

    steps_raw = db.execute("""
        SELECT * FROM recipe_steps WHERE recipe_id = ?
        ORDER BY step_number
    """, (item["recipe_id"],)).fetchall()

    db.close()

    ingredient_tokens = _build_ingredient_tokens([ing["name"] for ing in ingredients])
    steps = []
    for s in steps_raw:
        d = dict(s)
        d["instruction_html"] = scale_step_text(s["instruction"], ingredient_tokens)
        steps.append(d)

    return templates.TemplateResponse("recipes/cooking.html", {
        "request": request,
        "recipe": recipe,
        "ingredients": ingredients,
        "steps": steps,
        "servings": item["servings"],
        "return_url": f"/plans/{plan_id}",
    })
