from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
import os

from app.database.db import init_db
from app.routes.recipes import router as recipes_router
from app.routes.plans import router as plans_router

app = FastAPI(title="Seasons Care Services", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(recipes_router)
app.include_router(plans_router)


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
