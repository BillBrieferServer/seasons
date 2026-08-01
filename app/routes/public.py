"""Public marketing site for Seasons Care Services.

DRAFT MODE: while PUBLIC_DRAFT is true the site lives under PUBLIC_PREFIX
(/preview), carries noindex headers + meta tags, and robots.txt disallows
everything. Going live = set PUBLIC_DRAFT=0 in the environment.
"""
import os
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.database.db import get_db
from app.mailer import send_inquiry

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

BASE_URL = os.environ.get("BASE_URL", "https://seasonscareservices.com")
GA_ID = os.environ.get("GA_MEASUREMENT_ID", "G-ER2SNTDBDP")
DRAFT = os.environ.get("PUBLIC_DRAFT", "1") == "1"
PREFIX = "/preview" if DRAFT else ""

router = APIRouter(prefix=PREFIX, tags=["public"])

BUSINESS = {
    "name": "Seasons Care Services",
    "legal": "Vita Nova LLC",
    "tagline": "Personal Support for Every Season",
    "phone": "208-604-2139",
    "phone_href": "+12086042139",
    "email": "julie@seasonscareservices.com",
    "owner": "Julie Brown",
    "area": "Pocatello & Chubbuck, Idaho",
}

NAV = [
    ("", "Home"),
    ("/services", "Services"),
    ("/credentials", "Credentials"),
    ("/story", "My Story"),
    ("/resources", "Resources"),
    ("/contact", "Contact"),
]


def ctx(request: Request, page: str, **extra):
    data = {
        "request": request,
        "b": BUSINESS,
        "nav": NAV,
        "p": PREFIX,
        "page": page,
        "draft": DRAFT,
        # canonical always points at the LIVE url, never the /preview one
        "canonical": BASE_URL + (page or "/"),
        "base_url": BASE_URL,
        "ga_id": GA_ID,
    }
    data.update(extra)
    return data


def render(name: str, request: Request, page: str, **extra):
    resp = templates.TemplateResponse(name, ctx(request, page, **extra))
    if DRAFT:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        # while drafting, never let a browser serve Julie a stale page
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("", response_class=HTMLResponse)
async def home(request: Request):
    return render("public/home.html", request, "")


@router.get("/services", response_class=HTMLResponse)
async def services(request: Request):
    return render("public/services.html", request, "/services")


@router.get("/services/senior-transportation", response_class=HTMLResponse)
async def service_transportation(request: Request):
    return render("public/service_transportation.html", request, "/services")


@router.get("/services/respite-care", response_class=HTMLResponse)
async def service_respite(request: Request):
    return render("public/service_respite.html", request, "/services")


@router.get("/credentials", response_class=HTMLResponse)
async def credentials(request: Request):
    return render("public/credentials.html", request, "/credentials")


@router.get("/story", response_class=HTMLResponse)
async def story(request: Request):
    return render("public/story.html", request, "/story")


@router.get("/resources", response_class=HTMLResponse)
async def resources(request: Request):
    return render("public/resources.html", request, "/resources")


@router.get("/service-area", response_class=HTMLResponse)
async def service_area(request: Request):
    return render("public/service_area.html", request, "/service-area")


@router.get("/resources/aging-parent-refuses-help", response_class=HTMLResponse)
async def resource_parent_help(request: Request):
    return render("public/resource_parent_help.html", request, "/resources")


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request):
    return render("public/contact.html", request, "/contact", sent=False)


@router.post("/contact", response_class=HTMLResponse)
async def contact_post(
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    care_for: str = Form(""),
    message: str = Form(""),
    best_time: str = Form(""),
    website: str = Form(""),
):
    # "website" is a honeypot -- real people leave it empty.
    if website.strip():
        return RedirectResponse(url=PREFIX + "/contact?sent=1", status_code=303)

    row = {
        "name": name.strip(),
        "phone": phone.strip(),
        "email": email.strip(),
        "care_for": care_for.strip(),
        "message": message.strip(),
        "best_time": best_time.strip(),
    }

    # Store first -- the database is the system of record, email is a convenience.
    conn = get_db()
    conn.execute(
        """INSERT INTO contact_submissions
           (name, phone, email, care_for, message, best_time)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (row["name"], row["phone"], row["email"],
         row["care_for"], row["message"], row["best_time"]),
    )
    conn.commit()
    conn.close()

    send_inquiry(row)
    return RedirectResponse(url=PREFIX + "/contact?sent=1", status_code=303)


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots():
    if DRAFT:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(
        "User-agent: *\nAllow: /\n"
        "Sitemap: https://seasonscareservices.com/sitemap.xml\n"
    )


SITEMAP_PATHS = ["/", "/services", "/services/senior-transportation",
                 "/services/respite-care", "/credentials", "/story",
                 "/service-area", "/resources",
                 "/resources/aging-parent-refuses-help", "/contact"]


def sitemap_xml() -> str:
    urls = "".join(
        "  <url><loc>%s%s</loc></url>\n" % (BASE_URL, p)
        for p in SITEMAP_PATHS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + urls +
        '</urlset>\n'
    )
