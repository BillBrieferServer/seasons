from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
import os

from app.database.db import init_db
from app.routes.recipes import router as recipes_router
from app.routes.plans import router as plans_router
from app.routes.shopping import router as shopping_router
from app.routes.clients import router as clients_router
from app.routes.auth import router as auth_router, get_current_user, generate_csrf_token, make_csrf_signature
from app.routes.admin import router as admin_router

app = FastAPI(title="Seasons Care Services", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Auth routes (no login required)
app.include_router(auth_router)

# Protected routes
app.include_router(recipes_router)
app.include_router(plans_router)
app.include_router(shopping_router)
app.include_router(clients_router)
app.include_router(admin_router)

PUBLIC_PATHS = {"/login", "/setup-pin", "/pin", "/health", "/static"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Allow public paths
    if any(path.startswith(p) for p in PUBLIC_PATHS):
        return await call_next(request)
    # Check auth
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    # Inject CSRF token into request state for templates
    csrf = generate_csrf_token()
    request.state.csrf_token = csrf
    request.state.csrf_sig = make_csrf_signature(csrf)
    return await call_next(request)


@app.on_event("startup")
async def startup():
    db_path = os.environ.get("DATABASE_URL", "/app/data/seasons.db")
    init_db(db_path)


@app.get("/")
async def home():
    return RedirectResponse(url="/recipes", status_code=302)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "seasons-care-services"}
