from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import hashlib
import secrets
import time

from app.database.db import get_db

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

SECRET_KEY = None

def get_secret():
    global SECRET_KEY
    if SECRET_KEY is None:
        import os
        SECRET_KEY = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
    return SECRET_KEY

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def create_session_token(user_id: int) -> str:
    import hmac
    ts = str(int(time.time()))
    payload = f"{user_id}:{ts}"
    sig = hmac.new(get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"

def verify_session_token(token: str):
    import hmac
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        user_id, ts, sig = parts
        payload = f"{user_id}:{ts}"
        expected = hmac.new(get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        if time.time() - int(ts) > 30 * 24 * 3600:
            return None
        return int(user_id)
    except Exception:
        return None

def get_current_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = verify_session_token(token)
    if user_id is None:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user and user["is_setup"]:
        return RedirectResponse(url="/", status_code=302)
    # Check if any user has a PIN set up
    conn = get_db()
    setup_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_setup = 1").fetchone()["c"]
    conn.close()
    if setup_count > 0:
        # Show PIN-only page (checks all users' PINs)
        return templates.TemplateResponse("auth/pin.html", {"request": request, "error": None})
    else:
        # No one has set up yet — show email form to start setup
        return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.get("/login/email", response_class=HTMLResponse)
async def login_email_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, email: str = Form(...)):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    if not user:
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": "Email not recognized."
        })
    if user["is_setup"]:
        return templates.TemplateResponse("auth/pin.html", {
            "request": request,
            "error": None
        })
    return templates.TemplateResponse("auth/setup_pin.html", {
        "request": request,
        "email": user["email"],
        "error": None
    })


@router.post("/setup-pin", response_class=HTMLResponse)
async def setup_pin(request: Request, email: str = Form(...), pin: str = Form(...), pin_confirm: str = Form(...)):
    if pin != pin_confirm:
        return templates.TemplateResponse("auth/setup_pin.html", {
            "request": request,
            "email": email,
            "error": "PINs do not match."
        })
    if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        return templates.TemplateResponse("auth/setup_pin.html", {
            "request": request,
            "email": email,
            "error": "PIN must be 4-6 digits."
        })
    conn = get_db()
    conn.execute("UPDATE users SET pin_hash = ?, is_setup = 1 WHERE email = ?", (hash_pin(pin), email))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    token = create_session_token(user["id"])
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("session", token, max_age=30*24*3600, httponly=True, samesite="lax")
    return response


@router.post("/pin", response_class=HTMLResponse)
async def pin_submit(request: Request, pin: str = Form(...)):
    pin_hash = hash_pin(pin)
    conn = get_db()
    # Check PIN against all users
    user = conn.execute("SELECT * FROM users WHERE pin_hash = ? AND is_setup = 1", (pin_hash,)).fetchone()
    conn.close()
    if not user:
        return templates.TemplateResponse("auth/pin.html", {
            "request": request,
            "error": "Incorrect PIN."
        })
    token = create_session_token(user["id"])
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("session", token, max_age=30*24*3600, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response
