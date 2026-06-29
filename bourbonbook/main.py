from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.sessions import SessionMiddleware

from bourbonbook.analysis import analyze_bottle, analyze_bottle_name
from bourbonbook.auth import (
    csrf_token,
    current_user,
    hash_password,
    require_user,
    verify_csrf,
    verify_password,
)
from bourbonbook.catalog import verified_product
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import Bottle, User
from bourbonbook.photos import save_photo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=ROOT / "templates")


def money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "—"


templates.env.filters["money"] = money


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        database.create_all()
        yield
        database.engine.dispose()

    app = FastAPI(title="Bourbon Book", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.secure_cookies,
        max_age=60 * 60 * 24 * 30,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    register_routes(app)
    return app


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    context.update(request=request, csrf_token=csrf_token(request))
    return templates.TemplateResponse(request, name, context)


def parse_float(value: Any) -> float | None:
    try:
        return float(value) if str(value).strip() else None
    except (TypeError, ValueError):
        return None


def parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(float(value))))
    except (TypeError, ValueError):
        return default


TEXT_FIELDS = (
    "name",
    "brand",
    "release",
    "edition",
    "spirit_type",
    "distilled_by",
    "mash_bill",
    "size",
    "age_statement",
    "barrel_number",
    "bottle_number",
    "warehouse",
    "floor",
    "storage_location",
    "tasting_notes",
    "notes",
)


def update_bottle_from_form(bottle: Bottle, form: Any) -> None:
    for field in TEXT_FIELDS:
        setattr(bottle, field, str(form.get(field, "")).strip())
    status = str(form.get("status", "Unopened"))
    bottle.status = status if status in {"Unopened", "Opened", "Empty"} else "Unopened"
    bottle.fill_level = parse_int(form.get("fill_level"), 100, 0, 100)
    bottle.quantity = parse_int(form.get("quantity"), 1, 1, 99)
    bottle.rating = parse_int(form.get("rating"), 0, 0, 5)
    for field in ("proof", "abv", "purchase_price", "msrp", "secondary_price"):
        setattr(bottle, field, parse_float(form.get(field)))


def apply_analysis(bottle: Bottle, analysis: dict[str, Any]) -> None:
    for key, value in analysis.items():
        if hasattr(bottle, key) and value is not None:
            setattr(bottle, key, value)
    bottle.name = bottle.name or bottle.release or bottle.brand or "Untitled bottle"
    bottle.fill_level = parse_int(bottle.fill_level, 100, 0, 100)


async def enrich_bottle_by_name(
    bottle: Bottle, settings: Settings, *, allow_provider: bool = True
) -> tuple[dict[str, Any], str]:
    verified = verified_product(bottle.name)
    if verified:
        return verified, "verified"
    if not allow_provider:
        return {}, "unavailable"
    return await analyze_bottle_name(bottle.name, settings)


def owned_bottle(session: Session, user: User, bottle_id: int) -> Bottle | None:
    return session.scalar(
        select(Bottle).where(Bottle.id == bottle_id, Bottle.owner_id == user.id)
    )


