from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import hashlib
import secrets
import time
import hmac as hmac_mod

from app.database.db import get_db

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

SECRET_KEY = None

# Brute-force protection: track failed attempts by IP
_failed_attempts = {}  # ip -> {"count": int, "lockout_until": float}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes


def get_secret():
    global SECRET_KEY
    if SECRET_KEY is None:
        import os
        SECRET_KEY = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
    return SECRET_KEY


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def create_session_token(user_id: int) -> str:
    ts = str(int(time.time()))
    payload = f"{user_id}:{ts}"
    sig = hmac_mod.new(get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str):
    try:
        parts = token.rsplit(":", 1)
        if len(parts) != 2:
            return None
        payload, sig = parts
        expected = hmac_mod.new(get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac_mod.compare_digest(sig, expected):
            return None
        user_id_str, ts = payload.split(":", 1)
        if time.time() - int(ts) > 30 * 24 * 3600:
            return None
        return int(user_id_str)
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


def generate_csrf_token() -> str:
    return secrets.token_hex(32)


def make_csrf_signature(token: str) -> str:
    return hmac_mod.new(get_secret().encode(), token.encode(), hashlib.sha256).hexdigest()[:32]


def validate_csrf(request_token: str, request_sig: str) -> bool:
    if not request_token or not request_sig:
        return False
    expected_sig = make_csrf_signature(request_token)
    return hmac_mod.compare_digest(expected_sig, request_sig)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(ip: str) -> str | None:
    """Returns error message if rate limited, None if OK."""
    now = time.time()
    entry = _failed_attempts.get(ip)
    if entry and now < entry.get("lockout_until", 0):
        remaining = int(entry["lockout_until"] - now)
        return f"Too many failed attempts. Try again in {remaining} seconds."
    return None


def record_failed_attempt(ip: str):
    now = time.time()
    entry = _failed_attempts.get(ip, {"count": 0, "lockout_until": 0})
    # Reset if lockout expired
    if now > entry.get("lockout_until", 0) and entry["count"] >= MAX_ATTEMPTS:
        entry = {"count": 0, "lockout_until": 0}
    entry["count"] = entry.get("count", 0) + 1
    if entry["count"] >= MAX_ATTEMPTS:
        entry["lockout_until"] = now + LOCKOUT_SECONDS
    _failed_attempts[ip] = entry


def clear_failed_attempts(ip: str):
    _failed_attempts.pop(ip, None)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user and user["is_setup"]:
        return RedirectResponse(url="/", status_code=302)
    conn = get_db()
    setup_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_setup = 1").fetchone()["c"]
    conn.close()
    if setup_count > 0:
        csrf = generate_csrf_token()
        csrf_sig = make_csrf_signature(csrf)
        return templates.TemplateResponse("auth/pin.html", {
            "request": request, "error": None,
            "csrf_token": csrf, "csrf_sig": csrf_sig,
        })
    else:
        csrf = generate_csrf_token()
        csrf_sig = make_csrf_signature(csrf)
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": None,
            "csrf_token": csrf, "csrf_sig": csrf_sig,
        })


@router.get("/login/email", response_class=HTMLResponse)
async def login_email_page(request: Request):
    csrf = generate_csrf_token()
    csrf_sig = make_csrf_signature(csrf)
    return templates.TemplateResponse("auth/login.html", {
        "request": request, "error": None,
        "csrf_token": csrf, "csrf_sig": csrf_sig,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, email: str = Form(...),
                       csrf_token: str = Form(""), csrf_sig: str = Form("")):
    if not validate_csrf(csrf_token, csrf_sig):
        csrf = generate_csrf_token()
        csrf_sig_new = make_csrf_signature(csrf)
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Invalid request. Please try again.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()

    csrf = generate_csrf_token()
    csrf_sig_new = make_csrf_signature(csrf)

    if not user:
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Email not recognized.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })
    if user["is_setup"]:
        return templates.TemplateResponse("auth/pin.html", {
            "request": request, "error": None,
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })
    # Generate a one-time setup token tied to this email
    setup_token = hmac_mod.new(get_secret().encode(), email.lower().strip().encode(), hashlib.sha256).hexdigest()[:32]
    return templates.TemplateResponse("auth/setup_pin.html", {
        "request": request, "email": user["email"], "error": None,
        "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        "setup_token": setup_token,
    })


@router.post("/setup-pin", response_class=HTMLResponse)
async def setup_pin(request: Request, email: str = Form(...), pin: str = Form(...),
                    pin_confirm: str = Form(...), setup_token: str = Form(""),
                    csrf_token: str = Form(""), csrf_sig: str = Form("")):
    csrf = generate_csrf_token()
    csrf_sig_new = make_csrf_signature(csrf)

    if not validate_csrf(csrf_token, csrf_sig):
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Invalid request. Please try again.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    # Verify setup token matches this email
    expected_token = hmac_mod.new(get_secret().encode(), email.lower().strip().encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac_mod.compare_digest(setup_token, expected_token):
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Invalid setup link. Please start over.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    # Verify user exists and hasn't already set up
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ? AND is_setup = 0", (email.lower().strip(),)).fetchone()
    if not user:
        conn.close()
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Account already set up. Use your PIN to sign in.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    if pin != pin_confirm:
        conn.close()
        return templates.TemplateResponse("auth/setup_pin.html", {
            "request": request, "email": email, "error": "PINs do not match.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new, "setup_token": setup_token,
        })
    if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        conn.close()
        return templates.TemplateResponse("auth/setup_pin.html", {
            "request": request, "email": email, "error": "PIN must be 4-6 digits.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new, "setup_token": setup_token,
        })

    conn.execute("UPDATE users SET pin_hash = ?, is_setup = 1 WHERE email = ?", (hash_pin(pin), email))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    token = create_session_token(user["id"])
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("session", token, max_age=30*24*3600, httponly=True, samesite="lax", secure=True)
    return response


@router.post("/pin", response_class=HTMLResponse)
async def pin_submit(request: Request, pin: str = Form(...),
                     csrf_token: str = Form(""), csrf_sig: str = Form("")):
    csrf = generate_csrf_token()
    csrf_sig_new = make_csrf_signature(csrf)

    if not validate_csrf(csrf_token, csrf_sig):
        return templates.TemplateResponse("auth/pin.html", {
            "request": request, "error": "Invalid request. Please try again.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    ip = get_client_ip(request)
    rate_error = check_rate_limit(ip)
    if rate_error:
        return templates.TemplateResponse("auth/pin.html", {
            "request": request, "error": rate_error,
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    pin_hash = hash_pin(pin)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE pin_hash = ? AND is_setup = 1", (pin_hash,)).fetchone()
    conn.close()

    if not user:
        record_failed_attempt(ip)
        return templates.TemplateResponse("auth/pin.html", {
            "request": request, "error": "Incorrect PIN.",
            "csrf_token": csrf, "csrf_sig": csrf_sig_new,
        })

    clear_failed_attempts(ip)
    token = create_session_token(user["id"])
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("session", token, max_age=30*24*3600, httponly=True, samesite="lax", secure=True)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response
