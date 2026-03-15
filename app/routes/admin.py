from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.database.db import get_db
from app.routes.auth import get_current_user, hash_pin

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    conn.close()
    return templates.TemplateResponse("admin/index.html", {
        "request": request,
        "user": user,
        "settings": settings,
        "success": None
    })


@router.post("/reset-pin", response_class=HTMLResponse)
async def reset_pin(request: Request, new_pin: str = Form(...), confirm_pin: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}

    if new_pin != confirm_pin:
        conn.close()
        return templates.TemplateResponse("admin/index.html", {
            "request": request, "user": user, "settings": settings,
            "success": None, "pin_error": "PINs do not match."
        })
    if not new_pin.isdigit() or len(new_pin) < 4 or len(new_pin) > 6:
        conn.close()
        return templates.TemplateResponse("admin/index.html", {
            "request": request, "user": user, "settings": settings,
            "success": None, "pin_error": "PIN must be 4-6 digits."
        })

    conn.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (hash_pin(new_pin), user["id"]))
    conn.commit()
    conn.close()
    return templates.TemplateResponse("admin/index.html", {
        "request": request, "user": user, "settings": settings,
        "success": "PIN updated successfully."
    })


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
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    conn.close()
    return templates.TemplateResponse("admin/index.html", {
        "request": request, "user": user, "settings": settings,
        "success": "Settings saved."
    })