def register_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            ROOT / "static" / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        with app.state.database.session_factory() as session:
            if current_user(request, session):
                return RedirectResponse("/", 303)
        return render(request, "login.html", mode="login", error=None)

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        username = str(form.get("username", "")).strip().lower()
        with app.state.database.session_factory() as session:
            user = session.scalar(select(User).where(User.username == username))
            if not user or not verify_password(str(form.get("password", "")), user.password_hash):
                return render(
                    request,
                    "login.html",
                    mode="login",
                    error="Username or password is incorrect.",
                )
            request.session.clear()
            request.session["user_id"] = user.id
        return RedirectResponse("/", 303)

    @app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request) -> Response:
        return render(request, "login.html", mode="register", error=None)

    @app.post("/register")
    async def register(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        username = str(form.get("username", "")).strip().lower()
        display_name = str(form.get("display_name", "")).strip()
        password = str(form.get("password", ""))
        error = None
        if not (3 <= len(username) <= 40) or not username.replace("_", "").isalnum():
            error = "Use 3–40 letters, numbers, or underscores for your username."
        elif len(display_name) < 2:
            error = "Please add your name."
        elif len(password) < 10:
            error = "Use a password with at least 10 characters."
        with app.state.database.session_factory() as session:
            if session.scalar(select(func.count(User.id))) >= app.state.settings.max_users:
                error = "This Bourbon Book has reached its user limit."
            elif session.scalar(select(User).where(User.username == username)):
                error = "That username is already in use."
            if error:
                return render(request, "login.html", mode="register", error=error)
            user = User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
            )
            session.add(user)
            session.commit()
            request.session.clear()
            request.session["user_id"] = user.id
        return RedirectResponse("/", 303)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        request.session.clear()
        return RedirectResponse("/login", 303)

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request, q: str = "", sort: str = "newest") -> Response:
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            statement = select(Bottle).where(Bottle.owner_id == user.id)
            if q.strip():
                term = f"%{q.strip()}%"
                statement = statement.where(
                    or_(
                        Bottle.name.ilike(term),
                        Bottle.brand.ilike(term),
                        Bottle.release.ilike(term),
                    )
                )
            orders = {
                "name": Bottle.name.asc(),
                "value": Bottle.secondary_price.desc().nullslast(),
                "oldest": Bottle.created_at.asc(),
                "newest": Bottle.created_at.desc(),
            }
            bottles = list(session.scalars(statement.order_by(orders.get(sort, orders["newest"]))))
            all_bottles = list(session.scalars(select(Bottle).where(Bottle.owner_id == user.id)))
            bottle_count = sum(bottle.quantity for bottle in all_bottles)
            total_value = sum(bottle.estimated_value for bottle in all_bottles)
            return render(
                request,
                "library.html",
                user=user,
                bottles=bottles,
                bottle_count=bottle_count,
                total_value=total_value,
                q=q,
                sort=sort,
            )

    @app.get("/bottles/new", response_class=HTMLResponse)
    def new_bottle(request: Request) -> Response:
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            return render(request, "new.html", user=user)

    @app.post("/bottles")
    async def add_bottle(
        request: Request,
        photo: Annotated[UploadFile, File()],
        purchase_price: Annotated[str, Form()] = "",
        quantity: Annotated[str, Form()] = "1",
        csrf: Annotated[str, Form(alias="csrf_token")] = "",
    ) -> Response:
        verify_csrf(request, csrf)
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            photo_name = await save_photo(
                photo, app.state.settings.data_dir / "uploads", app.state.settings.max_upload_mb
            )
            analysis, analysis_status = await analyze_bottle(
                app.state.settings.data_dir / "uploads" / photo_name, app.state.settings
            )
            bottle = Bottle(
                owner_id=user.id,
                photo_name=photo_name,
                analysis_status=analysis_status,
            )
            apply_analysis(bottle, analysis)
            if bottle.name and bottle.name != "Untitled bottle":
                enrichment, enrichment_status = await enrich_bottle_by_name(
                    bottle, app.state.settings, allow_provider=False
                )
                apply_analysis(bottle, enrichment)
                if enrichment:
                    bottle.analysis_status = enrichment_status
            bottle.purchase_price = parse_float(purchase_price)
            bottle.quantity = parse_int(quantity, 1, 1, 99)
            session.add(bottle)
            session.commit()
            return RedirectResponse(f"/bottles/{bottle.id}/edit?new=1", 303)

    @app.get("/bottles/{bottle_id}", response_class=HTMLResponse)
    def bottle_detail(request: Request, bottle_id: int) -> Response:
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            return render(request, "detail.html", user=user, bottle=bottle)

    @app.get("/bottles/{bottle_id}/edit", response_class=HTMLResponse)
    def bottle_edit(
        request: Request, bottle_id: int, new: int = 0, analysis: str = ""
    ) -> Response:
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            return render(
                request,
                "edit.html",
                user=user,
                bottle=bottle,
                is_new=bool(new),
                analysis_result=analysis,
            )

    @app.post("/bottles/{bottle_id}/edit")
    async def save_bottle(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            update_bottle_from_form(bottle, form)
            upload = form.get("photo")
            if isinstance(upload, StarletteUploadFile) and upload.filename:
                old_photo = bottle.photo_name
                bottle.photo_name = await save_photo(
                    upload,
                    app.state.settings.data_dir / "uploads",
                    app.state.settings.max_upload_mb,
                )
                if old_photo:
                    (app.state.settings.data_dir / "uploads" / old_photo).unlink(missing_ok=True)
            session.commit()
        return RedirectResponse(f"/bottles/{bottle_id}", 303)

    @app.post("/bottles/{bottle_id}/analyze")
    async def refresh_bottle_analysis(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        mode = str(form.get("analysis_mode", "photo"))
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            update_bottle_from_form(bottle, form)
            upload = form.get("photo")
            if isinstance(upload, StarletteUploadFile) and upload.filename:
                old_photo = bottle.photo_name
                bottle.photo_name = await save_photo(
                    upload,
                    app.state.settings.data_dir / "uploads",
                    app.state.settings.max_upload_mb,
                )
                if old_photo:
                    (app.state.settings.data_dir / "uploads" / old_photo).unlink(missing_ok=True)
            if mode == "name":
                analysis, analysis_status = await enrich_bottle_by_name(
                    bottle, app.state.settings
                )
            elif bottle.photo_name:
                analysis, analysis_status = await analyze_bottle(
                    app.state.settings.data_dir / "uploads" / bottle.photo_name,
                    app.state.settings,
                )
            else:
                analysis, analysis_status = {}, "unavailable"
            apply_analysis(bottle, analysis)
            if mode == "photo" and bottle.name:
                enrichment, enrichment_status = await enrich_bottle_by_name(
                    bottle, app.state.settings, allow_provider=False
                )
                apply_analysis(bottle, enrichment)
                if enrichment:
                    analysis_status = enrichment_status
            bottle.analysis_status = analysis_status
            session.commit()
        return RedirectResponse(
            f"/bottles/{bottle_id}/edit?analysis={analysis_status}", 303
        )

    @app.post("/bottles/{bottle_id}/delete")
    async def delete_bottle(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if bottle:
                if bottle.photo_name:
                    (app.state.settings.data_dir / "uploads" / bottle.photo_name).unlink(
                        missing_ok=True
                    )
                session.delete(bottle)
                session.commit()
        return RedirectResponse("/", 303)

    @app.get("/media/{photo_name}")
    def photo(request: Request, photo_name: str) -> Response:
        with app.state.database.session_factory() as session:
            user = require_user(request, session)
            bottle = session.scalar(
                select(Bottle).where(Bottle.owner_id == user.id, Bottle.photo_name == photo_name)
            )
            path = app.state.settings.data_dir / "uploads" / photo_name
            if not bottle or not path.is_file():
                return Response(status_code=404)
            return FileResponse(
                path,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=86400"},
            )


app = create_app()
