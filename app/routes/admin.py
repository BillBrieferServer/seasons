from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.database.db import get_db
from app.routes.auth import get_current_user, hash_pin

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_admin_context(conn, user, success=None, **extra):
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    tags = conn.execute("SELECT * FROM dietary_tags ORDER BY display_name").fetchall()
    ctx = {"user": user, "settings": settings, "tags": tags, "success": success}
    ctx.update(extra)
    return ctx


@router.get("", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    ctx = get_admin_context(conn, user)
    conn.close()
    return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})


@router.post("/reset-pin", response_class=HTMLResponse)
async def reset_pin(request: Request, new_pin: str = Form(...), confirm_pin: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()

    if new_pin != confirm_pin:
        ctx = get_admin_context(conn, user, pin_error="PINs do not match.")
        conn.close()
        return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})
    if not new_pin.isdigit() or len(new_pin) < 4 or len(new_pin) > 6:
        ctx = get_admin_context(conn, user, pin_error="PIN must be 4-6 digits.")
        conn.close()
        return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})

    conn.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (hash_pin(new_pin), user["id"]))
    conn.commit()
    ctx = get_admin_context(conn, user, success="PIN updated successfully.")
    conn.close()
    return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})


@router.post("/settings", response_class=HTMLResponse)
async def update_settings(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    conn = get_db()
    for key in ["business_name", "tagline", "phone", "email", "address", "service_area"]:
        val = form.get(key)
        if val is not None:
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (val.strip(), key))
    conn.commit()
    ctx = get_admin_context(conn, user, success="Settings saved.")
    conn.close()
    return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})


@router.post("/tags/add", response_class=HTMLResponse)
async def add_tag(request: Request, tag_name: str = Form(...), tag_display: str = Form(...), tag_color: str = Form("#808D86")):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    # Slugify the name
    slug = tag_name.strip().lower().replace(" ", "-")
    display = tag_display.strip()
    try:
        conn.execute("INSERT INTO dietary_tags (name, display_name, color) VALUES (?, ?, ?)",
                     (slug, display, tag_color.strip()))
        conn.commit()
        ctx = get_admin_context(conn, user, success=f"Tag \"{display}\" added.")
    except Exception:
        ctx = get_admin_context(conn, user, tag_error=f"Tag \"{slug}\" already exists.")
    conn.close()
    return templates.TemplateResponse("admin/index.html", {"request": request, **ctx})


@router.post("/tags/delete/{tag_id}")
async def delete_tag(request: Request, tag_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    tag = conn.execute("SELECT display_name FROM dietary_tags WHERE id = ?", (tag_id,)).fetchone()
    if tag:
        conn.execute("DELETE FROM recipe_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM client_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM dietary_tags WHERE id = ?", (tag_id,))
        conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=302)
