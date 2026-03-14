from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from collections import defaultdict
from app.database.db import get_db
from app.config import AISLE_CATEGORIES

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

AISLE_ORDER = [
    ("produce", "Produce"),
    ("dairy", "Dairy & Eggs"),
    ("meat", "Meat & Seafood"),
    ("bakery", "Bakery & Bread"),
    ("canned", "Canned & Jarred"),
    ("grains", "Grains, Pasta & Rice"),
    ("frozen", "Frozen"),
    ("spices", "Spices & Seasonings"),
    ("oils", "Oils & Vinegars"),
    ("condiments", "Condiments & Sauces"),
    ("baking", "Baking"),
    ("snacks", "Snacks & Nuts"),
    ("beverages", "Beverages"),
    ("other", "Other"),
]

AISLE_LABELS = dict(AISLE_ORDER)
AISLE_KEYS = [a[0] for a in AISLE_ORDER]


def format_amount(amount):
    if amount is None or amount == 0:
        return ""
    if abs(amount - round(amount)) < 0.05:
        return str(int(round(amount)))
    if amount < 1:
        formatted = f"{amount:.2f}".rstrip('0').rstrip('.')
        return formatted
    formatted = f"{amount:.1f}".rstrip('0').rstrip('.')
    return formatted


def generate_list_items(db, plan_id):
    """Aggregate and scale ingredients from a meal plan."""
    items = db.execute("""
        SELECT mpi.servings as plan_servings, r.base_servings,
               ri.name, ri.amount, ri.unit, ri.aisle_category
        FROM meal_plan_items mpi
        JOIN recipes r ON mpi.recipe_id = r.id
        JOIN recipe_ingredients ri ON r.id = ri.recipe_id
        WHERE mpi.meal_plan_id = ?
    """, (plan_id,)).fetchall()

    # Combine: key = (lowercase name, unit)
    combined = defaultdict(lambda: {"amount": None, "aisle": "other", "has_amount": False})

    for item in items:
        name_lower = item["name"].strip().lower()
        unit = (item["unit"] or "").strip().lower() or None
        key = (name_lower, unit)

        if item["amount"] is not None and item["base_servings"]:
            scale = item["plan_servings"] / item["base_servings"]
            scaled = item["amount"] * scale
            entry = combined[key]
            if entry["has_amount"]:
                entry["amount"] = (entry["amount"] or 0) + scaled
            else:
                entry["amount"] = scaled
                entry["has_amount"] = True
        else:
            # No amount - just mark presence
            if not combined[key]["has_amount"]:
                combined[key]["amount"] = None

        # Keep aisle from first occurrence
        if combined[key].get("aisle") == "other" and item["aisle_category"]:
            combined[key]["aisle"] = item["aisle_category"]

        # Store original-case name from first occurrence
        if "display_name" not in combined[key]:
            combined[key]["display_name"] = item["name"].strip()

    result = []
    for (name_lower, unit), data in combined.items():
        result.append({
            "name": data.get("display_name", name_lower),
            "amount": data["amount"],
            "unit": unit,
            "aisle": data["aisle"],
        })

    return result


@router.post("/plans/{plan_id}/generate-list")
async def generate_shopping_list(request: Request, plan_id: int):
    db = get_db()
    # Check plan has items
    count = db.execute("SELECT COUNT(*) as cnt FROM meal_plan_items WHERE meal_plan_id = ?", (plan_id,)).fetchone()["cnt"]
    if count == 0:
        db.close()
        return RedirectResponse(url="/plans/" + str(plan_id), status_code=303)

    items = generate_list_items(db, plan_id)

    cursor = db.execute("INSERT INTO shopping_lists (meal_plan_id) VALUES (?)", (plan_id,))
    list_id = cursor.lastrowid

    for item in items:
        db.execute("""
            INSERT INTO shopping_list_items (shopping_list_id, ingredient_name, amount, unit, aisle_category)
            VALUES (?, ?, ?, ?, ?)
        """, (list_id, item["name"], item["amount"], item["unit"], item["aisle"]))

    db.commit()
    db.close()
    return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)


@router.get("/shopping", response_class=HTMLResponse)
async def shopping_index(request: Request):
    db = get_db()
    lists = db.execute("""
        SELECT sl.*, mp.name as plan_name, mp.week_start,
               COUNT(sli.id) as item_count,
               COALESCE(SUM(sli.checked), 0) as checked_count
        FROM shopping_lists sl
        LEFT JOIN meal_plans mp ON sl.meal_plan_id = mp.id
        LEFT JOIN shopping_list_items sli ON sl.id = sli.shopping_list_id
        GROUP BY sl.id
        ORDER BY sl.created_at DESC
    """).fetchall()
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("shopping/list.html", {
        "request": request,
        "lists": lists,
        "review_count": review_count,
    })


