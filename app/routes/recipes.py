from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional, List
import os
import json
import base64
import time
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
async def review_queue(request: Request, msg: str = ""):
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
        "flash_msg": msg,
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


@router.get("/recipes/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    db = get_db()
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()
    api_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return templates.TemplateResponse("recipes/upload.html", {
        "request": request,
        "review_count": review_count,
        "api_configured": api_configured,
    })


@router.post("/recipes/upload")
async def upload_process(request: Request, files: List[UploadFile] = File(...)):
    api_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_configured:
        return RedirectResponse(url="/recipes/upload", status_code=303)

    successes = []
    failures = []

    for f in files:
        filename = f.filename or "unknown.pdf"

        # Validate file type
        if not filename.lower().endswith(".pdf"):
            failures.append((filename, "Not a PDF file"))
            continue

        # Read bytes
        pdf_bytes = await f.read()

        # Validate size (10MB)
        if len(pdf_bytes) > 10 * 1024 * 1024:
            failures.append((filename, "File too large (max 10MB)"))
            continue

        if len(pdf_bytes) == 0:
            failures.append((filename, "Empty file"))
            continue

        # Extract via Claude API
        data = extract_recipe_from_pdf(pdf_bytes)

        if "error" in data:
            failures.append((filename, data["error"]))
        else:
            db = get_db()
            try:
                save_extracted_recipe(data, filename, db)
                db.commit()
                successes.append(filename)
            except Exception as e:
                failures.append((filename, "Database error: " + str(e)))
            finally:
                db.close()

        # Brief delay between files
        if len(files) > 1:
            time.sleep(1)

    # Build result message
    parts = []
    if successes:
        parts.append("Extracted " + str(len(successes)) + " recipe" + ("s" if len(successes) != 1 else "") + " from PDFs.")
    if failures:
        fail_details = "; ".join(name + " (" + err + ")" for name, err in failures)
        parts.append("Failed: " + fail_details)

    msg = " ".join(parts) if parts else "No files processed."

    return RedirectResponse(url="/recipes/review?msg=" + msg, status_code=303)


@router.get("/recipes/{recipe_id}", response_class=HTMLResponse)


@router.get("/recipes/import-url", response_class=HTMLResponse)
async def import_url_form(request: Request):
    db = get_db()
    review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
    db.close()
    api_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return templates.TemplateResponse("recipes/import_url.html", {
        "request": request,
        "review_count": review_count,
        "api_configured": api_configured,
    })


@router.post("/recipes/import-url", response_class=HTMLResponse)
async def import_url_process(request: Request):
    form = await request.form()
    url = (form.get("url") or "").strip()

    if not url or not (url.startswith("http://") or url.startswith("https://")):
        db = get_db()
        review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
        db.close()
        return templates.TemplateResponse("recipes/import_url.html", {
            "request": request,
            "review_count": review_count,
            "api_configured": True,
            "error_msg": "Please enter a valid URL starting with http:// or https://",
            "url_value": url,
        })

    page = await fetch_page_content(url)
    if "error" in page:
        db = get_db()
        review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
        db.close()
        return templates.TemplateResponse("recipes/import_url.html", {
            "request": request,
            "review_count": review_count,
            "api_configured": True,
            "error_msg": page["error"],
            "url_value": url,
        })

    if len(page["text"]) < 100:
        db = get_db()
        review_count = db.execute("SELECT COUNT(*) as cnt FROM recipes WHERE status='review'").fetchone()["cnt"]
        db.close()
        return templates.TemplateResponse("recipes/import_url.html", {
            "request": request,
            "review_count": review_count,
            "api_configured": True,
            "error_msg": "This page couldn't be read. It may require JavaScript to load or be behind a paywall. Try copying the recipe text and entering it manually.",
            "url_value": url,
        })

    recipes_data = extract_recipes_from_web(page["text"], page["schema_recipes"])

    success_count = 0
    error_count = 0

    db = get_db()
    try:
        for recipe_data in recipes_data:
            if "error" in recipe_data:
                error_count += 1
                continue
            if not recipe_data.get("title") or not recipe_data.get("ingredients"):
                continue
            rid = save_extracted_recipe(recipe_data, url, db)
            db.execute("UPDATE recipes SET source = 'Web import' WHERE id = ?", (rid,))
            success_count += 1
        db.commit()
    finally:
        db.close()

    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    if success_count > 0:
        msg = f"Extracted {success_count} recipe{'s' if success_count != 1 else ''} from {domain}"
    elif error_count > 0:
        msg = f"Could not extract recipes from {domain}. The API returned an error."
    else:
        msg = "No recipes were found on this page."

    return RedirectResponse(url="/recipes/review?msg=" + msg, status_code=303)

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


# ========================================
# PDF Upload & Claude API Extraction
# ========================================

EXTRACTION_PROMPT = """You are extracting a recipe from this document. The document may be typed or handwritten, portrait or landscape orientation. Extract ALL information you can find and return it as a JSON object.

Return ONLY valid JSON with no other text, no markdown backticks, no explanation. Use this exact structure:

{
    "title": "Recipe title",
    "description": "Brief 1-2 sentence description of the dish (write one if not explicitly stated)",
    "base_servings": 4,
    "prep_time_minutes": 15,
    "cook_time_minutes": 30,
    "notes": "Any tips, variations, or notes from the original recipe",
    "ingredients": [
        {
            "name": "Ingredient name",
            "amount": 1.5,
            "unit": "cups",
            "aisle_category": "produce",
            "notes": "diced, optional modifier"
        }
    ],
    "steps": [
        "First instruction step",
        "Second instruction step"
    ],
    "suggested_tags": ["heart-healthy", "low-sodium"]
}

Rules:
- For base_servings: use the number stated in the recipe. If not stated, estimate based on ingredient quantities and default to 4.
- For prep_time_minutes and cook_time_minutes: extract if stated, otherwise set to null.
- For ingredient amounts: use decimal numbers (1.5, 0.25, etc). For "a pinch" or "to taste", set amount to null.
- For ingredient units: use standard abbreviations — cups, tbsp, tsp, lbs, oz, whole, cloves, stalks, cans, etc. For countable items like "3 carrots", use "whole" as the unit.
- For aisle_category: assign each ingredient to one of these exact categories:
  produce, dairy, meat, bakery, frozen, canned, grains, spices, oils, snacks, beverages, baking, condiments, other
- For steps: extract each instruction as a separate string in order. If written as a paragraph, break it into logical steps.
- For suggested_tags: suggest which of these dietary categories apply based on the recipe content:
  heart-healthy, diabetic-friendly, anti-inflammatory, bone-health, digestive-wellness, soft-foods, high-protein, calorie-dense, low-sugar, low-sodium, general-healthy, freezer-friendly
- For handwritten recipes: do your best to read the handwriting. If a word is unclear, make your best guess and note "[unclear]" in the notes field.
- If the document contains multiple recipes, extract only the FIRST one and note in description that additional recipes were found.
"""


def get_anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def parse_recipe_json(response_text):
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": "Could not parse extracted recipe: " + str(e), "raw_response": response_text[:500]}


def extract_recipe_from_pdf(pdf_bytes):
    client = get_anthropic_client()
    if not client:
        return {"error": "Claude API key not configured. Contact Steve."}

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    },
                ],
            }],
        )
        response_text = message.content[0].text
        return parse_recipe_json(response_text)
    except Exception as e:
        return {"error": "API call failed: " + str(e)}


