from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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

from bourbonbook.analysis import analyze_bottle, analyze_bottle_name, search_bottle_prices
from bourbonbook.auth import (
    authenticate_session,
    csrf_token,
    current_user,
    hash_password,
    normalize_email,
    require_verified_user,
    validate_password,
    verify_csrf,
    verify_password,
)
from bourbonbook.catalog import verified_product
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.email import create_email_sender, security_message
from bourbonbook.identity import bootstrap_admin, issue_reset, issue_verification
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import Bottle, PriceSource, User, UserToken
from bourbonbook.photos import save_photo
from bourbonbook.rate_limit import RateLimiter
from bourbonbook.tokens import (
    RESET_PASSWORD,
    VERIFY_EMAIL,
    consume_token,
    find_valid_token,
    revoke_tokens,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

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
        settings.validate_identity()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        bootstrap_database(settings)
        with database.session_factory() as session:
            await bootstrap_admin(session, settings, app.state.email_sender)
        yield
        database.engine.dispose()

    app = FastAPI(title="Bourbon Book", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.email_sender = create_email_sender(settings)
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_secret or settings.session_secret,
        limit=settings.rate_limit_attempts,
        window=settings.rate_limit_window_seconds,
        global_limit=settings.rate_limit_global_attempts,
    )
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
    status_code = context.pop("status_code", 200)
    context.update(request=request, csrf_token=csrf_token(request))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


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


def apply_price_search(
    bottle: Bottle, prices: dict[str, float], sources: list[dict[str, str]]
) -> None:
    for field in ("msrp", "secondary_price"):
        if field in prices:
            setattr(bottle, field, prices[field])
    if sources:
        refreshed_kinds = {source["kind"] for source in sources}
        for existing in list(bottle.price_sources):
            if existing.kind in refreshed_kinds:
                bottle.price_sources.remove(existing)
        bottle.price_sources.extend(PriceSource(**source) for source in sources)


async def refresh_prices(bottle: Bottle, settings: Settings) -> str:
    if not bottle.name or bottle.name == "Untitled bottle":
        return "unavailable"
    prices, sources, status = await search_bottle_prices(bottle.name, settings)
    apply_price_search(bottle, prices, sources)
    return status


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
    return session.scalar(select(Bottle).where(Bottle.id == bottle_id, Bottle.owner_id == user.id))


def register_routes(app: FastAPI) -> None:
    def limited(request: Request, operation: str, email: str) -> bool:
        client_ip = request.client.host if request.client else "unknown"
        return app.state.rate_limiter.allow(operation, email, client_ip)

    def too_many(request: Request, mode: str) -> HTMLResponse:
        return render(
            request,
            "login.html",
            mode=mode,
            error="Too many attempts. Please wait a few minutes and try again.",
            status_code=429,
        )

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
        email_text = str(form.get("email", ""))
        try:
            email = normalize_email(email_text)
        except ValueError:
            email = email_text.strip().lower()
        if not limited(request, "login", email):
            return too_many(request, "login")
        with app.state.database.session_factory() as session:
            user = session.scalar(select(User).where(User.email == email))
            if not user or not verify_password(str(form.get("password", "")), user.password_hash):
                return render(
                    request,
                    "login.html",
                    mode="login",
                    error="Email or password is incorrect.",
                )
            if not user.email:
                return render(
                    request,
                    "login.html",
                    mode="login",
                    error="Contact an administrator to update this legacy account.",
                )
            if not user.email_verified_at:
                request.session.clear()
                request.session["unverified_user_id"] = user.id
                return RedirectResponse("/check-email", 303)
            authenticate_session(request, user)
        return RedirectResponse("/", 303)

    @app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request) -> Response:
        return render(request, "login.html", mode="register", error=None)

    @app.post("/register")
    async def register(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        email_text = str(form.get("email", ""))
        screen_name = str(form.get("screen_name", "")).strip()
        password = str(form.get("password", ""))
        error = None
        try:
            email = normalize_email(email_text)
            validate_password(password)
        except ValueError as exc:
            email = email_text.strip().lower()
            error = str(exc)
        if len(screen_name) > 80:
            error = "Screen name must be 80 characters or fewer."
        if not limited(request, "register", email):
            return too_many(request, "register")
        with app.state.database.session_factory() as session:
            if session.scalar(select(func.count(User.id))) >= app.state.settings.max_users:
                error = "This Bourbon Book has reached its user limit."
            elif session.scalar(select(User).where(User.email == email)):
                error = "That email is already in use."
            if error:
                return render(request, "login.html", mode="register", error=error)
            user = User(
                username=email,
                display_name=screen_name or email.split("@", 1)[0],
                email=email,
                screen_name=screen_name or email.split("@", 1)[0],
                password_hash=hash_password(password),
            )
            session.add(user)
            session.flush()
            request.session.clear()
            request.session["unverified_user_id"] = user.id
            try:
                await issue_verification(session, user, app.state.settings, app.state.email_sender)
            except Exception:
                logger.exception("Verification email delivery failed user_id=%s", user.id)
                return render(request, "check_email.html", delivery_error=True, status_code=503)
        return RedirectResponse("/check-email", 303)

    @app.get("/check-email", response_class=HTMLResponse)
    def check_email(request: Request) -> Response:
        return render(request, "check_email.html", delivery_error=False)

    @app.post("/verification/resend")
    async def resend_verification(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        email_text = str(form.get("email", "")).strip()
        user_id = request.session.get("unverified_user_id")
        with app.state.database.session_factory() as session:
            user = session.get(User, user_id) if user_id else None
            try:
                email = user.email if user and user.email else normalize_email(email_text)
            except ValueError:
                email = email_text.lower()
            if not limited(request, "resend", email):
                return render(request, "check_email.html", delivery_error=False, status_code=429)
            if not user and email:
                user = session.scalar(select(User).where(User.email == email))
            if user and user.email and not user.email_verified_at:
                try:
                    await issue_verification(
                        session, user, app.state.settings, app.state.email_sender
                    )
                except Exception:
                    logger.exception("Verification email delivery failed user_id=%s", user.id)
                    return render(request, "check_email.html", delivery_error=True, status_code=503)
        return render(request, "check_email.html", delivery_error=False, resent=True)

    @app.get("/verify-email")
    def stage_verification(request: Request, token: str) -> Response:
        with app.state.database.session_factory() as session:
            found = find_valid_token(session, token, VERIFY_EMAIL)
            request.session.pop("pending_verification_id", None)
            if found:
                request.session["pending_verification_id"] = found.id
                request.session["pending_verification_expires"] = int(time.time()) + 600
        return RedirectResponse(
            "/verify-email/confirm",
            303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.get("/verify-email/confirm", response_class=HTMLResponse)
    def verification_confirmation(request: Request) -> Response:
        valid = False
        token_id = request.session.get("pending_verification_id")
        if request.session.get("pending_verification_expires", 0) < time.time():
            request.session.pop("pending_verification_id", None)
            token_id = None
        with app.state.database.session_factory() as session:
            token = session.get(UserToken, token_id) if token_id else None
            valid = bool(token and token.purpose == VERIFY_EMAIL and token.used_at is None)
        response = render(request, "verify_email.html", valid=valid)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    @app.post("/verify-email/confirm")
    async def confirm_verification(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        token_id = request.session.pop("pending_verification_id", None)
        request.session.pop("pending_verification_expires", None)
        if not limited(request, "verify", str(token_id or "missing")):
            return render(request, "verify_email.html", valid=False, status_code=429)
        with app.state.database.session_factory() as session:
            token = consume_token(session, token_id, VERIFY_EMAIL) if token_id else None
            user = session.get(User, token.user_id) if token else None
            if not token or not user or not user.email or token.email_snapshot != user.email:
                session.rollback()
                return render(request, "verify_email.html", valid=False, status_code=400)
            user.email_verified_at = datetime.now(UTC)
            session.commit()
            authenticate_session(request, user)
        return RedirectResponse("/profile", 303)

    @app.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password_page(request: Request) -> Response:
        return render(request, "forgot_password.html", sent=False)

    @app.post("/forgot-password")
    async def forgot_password(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        email_text = str(form.get("email", "")).strip()
        try:
            email = normalize_email(email_text)
        except ValueError:
            email = email_text.lower()
        if not limited(request, "forgot", email):
            return render(request, "forgot_password.html", sent=True, status_code=429)
        with app.state.database.session_factory() as session:
            user = session.scalar(select(User).where(User.email == email)) if email else None
            if user and user.email:
                try:
                    await issue_reset(session, user, app.state.settings, app.state.email_sender)
                except Exception:
                    logger.exception("Password reset email delivery failed user_id=%s", user.id)
        return render(request, "forgot_password.html", sent=True)

    @app.get("/reset-password")
    def reset_password_page(request: Request, token: str | None = None) -> Response:
        if token:
            with app.state.database.session_factory() as session:
                found = find_valid_token(session, token, RESET_PASSWORD)
                request.session.pop("pending_reset_id", None)
                if found:
                    request.session["pending_reset_id"] = found.id
                    request.session["pending_reset_expires"] = int(time.time()) + 600
            return RedirectResponse(
                "/reset-password",
                303,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        if request.session.get("pending_reset_expires", 0) < time.time():
            request.session.pop("pending_reset_id", None)
        response = render(
            request,
            "reset_password.html",
            valid=bool(request.session.get("pending_reset_id")),
            error=None,
        )
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    @app.post("/reset-password")
    async def reset_password(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        password = str(form.get("password", ""))
        if password != str(form.get("password_confirmation", "")):
            return render(
                request, "reset_password.html", valid=True, error="Passwords do not match."
            )
        try:
            validate_password(password)
        except ValueError as exc:
            return render(request, "reset_password.html", valid=True, error=str(exc))
        token_id = request.session.pop("pending_reset_id", None)
        request.session.pop("pending_reset_expires", None)
        if not limited(request, "reset", str(token_id or "missing")):
            return render(
                request,
                "reset_password.html",
                valid=False,
                error="Too many attempts.",
                status_code=429,
            )
        with app.state.database.session_factory() as session:
            token = consume_token(session, token_id, RESET_PASSWORD) if token_id else None
            user = session.get(User, token.user_id) if token else None
            if not token or not user or token.email_snapshot != user.email:
                session.rollback()
                return render(
                    request,
                    "reset_password.html",
                    valid=False,
                    error="This reset link is invalid or expired.",
                    status_code=400,
                )
            user.password_hash = hash_password(password)
            user.session_version += 1
            revoke_tokens(session, user.id, RESET_PASSWORD)
            session.commit()
            request.session.clear()
            try:
                await app.state.email_sender.send(security_message(user.email or ""))
            except Exception:
                logger.exception("Password change notification failed user_id=%s", user.id)
        return RedirectResponse("/login", 303)

    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request) -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            return render(request, "profile.html", user=user, error=None, notice=None)

    @app.post("/profile/name")
    async def update_profile_name(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        screen_name = str(form.get("screen_name", "")).strip()
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            if not 1 <= len(screen_name) <= 80:
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="Screen name must be 1–80 characters.",
                    notice=None,
                )
            user.screen_name = screen_name
            user.display_name = screen_name
            session.commit()
            return render(
                request, "profile.html", user=user, error=None, notice="Screen name updated."
            )

    @app.post("/profile/email")
    async def update_profile_email(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            if not verify_password(str(form.get("current_password", "")), user.password_hash):
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="Current password is incorrect.",
                    notice=None,
                )
            try:
                email = normalize_email(str(form.get("email", "")))
            except ValueError as exc:
                return render(request, "profile.html", user=user, error=str(exc), notice=None)
            duplicate = session.scalar(select(User).where(User.email == email, User.id != user.id))
            if duplicate:
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="That email is already in use.",
                    notice=None,
                )
            user.email = email
            user.username = email
            user.email_verified_at = None
            user.session_version += 1
            revoke_tokens(session, user.id)
            session.commit()
            authenticate_session(request, user)
            request.session["unverified_user_id"] = user.id
            try:
                await issue_verification(session, user, app.state.settings, app.state.email_sender)
            except Exception:
                logger.exception("Verification email delivery failed user_id=%s", user.id)
                return render(request, "check_email.html", delivery_error=True, status_code=503)
        return RedirectResponse("/check-email", 303)

    @app.post("/profile/password")
    async def update_profile_password(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            new_password = str(form.get("new_password", ""))
            if not verify_password(str(form.get("current_password", "")), user.password_hash):
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="Current password is incorrect.",
                    notice=None,
                )
            if new_password != str(form.get("password_confirmation", "")):
                return render(
                    request, "profile.html", user=user, error="Passwords do not match.", notice=None
                )
            try:
                validate_password(new_password)
            except ValueError as exc:
                return render(request, "profile.html", user=user, error=str(exc), notice=None)
            user.password_hash = hash_password(new_password)
            user.session_version += 1
            revoke_tokens(session, user.id, RESET_PASSWORD)
            session.commit()
            request.session.clear()
        return RedirectResponse("/login", 303)

    @app.post("/profile/delete")
    async def delete_profile(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            if (
                not verify_password(str(form.get("current_password", "")), user.password_hash)
                or str(form.get("confirmation", "")) != "DELETE MY ACCOUNT"
            ):
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="Password and exact confirmation phrase are required.",
                    notice=None,
                )
            upload_root = (app.state.settings.data_dir / "uploads").resolve()
            photo_paths = []
            for bottle in user.bottles:
                if bottle.photo_name:
                    candidate = (upload_root / bottle.photo_name).resolve()
                    if candidate.parent == upload_root:
                        photo_paths.append(candidate)
            session.delete(user)
            session.commit()
            for path in photo_paths:
                path.unlink(missing_ok=True)
            request.session.clear()
        return RedirectResponse("/account-deleted", 303)

    @app.get("/account-deleted", response_class=HTMLResponse)
    def account_deleted(request: Request) -> Response:
        return render(request, "account_deleted.html")

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        request.session.clear()
        return RedirectResponse("/login", 303)

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request, q: str = "", sort: str = "newest") -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
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
            user = require_verified_user(request, session)
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
            user = require_verified_user(request, session)
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
                price_status = await refresh_prices(bottle, app.state.settings)
                if price_status == "complete":
                    bottle.analysis_status = price_status
            bottle.purchase_price = parse_float(purchase_price)
            bottle.quantity = parse_int(quantity, 1, 1, 99)
            session.add(bottle)
            session.commit()
            return RedirectResponse(f"/bottles/{bottle.id}/edit?new=1", 303)

    @app.get("/bottles/{bottle_id}", response_class=HTMLResponse)
    def bottle_detail(request: Request, bottle_id: int) -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            return render(request, "detail.html", user=user, bottle=bottle)

    @app.get("/bottles/{bottle_id}/edit", response_class=HTMLResponse)
    def bottle_edit(request: Request, bottle_id: int, new: int = 0, analysis: str = "") -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
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
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if not bottle:
                return RedirectResponse("/", 303)
            previous_prices = {"msrp": bottle.msrp, "secondary": bottle.secondary_price}
            update_bottle_from_form(bottle, form)
            current_prices = {"msrp": bottle.msrp, "secondary": bottle.secondary_price}
            for source in list(bottle.price_sources):
                if previous_prices[source.kind] != current_prices[source.kind]:
                    bottle.price_sources.remove(source)
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
            user = require_verified_user(request, session)
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
            if mode == "price":
                analysis, analysis_status = {}, await refresh_prices(bottle, app.state.settings)
            elif mode == "name":
                analysis, analysis_status = await enrich_bottle_by_name(bottle, app.state.settings)
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
            if mode in {"name", "photo"} and bottle.name:
                price_status = await refresh_prices(bottle, app.state.settings)
                if price_status == "complete":
                    analysis_status = price_status
            bottle.analysis_status = analysis_status
            session.commit()
        return RedirectResponse(f"/bottles/{bottle_id}/edit?analysis={analysis_status}", 303)

    @app.post("/bottles/{bottle_id}/delete")
    async def delete_bottle(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
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
            user = require_verified_user(request, session)
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