@router.get("/shopping/{list_id}", response_class=HTMLResponse)
async def shopping_detail(request: Request, list_id: int):
    db = get_db()
    slist = db.execute("""
        SELECT sl.*, mp.name as plan_name, mp.week_start, mp.id as plan_id
        FROM shopping_lists sl
        LEFT JOIN meal_plans mp ON sl.meal_plan_id = mp.id
        WHERE sl.id = ?
    """, (list_id,)).fetchone()
    if not slist:
        db.close()
        return RedirectResponse(url="/shopping", status_code=302)

    items = db.execute("""
        SELECT * FROM shopping_list_items
        WHERE shopping_list_id = ?
        ORDER BY ingredient_name
    """, (list_id,)).fetchall()

    # Group by aisle in correct order
    aisles = []
    aisle_items = defaultdict(list)
    for item in items:
        aisle_items[item["aisle_category"] or "other"].append(item)

    for aisle_key, aisle_label in AISLE_ORDER:
        if aisle_key in aisle_items:
            unchecked = [i for i in aisle_items[aisle_key] if not i["checked"]]
            checked = [i for i in aisle_items[aisle_key] if i["checked"]]
            aisles.append({
                "key": aisle_key,
                "label": aisle_label,
                "items": unchecked + checked,
                "count": len(aisle_items[aisle_key]),
                "unchecked_count": len(unchecked),
            })

    total = len(items)
    checked = sum(1 for i in items if i["checked"])
    pct = int((checked / total * 100)) if total > 0 else 0

    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()

    return templates.TemplateResponse("shopping/detail.html", {
        "request": request,
        "slist": slist,
        "aisles": aisles,
        "total": total,
        "checked": checked,
        "pct": pct,
        "review_count": review_count,
        "format_amount": format_amount,
    })


@router.get("/shopping/{list_id}/print", response_class=HTMLResponse)
async def shopping_print(request: Request, list_id: int):
    db = get_db()
    slist = db.execute("""
        SELECT sl.*, mp.name as plan_name, mp.week_start
        FROM shopping_lists sl
        LEFT JOIN meal_plans mp ON sl.meal_plan_id = mp.id
        WHERE sl.id = ?
    """, (list_id,)).fetchone()
    if not slist:
        db.close()
        return RedirectResponse(url="/shopping", status_code=302)

    items = db.execute("""
        SELECT * FROM shopping_list_items
        WHERE shopping_list_id = ?
        ORDER BY ingredient_name
    """, (list_id,)).fetchall()

    aisle_items = defaultdict(list)
    for item in items:
        aisle_items[item["aisle_category"] or "other"].append(item)

    aisles = []
    for aisle_key, aisle_label in AISLE_ORDER:
        if aisle_key in aisle_items:
            aisles.append({
                "label": aisle_label,
                "items": aisle_items[aisle_key],
            })

    db.close()

    return templates.TemplateResponse("shopping/print.html", {
        "request": request,
        "slist": slist,
        "aisles": aisles,
        "format_amount": format_amount,
    })


@router.post("/shopping/{list_id}/check/{item_id}")
async def toggle_check(request: Request, list_id: int, item_id: int):
    db = get_db()
    item = db.execute("SELECT checked FROM shopping_list_items WHERE id = ? AND shopping_list_id = ?",
                      (item_id, list_id)).fetchone()
    if item:
        new_val = 0 if item["checked"] else 1
        db.execute("UPDATE shopping_list_items SET checked = ? WHERE id = ?", (new_val, item_id))
        db.commit()
    db.close()

    # Check if this is a fetch request
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        from fastapi.responses import JSONResponse
        return JSONResponse({"checked": bool(new_val)})

    return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)


@router.post("/shopping/{list_id}/check-all")
async def check_all_aisle(request: Request, list_id: int):
    form = await request.form()
    aisle = form.get("aisle_category", "other")
    db = get_db()
    db.execute("""
        UPDATE shopping_list_items SET checked = 1
        WHERE shopping_list_id = ? AND aisle_category = ?
    """, (list_id, aisle))
    db.commit()
    db.close()
    return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)


@router.post("/shopping/{list_id}/uncheck-all")
async def uncheck_all(request: Request, list_id: int):
    db = get_db()
    db.execute("UPDATE shopping_list_items SET checked = 0 WHERE shopping_list_id = ?", (list_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)


@router.post("/shopping/{list_id}/delete")
async def delete_shopping_list(request: Request, list_id: int):
    db = get_db()
    db.execute("DELETE FROM shopping_lists WHERE id = ?", (list_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/{list_id}/regenerate")
async def regenerate_list(request: Request, list_id: int):
    db = get_db()
    slist = db.execute("SELECT meal_plan_id FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    if not slist or not slist["meal_plan_id"]:
        db.close()
        return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)

    plan_id = slist["meal_plan_id"]
    items = generate_list_items(db, plan_id)

    db.execute("DELETE FROM shopping_list_items WHERE shopping_list_id = ?", (list_id,))
    for item in items:
        db.execute("""
            INSERT INTO shopping_list_items (shopping_list_id, ingredient_name, amount, unit, aisle_category)
            VALUES (?, ?, ?, ?, ?)
        """, (list_id, item["name"], item["amount"], item["unit"], item["aisle"]))

    db.commit()
    db.close()
    return RedirectResponse(url="/shopping/" + str(list_id), status_code=303)