def save_extracted_recipe(data, filename, db):
    cursor = db.execute("""
        INSERT INTO recipes (title, description, base_servings, prep_time_minutes,
                            cook_time_minutes, source, source_url, notes, status)
        VALUES (?, ?, ?, ?, ?, 'PDF upload', ?, ?, 'review')
    """, (
        data.get("title", "Untitled Recipe"),
        data.get("description", ""),
        data.get("base_servings", 4),
        data.get("prep_time_minutes"),
        data.get("cook_time_minutes"),
        filename,
        data.get("notes", ""),
    ))
    recipe_id = cursor.lastrowid

    for i, ing in enumerate(data.get("ingredients", [])):
        amount = ing.get("amount")
        if amount is not None:
            try:
                amount = float(amount)
            except (ValueError, TypeError):
                amount = None
        db.execute("""
            INSERT INTO recipe_ingredients (recipe_id, name, amount, unit, aisle_category, sort_order, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            recipe_id,
            ing.get("name", "Unknown ingredient"),
            amount,
            ing.get("unit", "") or None,
            ing.get("aisle_category", "other"),
            i,
            ing.get("notes", "") or None,
        ))

    for i, step in enumerate(data.get("steps", [])):
        db.execute("""
            INSERT INTO recipe_steps (recipe_id, step_number, instruction)
            VALUES (?, ?, ?)
        """, (recipe_id, i + 1, step))

    for tag_name in data.get("suggested_tags", []):
        tag = db.execute("SELECT id FROM dietary_tags WHERE name = ?", (tag_name,)).fetchone()
        if tag:
            db.execute("INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                       (recipe_id, tag["id"]))

    return recipe_id



# ========================================
# Cooking Mode
# ========================================

def format_cook_amount(amount):
    if amount is None:
        return None
    if amount == 0:
        return ""
    if abs(amount - round(amount)) < 0.05:
        return str(int(round(amount)))
    if amount < 1:
        return f"{amount:.2f}".rstrip('0').rstrip('.')
    return f"{amount:.1f}".rstrip('0').rstrip('.')


@router.get("/recipes/{recipe_id}/cook", response_class=HTMLResponse)
async def cooking_mode(request: Request, recipe_id: int, servings: Optional[int] = None):
    db = get_db()
    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        db.close()
        return RedirectResponse(url="/recipes", status_code=302)

    active_servings = servings if servings else recipe["base_servings"]

    ingredients = db.execute("""
        SELECT * FROM recipe_ingredients WHERE recipe_id = ?
        ORDER BY sort_order
    """, (recipe_id,)).fetchall()

    steps = db.execute("""
        SELECT * FROM recipe_steps WHERE recipe_id = ?
        ORDER BY step_number
    """, (recipe_id,)).fetchall()

    db.close()

    return templates.TemplateResponse("recipes/cooking.html", {
        "request": request,
        "recipe": recipe,
        "ingredients": ingredients,
        "steps": steps,
        "servings": active_servings,
        "return_url": f"/recipes/{recipe_id}",
    })



# ========================================
# Web URL Recipe Import Helpers
# ========================================

import httpx
from bs4 import BeautifulSoup

WEB_EXTRACTION_PROMPT = """You are extracting recipes from a web page. The page content below may contain one recipe or many recipes. It also contains navigation text, ads, and other non-recipe content — ignore all of that.

Extract EVERY recipe you find on this page. Return a JSON array of recipe objects — even if there's only one recipe, return it in an array.

Return ONLY valid JSON with no other text, no markdown backticks, no explanation.

[
    {
        "title": "Recipe title",
        "description": "Brief 1-2 sentence description of the dish",
        "base_servings": 4,
        "prep_time_minutes": 15,
        "cook_time_minutes": 30,
        "notes": "Any tips or notes from the original recipe",
        "ingredients": [
            {
                "name": "Ingredient name",
                "amount": 1.5,
                "unit": "cups",
                "aisle_category": "produce",
                "notes": "diced"
            }
        ],
        "steps": [
            "First instruction step",
            "Second instruction step"
        ],
        "suggested_tags": ["heart-healthy", "low-sodium"]
    }
]

Rules:
- Extract ALL recipes found on the page — there may be 1 or there may be 20+
- For base_servings: use the number stated. If not stated, default to 4.
- For times: extract if stated, otherwise null.
- For ingredient amounts: use decimal numbers (1.5, 0.25). For "to taste", set amount to null.
- For ingredient units: use standard abbreviations — cups, tbsp, tsp, lbs, oz, whole, cloves, stalks, etc. For countable items like "3 carrots", use "whole".
- For aisle_category: assign to one of: produce, dairy, meat, bakery, frozen, canned, grains, spices, oils, snacks, beverages, baking, condiments, other
- For steps: each instruction as a separate string in order.
- For suggested_tags: suggest from: heart-healthy, diabetic-friendly, anti-inflammatory, bone-health, digestive-wellness, soft-foods, high-protein, calorie-dense, low-sugar, low-sodium, general-healthy, freezer-friendly
- IGNORE non-recipe content (navigation, author bios, ads, related articles, comments)
- If the page has Schema.org recipe data included below, use it — it's usually the most accurate source.
- If a recipe has no ingredients or no steps, skip it — it's probably just a mention, not a full recipe.
"""


async def fetch_page_content(url: str) -> dict:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SeasonsRecipeImporter/1.0)"}
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return {"error": f"Page returned error {e.response.status_code}"}
    except httpx.RequestError as e:
        return {"error": f"Could not load page: {str(e)}"}

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    schema_recipes = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                schema_recipes.extend([d for d in data if isinstance(d, dict) and d.get("@type") == "Recipe"])
            elif isinstance(data, dict):
                if data.get("@type") == "Recipe":
                    schema_recipes.append(data)
                elif "@graph" in data:
                    schema_recipes.extend([d for d in data["@graph"] if isinstance(d, dict) and d.get("@type") == "Recipe"])
        except (json.JSONDecodeError, TypeError):
            pass

    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
        tag.decompose()

    body = soup.find("body")
    text_content = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

    if len(text_content) > 50000:
        text_content = text_content[:50000] + "\n[Content truncated]"

    return {
        "text": text_content,
        "schema_recipes": schema_recipes,
        "url": str(response.url),
    }


def extract_recipes_from_web(text_content: str, schema_recipes: list) -> list:
    client = get_anthropic_client()
    if not client:
        return [{"error": "Claude API key not configured. Contact Steve."}]

    content = text_content
    if schema_recipes:
        content += "\n\n--- SCHEMA.ORG RECIPE DATA ---\n"
        content += json.dumps(schema_recipes, indent=2)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": WEB_EXTRACTION_PROMPT},
                    {"type": "text", "text": f"--- PAGE CONTENT ---\n{content}"},
                ],
            }],
        )
        response_text = message.content[0].text
        return parse_web_recipes_json(response_text)
    except Exception as e:
        return [{"error": "API call failed: " + str(e)}]


def parse_web_recipes_json(response_text: str) -> list:
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        return [{"error": "Could not parse response"}]
