from __future__ import annotations

import logging
import os
import secrets
import signal
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import AsyncOpenAI
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.sessions import SessionMiddleware

from bourbonbook.admin_config import (
    CONFIG_FIELDS,
    managed_config_path,
    parse_config_form,
    settings_values,
    write_managed_config,
)
from bourbonbook.analysis import analyze_bottle, analyze_bottle_name, search_bottle_prices
from bourbonbook.auth import (
    authenticate_session,
    csrf_token,
    current_user,
    hash_password,
    normalize_email,
    require_admin,
    require_verified_user,
    validate_password,
    verify_csrf,
    verify_password,
)
from bourbonbook.catalog import catalog_price_key, verified_product
from bourbonbook.catalog_import_worker import CatalogImportWorker
from bourbonbook.catalog_imports import (
    CatalogImportApplyStateError,
    apply_catalog_import_batch,
    reserve_catalog_import_batch,
)
from bourbonbook.catalog_uploads import (
    cleanup_expired_catalog_import_sources,
    remove_catalog_import_batch_sources,
    stage_catalog_uploads,
    validate_catalog_uploads,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.email import create_email_sender, security_message
from bourbonbook.identity import bootstrap_admin, issue_reset, issue_verification
from bourbonbook.logging_config import (
    REQUEST_ID_HEADER,
    configure_logging,
    log_event,
    request_id_var,
    valid_request_id,
)
from bourbonbook.migrations import HEAD_REVISION, bootstrap_database
from bourbonbook.models import (
    ApiUsage,
    Bottle,
    CatalogImportBatch,
    CatalogImportProposal,
    CatalogPrice,
    PriceSource,
    User,
    UserToken,
)
from bourbonbook.observability import (
    HTTP_IN_PROGRESS,
    AIUsageRecorder,
    ObservedEmailSender,
    metrics_response,
    observe_auth_event,
    observe_http,
    route_template,
    usage_context,
)
from bourbonbook.photos import save_avatar, save_photo
from bourbonbook.provider_clients import (
    reset_shared_ollama_client,
    reset_shared_openai_client,
    set_shared_ollama_client,
    set_shared_openai_client,
)
from bourbonbook.qdrant_prices import QdrantPriceIndex
from bourbonbook.rate_limit import RateLimiter
from bourbonbook.tokens import (
    RESET_PASSWORD,
    VERIFY_EMAIL,
    consume_token,
    find_valid_token,
    revoke_tokens,
    token_digest,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=ROOT / "templates")
PRICE_CACHE_TTL = timedelta(days=90)
USER_PRICE_OVERRIDE_TTL = timedelta(days=183)
DELETABLE_CATALOG_IMPORT_STATES = frozenset({"queued", "failed", "review"})


def money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "—"


templates.env.filters["money"] = money


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings)
    database = Database(settings)
    usage_recorder = AIUsageRecorder(
        database.session_factory,
        retention_days=settings.api_usage_retention_days,
        metrics_enabled=settings.metrics_enabled,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_event(logger, logging.INFO, "app_starting", "Bourbon Book starting")
        settings.validate_identity()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        cleanup_expired_catalog_import_sources(settings)
        async with AsyncExitStack() as stack:
            app.state.openai_client = None
            if settings.openai_api_key:
                app.state.openai_client = await stack.enter_async_context(
                    AsyncOpenAI(api_key=settings.openai_api_key, timeout=120.0)
                )
            app.state.ollama_client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=120)
            )
            app.state.qdrant_price_index = QdrantPriceIndex(settings)
            stack.push_async_callback(app.state.qdrant_price_index.close)
            bootstrap_database(settings)
            await app.state.qdrant_price_index.ensure_collection()
            removed = app.state.usage_recorder.cleanup_old_records()
            if removed:
                log_event(
                    logger,
                    logging.INFO,
                    "usage_retention_cleanup",
                    "Old API usage records removed",
                    removed=removed,
                )
            with database.session_factory() as session:
                await bootstrap_admin(session, settings, app.state.email_sender)
            app.state.catalog_import_worker = CatalogImportWorker(
                database.session_factory, settings, app.state.ollama_client
            )
            await app.state.catalog_import_worker.start()
            stack.push_async_callback(app.state.catalog_import_worker.stop)
            yield
        log_event(logger, logging.INFO, "app_stopping", "Bourbon Book stopping")
        database.engine.dispose()

    app = FastAPI(title="Bourbon Book", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.usage_recorder = usage_recorder
    app.state.qdrant_price_index = None
    app.state.catalog_import_worker = None
    app.state.email_sender = ObservedEmailSender(
        create_email_sender(settings), metrics_enabled=settings.metrics_enabled
    )
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_secret or settings.session_secret,
        limit=settings.rate_limit_attempts,
        window=settings.rate_limit_window_seconds,
        global_limit=settings.rate_limit_global_attempts,
    )
    app.state.restart = lambda: os.kill(os.getpid(), signal.SIGTERM)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.secure_cookies,
        max_age=60 * 60 * 24 * 30,
    )

    @app.middleware("http")
    async def provider_client_context(request: Request, call_next):
        tokens = []
        openai_client = getattr(request.app.state, "openai_client", None)
        ollama_client = getattr(request.app.state, "ollama_client", None)
        if openai_client is not None:
            tokens.append(set_shared_openai_client(openai_client))
        if ollama_client is not None:
            tokens.append(set_shared_ollama_client(ollama_client))
        try:
            return await call_next(request)
        finally:
            if ollama_client is not None:
                reset_shared_ollama_client(tokens.pop())
            if openai_client is not None:
                reset_shared_openai_client(tokens.pop())

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.mount("/images", StaticFiles(directory=ROOT.parent / "images"), name="images")
    register_observability(app)
    register_routes(app)
    return app


def register_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = inbound if valid_request_id(inbound) else uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        method = request.method
        HTTP_IN_PROGRESS.labels(method, "pending").inc()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            template = route_template(request)
            observe_http(method, template, 500, duration)
            log_event(
                logger,
                logging.ERROR,
                "request_exception",
                "Unhandled request exception",
                method=method,
                route=template,
                status=500,
                duration_ms=round(duration * 1000),
                exc_info=True,
            )
            raise
        finally:
            HTTP_IN_PROGRESS.labels(method, "pending").dec()
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        if request.url.path != "/metrics":
            duration = time.perf_counter() - start
            template = route_template(request)
            observe_http(method, template, response.status_code, duration)
            log_event(
                logger,
                logging.INFO,
                "request_completed",
                "Request completed",
                method=method,
                route=template,
                status=response.status_code,
                duration_ms=round(duration * 1000),
                request_id=request_id,
            )
        return response


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


def delete_catalog_import_batch(session: Session, batch_id: int) -> bool:
    """Delete only a batch that has not been claimed or finalized by the worker."""
    result = session.execute(
        delete(CatalogImportBatch).where(
            CatalogImportBatch.id == batch_id,
            CatalogImportBatch.state.in_(DELETABLE_CATALOG_IMPORT_STATES),
        )
    )
    return bool(result.rowcount)


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
    for field in ("proof", "abv", "purchase_price", "msrp"):
        setattr(bottle, field, parse_float(form.get(field)))


def apply_analysis(bottle: Bottle, analysis: dict[str, Any]) -> None:
    for key, value in analysis.items():
        if key != "msrp" and hasattr(bottle, key) and value is not None:
            setattr(bottle, key, value)
    bottle.name = bottle.name or bottle.release or bottle.brand or "Untitled bottle"
    bottle.fill_level = parse_int(bottle.fill_level, 100, 0, 100)


def apply_price_search(
    bottle: Bottle, prices: dict[str, float], sources: list[dict[str, Any]]
) -> None:
    for field in ("msrp",):
        if field in prices:
            setattr(bottle, field, prices[field])
    if sources:
        refreshed_kinds = {source["kind"] for source in sources}
        for existing in list(bottle.price_sources):
            if existing.kind in refreshed_kinds:
                bottle.price_sources.remove(existing)
        bottle.price_sources.extend(PriceSource(**source) for source in sources)


def catalog_price_is_fresh(price: CatalogPrice) -> bool:
    checked_at = price.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return checked_at >= datetime.now(UTC) - PRICE_CACHE_TTL


def catalog_price_needs_user_update(price: CatalogPrice | None) -> bool:
    if price is None:
        return True
    checked_at = price.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return checked_at < datetime.now(UTC) - USER_PRICE_OVERRIDE_TTL


def catalog_price_source(price: CatalogPrice) -> dict[str, Any]:
    return {
        "kind": "msrp",
        "title": price.title,
        "url": price.url,
        "basis": price.basis,
        "checked_at": price.checked_at,
    }


def cached_catalog_price(session: Session, bottle: Bottle) -> CatalogPrice | None:
    product_key, size_key = catalog_price_key(bottle.name, bottle.size)
    if not product_key:
        return None
    price = session.scalar(
        select(CatalogPrice).where(
            CatalogPrice.product_key == product_key,
            CatalogPrice.size_key == size_key,
        )
    )
    return price if price and catalog_price_is_fresh(price) else None


async def apply_user_purchase_price(
    session: Session, bottle: Bottle, price_index: QdrantPriceIndex | None = None
) -> bool:
    """Use a recent user-entered purchase price only when shared catalog data is stale or absent."""
    if bottle.purchase_price is None or bottle.purchase_price <= 0:
        return False
    product_key, size_key = catalog_price_key(bottle.name, bottle.size)
    if not product_key:
        return False
    price = session.scalar(
        select(CatalogPrice).where(
            CatalogPrice.product_key == product_key,
            CatalogPrice.size_key == size_key,
        )
    )
    if not catalog_price_needs_user_update(price):
        return False
    if price is None:
        price = CatalogPrice(
            product_key=product_key,
            size_key=size_key,
            msrp=bottle.purchase_price,
            title="User-entered purchase price",
            url="",
        )
        session.add(price)
    price.msrp = bottle.purchase_price
    price.title = "User-entered purchase price"
    price.url = ""
    price.basis = ""
    price.checked_at = datetime.now(UTC)
    bottle.msrp = bottle.purchase_price
    session.flush()
    if price_index:
        await price_index.upsert(price)
    log_event(
        logger,
        logging.INFO,
        "user_purchase_price_applied",
        "User purchase price applied to local catalog",
        catalog_price_id=price.id,
    )
    return True


async def qdrant_catalog_price(
    session: Session, bottle: Bottle, price_index: QdrantPriceIndex | None
) -> CatalogPrice | None:
    if price_index is None:
        return None
    product_key, size_key = catalog_price_key(bottle.name, bottle.size)
    if not product_key:
        return None
    match = await price_index.find(product_key, size_key)
    if match is None or match.score < 0.82:
        return None
    price = session.get(CatalogPrice, match.catalog_price_id)
    if price is None or not catalog_price_is_fresh(price):
        return None
    if SequenceMatcher(None, product_key, price.product_key).ratio() < 0.82:
        return None
    return price


def cache_catalog_price(
    session: Session, bottle: Bottle, prices: dict[str, float], sources: list[dict[str, Any]]
) -> CatalogPrice | None:
    if "msrp" not in prices:
        return None
    source = next(
        (
            candidate
            for candidate in sources
            if candidate.get("kind") == "msrp"
            and urlsplit(str(candidate.get("url") or "")).scheme in {"http", "https"}
        ),
        None,
    )
    if source is None:
        return None
    product_key, size_key = catalog_price_key(bottle.name, bottle.size)
    if not product_key:
        return None
    cached = session.scalar(
        select(CatalogPrice).where(
            CatalogPrice.product_key == product_key,
            CatalogPrice.size_key == size_key,
        )
    )
    if cached is None:
        cached = CatalogPrice(
            product_key=product_key,
            size_key=size_key,
            msrp=prices["msrp"],
            url="",
        )
        session.add(cached)
    cached.msrp = prices["msrp"]
    cached.title = str(source.get("title") or "Local catalog")
    cached.url = str(source["url"])
    cached.basis = str(source.get("basis") or "")
    cached.checked_at = datetime.now(UTC)
    session.flush()
    return cached


async def refresh_prices(
    session: Session,
    bottle: Bottle,
    settings: Settings,
    *,
    force: bool = False,
    price_index: QdrantPriceIndex | None = None,
) -> str:
    if not bottle.name or bottle.name == "Untitled bottle":
        return "unavailable"
    if not force:
        cached = cached_catalog_price(session, bottle)
        if cached:
            apply_price_search(bottle, {"msrp": cached.msrp}, [catalog_price_source(cached)])
            return "cached"
        matched = await qdrant_catalog_price(session, bottle, price_index)
        if matched:
            apply_price_search(bottle, {"msrp": matched.msrp}, [catalog_price_source(matched)])
            return "local_match"
    prices, sources, status = await search_bottle_prices(bottle.name, settings, size=bottle.size)
    apply_price_search(bottle, prices, sources)
    if status == "complete":
        cached = cache_catalog_price(session, bottle, prices, sources)
        if cached and price_index:
            await price_index.upsert(cached)
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


def normalized_analysis_status(status: str) -> str:
    if status == "complete":
        return "complete"
    if status == "verified":
        return "verified"
    return "unavailable"


def analysis_redirect_query(status: str) -> str:
    if status == "complete":
        return "?analysis=complete"
    if status == "verified":
        return "?analysis=verified"
    return "?analysis=unavailable"


def owned_bottle(session: Session, user: User, bottle_id: int) -> Bottle | None:
    return session.scalar(select(Bottle).where(Bottle.id == bottle_id, Bottle.owner_id == user.id))


def collection_statement(user: User, q: str = "", sort: str = "name"):
    statement = select(Bottle).where(
        Bottle.owner_id == user.id,
        Bottle.status != "Empty",
        Bottle.on_shopping_list.is_(False),
    )
    if q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(Bottle.name.ilike(term), Bottle.brand.ilike(term), Bottle.release.ilike(term))
        )
    orders = {
        "name": (func.lower(Bottle.name).asc(), Bottle.created_at.desc()),
        "value": (
            func.coalesce(Bottle.msrp, Bottle.purchase_price, 0).desc(),
            func.lower(Bottle.name).asc(),
        ),
        "oldest": (Bottle.created_at.asc(),),
        "newest": (Bottle.created_at.desc(),),
    }
    selected_sort = sort if sort in orders else "name"
    return statement.order_by(*orders[selected_sort]), selected_sort


def remove_bottle_photo(bottle: Bottle, upload_dir: Path) -> None:
    if bottle.photo_name:
        (upload_dir / bottle.photo_name).unlink(missing_ok=True)


def selected_upload(form: Any, *field_names: str) -> StarletteUploadFile | None:
    for field_name in field_names:
        upload = form.get(field_name)
        if isinstance(upload, StarletteUploadFile) and upload.filename:
            return upload
    return None


def shared_collection_user(session: Session, raw_token: str) -> User | None:
    if not raw_token or len(raw_token) > 128:
        return None
    return session.scalar(
        select(User).where(User.collection_share_token_hash == token_digest(raw_token))
    )


def is_shopping_item(bottle: Bottle) -> bool:
    return bottle.on_shopping_list or bottle.status == "Empty"


def protect_shared_response(response: Response) -> Response:
    response.headers.update(
        {
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        }
    )
    return response


def render_admin_user(
    request: Request,
    session: Session,
    admin: User,
    target: User,
    error: str | None,
    notice: str | None,
    status_code: int,
) -> HTMLResponse:
    bottle_count = session.scalar(select(func.count(Bottle.id)).where(Bottle.owner_id == target.id))
    return render(
        request,
        "admin/user_detail.html",
        user=admin,
        target=target,
        bottle_count=bottle_count or 0,
        error=error,
        notice=notice,
        status_code=status_code,
    )


def log_admin_action(actor_user_id: int, target_user_id: int, action: str, success: bool) -> None:
    log_event(
        logger,
        logging.INFO if success else logging.WARNING,
        "admin_action",
        "Admin action completed",
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        result="success" if success else "failure",
    )


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

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        try:
            with app.state.database.engine.connect() as connection:
                revision = connection.execute(
                    text("select version_num from alembic_version")
                ).scalar()
                connection.execute(text("select 1")).scalar_one()
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        if revision != HEAD_REVISION:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        if not app.state.settings.metrics_enabled:
            return Response(status_code=404)
        content, media_type = metrics_response()
        return Response(content=content, media_type=media_type)

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
                observe_auth_event("login", "failure")
                log_event(logger, logging.INFO, "login_failed", "Login failed")
                return render(
                    request,
                    "login.html",
                    mode="login",
                    error="Email or password is incorrect.",
                )
            if not user.email:
                observe_auth_event("login", "failure")
                return render(
                    request,
                    "login.html",
                    mode="login",
                    error="Contact an administrator to update this legacy account.",
                )
            if app.state.settings.email_verification_required and not user.email_verified_at:
                observe_auth_event("login", "unverified")
                request.session.clear()
                request.session["unverified_user_id"] = user.id
                return RedirectResponse("/check-email", 303)
            authenticate_session(request, user)
            observe_auth_event("login", "success")
            log_event(
                logger,
                logging.INFO,
                "login_succeeded",
                "Login succeeded",
                actor_user_id=user.id,
            )
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
                email_verified_at=(
                    None if app.state.settings.email_verification_required else datetime.now(UTC)
                ),
            )
            session.add(user)
            session.flush()
            if not app.state.settings.email_verification_required:
                session.commit()
                authenticate_session(request, user)
                observe_auth_event("registration", "success")
                return RedirectResponse("/profile", 303)
            request.session.clear()
            request.session["unverified_user_id"] = user.id
            try:
                verification_url = await issue_verification(
                    session, user, app.state.settings, app.state.email_sender
                )
                if app.state.settings.app_env != "production":
                    request.session["verification_url"] = verification_url
            except Exception:
                logger.exception("Verification email delivery failed user_id=%s", user.id)
                return render(request, "check_email.html", delivery_error=True, status_code=503)
            observe_auth_event("registration", "success")
            log_event(
                logger,
                logging.INFO,
                "registration_succeeded",
                "Registration succeeded",
                target_user_id=user.id,
            )
        return RedirectResponse("/check-email", 303)

    @app.get("/check-email", response_class=HTMLResponse)
    def check_email(request: Request) -> Response:
        verification_url = request.session.get("verification_url")
        return render(
            request,
            "check_email.html",
            delivery_error=False,
            verification_url=verification_url,
        )

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
                observe_auth_event("verification_requested", "success")
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
            observe_auth_event("verification_completed", "success")
            log_event(
                logger,
                logging.INFO,
                "verification_completed",
                "Email verification completed",
                actor_user_id=user.id,
            )
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
            observe_auth_event("reset_requested", "success")
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
            observe_auth_event("reset_completed", "success")
            log_event(
                logger,
                logging.INFO,
                "password_reset_completed",
                "Password reset completed",
                actor_user_id=user.id,
            )
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

    @app.post("/profile/avatar")
    async def update_profile_avatar(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            upload = selected_upload(form, "avatar")
            if not upload:
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error="Choose an image to upload.",
                    notice=None,
                )
            avatar_dir = app.state.settings.data_dir / "avatars"
            try:
                avatar_name = await save_avatar(upload, avatar_dir)
            except HTTPException as exc:
                return render(
                    request,
                    "profile.html",
                    user=user,
                    error=str(exc.detail),
                    notice=None,
                    status_code=exc.status_code,
                )
            previous_name = user.avatar_name
            user.avatar_name = avatar_name
            session.commit()
            if previous_name:
                (avatar_dir / previous_name).unlink(missing_ok=True)
            return render(
                request,
                "profile.html",
                user=user,
                error=None,
                notice="Avatar updated.",
            )

    @app.post("/profile/avatar/remove")
    async def remove_profile_avatar(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            previous_name = user.avatar_name
            user.avatar_name = None
            session.commit()
            if previous_name:
                (app.state.settings.data_dir / "avatars" / previous_name).unlink(missing_ok=True)
            return render(
                request,
                "profile.html",
                user=user,
                error=None,
                notice="Avatar removed.",
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
            observe_auth_event("email_changed", "success")
            log_event(
                logger,
                logging.INFO,
                "email_changed",
                "Email changed",
                actor_user_id=user.id,
            )
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
            log_event(
                logger,
                logging.INFO,
                "password_changed",
                "Password changed",
                actor_user_id=user.id,
            )
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
            avatar_root = (app.state.settings.data_dir / "avatars").resolve()
            photo_paths = []
            for bottle in user.bottles:
                if bottle.photo_name:
                    candidate = (upload_root / bottle.photo_name).resolve()
                    if candidate.parent == upload_root:
                        photo_paths.append(candidate)
            avatar_path = None
            if user.avatar_name:
                candidate = (avatar_root / user.avatar_name).resolve()
                if candidate.parent == avatar_root:
                    avatar_path = candidate
            session.delete(user)
            session.commit()
            for path in photo_paths:
                path.unlink(missing_ok=True)
            if avatar_path:
                avatar_path.unlink(missing_ok=True)
            request.session.clear()
            observe_auth_event("account_deleted", "success")
            log_event(
                logger,
                logging.INFO,
                "account_deleted",
                "Account deleted",
                actor_user_id=user.id,
            )
        return RedirectResponse("/account-deleted", 303)

    @app.get("/account-deleted", response_class=HTMLResponse)
    def account_deleted(request: Request) -> Response:
        return render(request, "account_deleted.html")

    @app.get("/admin/users", response_class=HTMLResponse)
    def admin_users(request: Request, q: str = "", page: int = 1) -> Response:
        page_size = 20
        page = max(1, page)
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            statement = (
                select(User, func.count(Bottle.id).label("bottle_count"))
                .outerjoin(Bottle, Bottle.owner_id == User.id)
                .group_by(User.id)
            )
            count_statement = select(func.count(User.id))
            if q.strip():
                term = f"%{q.strip()}%"
                criteria = or_(User.email.ilike(term), User.screen_name.ilike(term))
                statement = statement.where(criteria)
                count_statement = count_statement.where(criteria)
            total = session.scalar(count_statement) or 0
            rows = list(
                session.execute(
                    statement.order_by(User.created_at.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            return render(
                request,
                "admin/users.html",
                user=admin,
                users=rows,
                q=q,
                page=page,
                page_size=page_size,
                total=total,
                max_page=max(1, (total + page_size - 1) // page_size),
            )

    @app.get("/admin/catalog", response_class=HTMLResponse)
    def admin_catalog(
        request: Request, q: str = "", sort: str = "name_asc", page: int = 1, page_size: int = 25
    ) -> Response:
        page_size = page_size if page_size in {10, 25, 50, 100} else 25
        page = max(1, page)
        ordering = {
            "name_asc": (CatalogPrice.product_key.asc(),),
            "name_desc": (CatalogPrice.product_key.desc(),),
            "price_asc": (CatalogPrice.msrp.asc(), CatalogPrice.product_key.asc()),
            "price_desc": (CatalogPrice.msrp.desc(), CatalogPrice.product_key.asc()),
        }
        sort = sort if sort in ordering else "name_asc"
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            statement = select(CatalogPrice)
            count_statement = select(func.count(CatalogPrice.id))
            if q.strip():
                statement = statement.where(CatalogPrice.product_key.ilike(f"%{q.strip()}%"))
                count_statement = count_statement.where(
                    CatalogPrice.product_key.ilike(f"%{q.strip()}%")
                )
            total = session.scalar(count_statement) or 0
            max_page = max(1, (total + page_size - 1) // page_size)
            page = min(page, max_page)
            prices = list(
                session.scalars(
                    statement.order_by(*ordering[sort])
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            return render(
                request,
                "admin/catalog.html",
                user=admin,
                prices=prices,
                q=q,
                sort=sort,
                page=page,
                page_size=page_size,
                total=total,
                max_page=max_page,
                pages=range(1, max_page + 1),
            )

    @app.post("/admin/catalog")
    async def admin_catalog_update(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        q = str(form.get("q", ""))
        sort = str(form.get("sort", "name_asc"))
        page = parse_int(form.get("page"), 1, 1, 100_000)
        page_size = parse_int(form.get("page_size"), 25, 10, 100)
        selected = {parse_int(value, 0, 1, 2_147_483_647) for value in form.getlist("selected")}
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            if str(form.get("action")) == "delete":
                for price_id in selected:
                    if price := session.get(CatalogPrice, price_id):
                        session.delete(price)
            else:
                for key, value in form.items():
                    if not key.startswith("name_"):
                        continue
                    price_id = parse_int(key.removeprefix("name_"), 0, 1, 2_147_483_647)
                    price = session.get(CatalogPrice, price_id)
                    if price is None:
                        continue
                    name = str(value).strip()
                    msrp = parse_float(form.get(f"msrp_{price_id}"))
                    if not name or msrp is None or msrp <= 0:
                        continue
                    product_key, size_key = catalog_price_key(name, price.size_key)
                    if product_key:
                        price.product_key, price.size_key, price.msrp = product_key, size_key, msrp
                        price.checked_at = datetime.now(UTC)
            session.commit()
            log_admin_action(admin.id, admin.id, f"catalog_{form.get('action', 'update')}", True)
        return RedirectResponse(
            f"/admin/catalog?q={q}&sort={sort}&page={page}&page_size={page_size}", 303
        )

    def catalog_import_pages(page: int, max_page: int) -> range:
        """Keep a compact, stable page window around the selected proposal page."""
        return range(max(1, page - 2), min(max_page, page + 2) + 1)

    def catalog_import_message(batch: CatalogImportBatch) -> str:
        messages = {
            "queued": "Extraction is queued and will start when the local processing lane is free.",
            "extracting": "Extraction is in progress. Refresh this page for its review status.",
            "review": "Review the proposed rows below. Saving changes does not update the catalog.",
            "applied": "This batch has already been applied; its proposal rows are read-only.",
            "failed": "Extraction did not complete. No catalog prices were changed.",
            "expired": (
                "This batch has expired. Its proposal rows are retained only as audit evidence."
            ),
        }
        return messages.get(batch.state, "This batch has an unrecognized status.")

    def render_catalog_import(
        request: Request,
        admin: User,
        *,
        error: str | None = None,
        notice: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        with app.state.database.session_factory() as session:
            recent_batches = list(
                session.scalars(
                    select(CatalogImportBatch)
                    .options(joinedload(CatalogImportBatch.created_by))
                    .order_by(CatalogImportBatch.created_at.desc(), CatalogImportBatch.id.desc())
                    .limit(20)
                )
            )
            return render(
                request,
                "admin/catalog_import.html",
                user=admin,
                error=error,
                notice=notice,
                batches=recent_batches,
                status_code=status_code,
            )

    def render_catalog_import_review(
        request: Request,
        admin: User,
        *,
        batch: CatalogImportBatch | None,
        page: int = 1,
        error: str | None = None,
        notice: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        if batch is None:
            raise HTTPException(status_code=404, detail="Catalog import batch not found.")
        with app.state.database.session_factory() as session:
            selected_batch = session.scalar(
                select(CatalogImportBatch)
                .options(joinedload(CatalogImportBatch.created_by))
                .where(CatalogImportBatch.id == batch.id)
            )
            if selected_batch is None:
                raise HTTPException(status_code=404, detail="Catalog import batch not found.")
            total = int(
                session.scalar(
                    select(func.count(CatalogImportProposal.id)).where(
                        CatalogImportProposal.batch_id == selected_batch.id
                    )
                )
                or 0
            )
            max_page = max(1, (total + 24) // 25)
            page = min(max(1, page), max_page)
            proposals = list(
                session.scalars(
                    select(CatalogImportProposal)
                    .where(CatalogImportProposal.batch_id == selected_batch.id)
                    .order_by(CatalogImportProposal.position)
                    .offset((page - 1) * 25)
                    .limit(25)
                )
            )
            current_catalog_prices: dict[int, CatalogPrice] = {}
            if proposals:
                matches = [
                    (CatalogPrice.product_key == proposal.product_key)
                    & (CatalogPrice.size_key == proposal.size_key)
                    for proposal in proposals
                ]
                prices_by_key = {
                    (price.product_key, price.size_key): price
                    for price in session.scalars(select(CatalogPrice).where(or_(*matches)))
                }
                current_catalog_prices = {
                    proposal.id: prices_by_key.get((proposal.product_key, proposal.size_key))
                    for proposal in proposals
                }
            return render(
                request,
                "admin/catalog_import_review.html",
                user=admin,
                error=error,
                notice=notice,
                batch=selected_batch,
                proposals=proposals,
                current_catalog_prices=current_catalog_prices,
                proposal_total=total,
                page=page,
                max_page=max_page,
                pages=catalog_import_pages(page, max_page),
                batch_message=catalog_import_message(selected_batch),
                status_code=status_code,
            )

    @app.get("/admin/catalog-import", response_class=HTMLResponse)
    def admin_catalog_import(
        request: Request, error: str | None = None, saved: int = 0, deleted: int = 0
    ) -> Response:
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
        return render_catalog_import(
            request,
            admin,
            error=error,
            notice=(
                "Review changes saved. No catalog prices have been updated."
                if saved
                else "Catalog import batch deleted. No catalog prices were changed."
                if deleted
                else None
            ),
        )

    @app.get("/admin/catalog-import/{batch_id}", response_class=HTMLResponse)
    def admin_catalog_import_review(
        request: Request,
        batch_id: int,
        page: int = 1,
        saved: int = 0,
        applied: int = 0,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
    ) -> Response:
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            batch = session.get(CatalogImportBatch, batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="Catalog import batch not found.")
        return render_catalog_import_review(
            request,
            admin,
            batch=batch,
            page=page,
            notice=(
                "Review changes saved. No catalog prices have been updated."
                if saved
                else (
                    f"Catalog import batch applied: {created} created, {updated} updated, "
                    f"{skipped} skipped."
                )
                if applied
                else None
            ),
        )

    @app.post("/admin/catalog-import/{batch_id}/review", response_class=HTMLResponse)
    async def admin_catalog_import_save_review(request: Request, batch_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        page = parse_int(form.get("page"), 1, 1, 100_000)
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            batch = session.get(CatalogImportBatch, batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="Catalog import batch not found.")
            if batch.state != "review":
                return render_catalog_import_review(
                    request,
                    admin,
                    batch=batch,
                    page=page,
                    error="Only batches awaiting review can be edited.",
                    status_code=409,
                )
            proposal_ids = {
                parse_int(value, 0, 1, 2_147_483_647) for value in form.getlist("proposal_id")
            }
            proposals = list(
                session.scalars(
                    select(CatalogImportProposal).where(
                        CatalogImportProposal.batch_id == batch.id,
                        CatalogImportProposal.id.in_(proposal_ids),
                    )
                )
            )
            errors: list[str] = []
            for proposal in proposals:
                name = str(form.get(f"name_{proposal.id}", "")).strip()
                size = str(form.get(f"size_{proposal.id}", "")).strip()
                msrp = parse_float(form.get(f"msrp_{proposal.id}"))
                raw_date = str(form.get(f"price_updated_at_{proposal.id}", "")).strip()
                try:
                    price_updated_at = date.fromisoformat(raw_date)
                except ValueError:
                    price_updated_at = None
                product_key, size_key = catalog_price_key(name, size)
                if (
                    not product_key
                    or not size_key
                    or msrp is None
                    or msrp <= 0
                    or price_updated_at is None
                ):
                    proposal.validation_error = (
                        "Use a bottle name, package size, positive displayed price, and valid "
                        "update date."
                    )
                    errors.append(f"Proposal {proposal.position}")
                    continue
                proposal.name = name[:240]
                proposal.product_key = product_key
                proposal.size_key = size_key[:80]
                proposal.msrp = msrp
                proposal.price_updated_at = price_updated_at
                proposal.included = str(form.get(f"included_{proposal.id}", "")) == "on"
                proposal.validation_error = None
            session.commit()
            if errors:
                return render_catalog_import_review(
                    request,
                    admin,
                    batch=batch,
                    page=page,
                    error=f"Fix the required fields for {', '.join(errors)} before saving.",
                    status_code=400,
                )
        return RedirectResponse(f"/admin/catalog-import/{batch_id}?page={page}&saved=1", 303)

    @app.post("/admin/catalog-import/{batch_id}/apply", response_class=HTMLResponse)
    async def admin_catalog_import_apply(request: Request, batch_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        page = parse_int(form.get("page"), 1, 1, 100_000)
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
        try:
            with app.state.database.session_factory() as session:
                result = apply_catalog_import_batch(session, batch_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CatalogImportApplyStateError as exc:
            with app.state.database.session_factory() as session:
                admin = require_admin(request, session)
                batch = session.get(CatalogImportBatch, batch_id)
            return render_catalog_import_review(
                request,
                admin,
                batch=batch,
                page=page,
                error=str(exc),
                status_code=409,
            )
        return RedirectResponse(
            f"/admin/catalog-import/{batch_id}?page={page}&applied=1&created={result.created}"
            f"&updated={result.updated}&skipped={result.skipped}",
            303,
        )

    @app.post("/admin/catalog-import/{batch_id}/delete", response_class=HTMLResponse)
    async def admin_catalog_import_delete(request: Request, batch_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            if not delete_catalog_import_batch(session, batch_id):
                session.rollback()
                batch = session.get(CatalogImportBatch, batch_id)
                if batch is None:
                    raise HTTPException(status_code=404, detail="Catalog import batch not found.")
                return render_catalog_import_review(
                    request,
                    admin,
                    batch=batch,
                    error=(
                        "An in-process or completed catalog import cannot be deleted. "
                        "Wait for extraction to finish before deleting it."
                    ),
                    status_code=409,
                )
            session.commit()
        remove_catalog_import_batch_sources(app.state.settings, batch_id)
        return RedirectResponse("/admin/catalog-import?deleted=1", 303)

    @app.post("/admin/catalog-import", response_class=HTMLResponse)
    async def admin_catalog_import_upload(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            pages = form.getlist("pages")
            if not pages or not all(isinstance(page, StarletteUploadFile) for page in pages):
                return render(
                    request,
                    "admin/catalog_import.html",
                    user=admin,
                    error="Upload PNG, JPEG, or PDF files.",
                    status_code=400,
                )
            max_file_bytes = app.state.settings.max_upload_mb * 1024 * 1024
            uploads = [(page.content_type, await page.read(max_file_bytes + 1)) for page in pages]
            try:
                staged = validate_catalog_uploads(uploads, app.state.settings)
                raw_date = str(form.get("price_updated_at", "")).strip()
                requested_date = date.fromisoformat(raw_date) if raw_date else None
            except (HTTPException, ValueError) as exc:
                status_code = exc.status_code if isinstance(exc, HTTPException) else 400
                detail = "Invalid price update date."
                if isinstance(exc, HTTPException):
                    detail = exc.detail
                return render(
                    request,
                    "admin/catalog_import.html",
                    user=admin,
                    error=str(detail),
                    status_code=status_code,
                )
            batch_id = reserve_catalog_import_batch(
                session,
                created_by_user_id=admin.id,
                requested_price_updated_at=requested_date,
                source_file_count=len(staged),
                queue_capacity=app.state.settings.catalog_import_queue_capacity,
            )
            if batch_id is None:
                return render(
                    request,
                    "admin/catalog_import.html",
                    user=admin,
                    error="Catalog import queue is full. Wait for an existing import to start.",
                    status_code=429,
                )

            try:
                stage_catalog_uploads(app.state.settings, batch_id, staged)
                session.commit()
            except (OSError, RuntimeError, SQLAlchemyError):
                session.rollback()
                remove_catalog_import_batch_sources(app.state.settings, batch_id)
                return render(
                    request,
                    "admin/catalog_import.html",
                    user=admin,
                    error="Catalog upload could not be staged. Please try again.",
                    status_code=500,
                )
        return render_catalog_import(
            request,
            admin,
            notice=f"{len(staged)} catalog file(s) staged securely. Extraction has been queued.",
        )

    def render_admin_config(
        request: Request,
        admin: User,
        *,
        values: dict[str, str] | None = None,
        error: str | None = None,
        notice: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        field_groups: dict[str, list[Any]] = {}
        for field in CONFIG_FIELDS:
            field_groups.setdefault(field.group, []).append(field)
        return render(
            request,
            "admin/config.html",
            user=admin,
            field_groups=field_groups,
            values=values or settings_values(app.state.settings),
            error=error,
            notice=notice,
            config_path=managed_config_path(app.state.settings),
            status_code=status_code,
        )

    @app.get("/admin/config", response_class=HTMLResponse)
    def admin_config(request: Request) -> Response:
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            return render_admin_config(request, admin)

    @app.post("/admin/config", response_class=HTMLResponse)
    async def admin_save_config(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            submitted = {field.key: str(form.get(field.key, "")) for field in CONFIG_FIELDS}
            try:
                values, _candidate = parse_config_form(form, app.state.settings)
                write_managed_config(managed_config_path(app.state.settings), values)
            except (OSError, ValueError) as exc:
                log_admin_action(admin.id, admin.id, "update_config", False)
                return render_admin_config(
                    request, admin, values=submitted, error=str(exc), status_code=400
                )
            log_admin_action(admin.id, admin.id, "update_config", True)
            return render_admin_config(
                request,
                admin,
                values=values,
                notice="Configuration saved. Restart the app to apply these changes.",
            )

    @app.post("/admin/restart")
    async def admin_restart(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            log_admin_action(admin.id, admin.id, "restart_app", True)
        return HTMLResponse(
            "<!doctype html><title>Restarting</title>"
            '<meta http-equiv="refresh" content="5;url=/admin/config">'
            "<p>Bourbon Book is restarting. This page will reconnect shortly.</p>",
            background=BackgroundTask(app.state.restart),
        )

    @app.get("/admin/users/{target_id}", response_class=HTMLResponse)
    def admin_user_detail(request: Request, target_id: int) -> Response:
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            target = session.get(User, target_id)
            if not target:
                return RedirectResponse("/admin/users", 303)
            bottle_count = session.scalar(
                select(func.count(Bottle.id)).where(Bottle.owner_id == target.id)
            )
            return render(
                request,
                "admin/user_detail.html",
                user=admin,
                target=target,
                bottle_count=bottle_count or 0,
                error=None,
                notice=None,
            )

    @app.post("/admin/users/{target_id}/send-reset")
    async def admin_send_reset(request: Request, target_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            target = session.get(User, target_id)
            if not target or not target.email:
                return RedirectResponse("/admin/users", 303)
            if not limited(request, "admin-reset", str(target.id)):
                return render_admin_user(
                    request, session, admin, target, "Too many attempts.", None, 429
                )
            try:
                await issue_reset(session, target, app.state.settings, app.state.email_sender)
            except Exception:
                log_admin_action(admin.id, target.id, "send_reset", False)
                return render_admin_user(
                    request, session, admin, target, "Reset email could not be sent.", None, 503
                )
            log_admin_action(admin.id, target.id, "send_reset", True)
            observe_auth_event("admin_reset_requested", "success")
            return render_admin_user(
                request, session, admin, target, None, "Password reset email sent.", 200
            )

    @app.post("/admin/users/{target_id}/resend-verification")
    async def admin_resend_verification(request: Request, target_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            target = session.get(User, target_id)
            if not target or not target.email:
                return RedirectResponse("/admin/users", 303)
            if not limited(request, "admin-verification", str(target.id)):
                return render_admin_user(
                    request, session, admin, target, "Too many attempts.", None, 429
                )
            try:
                await issue_verification(
                    session, target, app.state.settings, app.state.email_sender
                )
            except Exception:
                log_admin_action(admin.id, target.id, "resend_verification", False)
                return render_admin_user(
                    request,
                    session,
                    admin,
                    target,
                    "Verification email could not be sent.",
                    None,
                    503,
                )
            log_admin_action(admin.id, target.id, "resend_verification", True)
            observe_auth_event("admin_verification_requested", "success")
            return render_admin_user(
                request, session, admin, target, None, "Verification email sent.", 200
            )

    @app.post("/admin/users/{target_id}/email")
    async def admin_update_email(request: Request, target_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            target = session.get(User, target_id)
            if not target:
                return RedirectResponse("/admin/users", 303)
            try:
                email = normalize_email(str(form.get("email", "")))
            except ValueError as exc:
                return render_admin_user(request, session, admin, target, str(exc), None, 400)
            if str(form.get("confirmation", "")).strip().lower() != email:
                return render_admin_user(
                    request,
                    session,
                    admin,
                    target,
                    "Type the new email address exactly to confirm.",
                    None,
                    400,
                )
            duplicate = session.scalar(
                select(User).where(User.email == email, User.id != target.id)
            )
            if duplicate:
                return render_admin_user(
                    request, session, admin, target, "That email is already in use.", None, 400
                )
            target.email = email
            target.username = email
            target.email_verified_at = None
            target.session_version += 1
            revoke_tokens(session, target.id)
            session.flush()
            try:
                await issue_verification(
                    session, target, app.state.settings, app.state.email_sender
                )
            except Exception:
                log_admin_action(admin.id, target.id, "update_email", False)
                return render_admin_user(
                    request,
                    session,
                    admin,
                    target,
                    "Email was changed, but verification could not be sent.",
                    None,
                    503,
                )
            log_admin_action(admin.id, target.id, "update_email", True)
            observe_auth_event("admin_email_changed", "success")
            return render_admin_user(
                request,
                session,
                admin,
                target,
                None,
                "Email changed and verification sent.",
                200,
            )

    @app.get("/admin/usage", response_class=HTMLResponse)
    def admin_usage(request: Request, days: int = 7, page: int = 1) -> Response:
        page = max(1, page)
        days = max(1, min(365, days))
        page_size = 25
        since = datetime.now(UTC) - timedelta(days=days)
        with app.state.database.session_factory() as session:
            admin = require_admin(request, session)
            totals = list(
                session.execute(
                    select(
                        ApiUsage.provider,
                        ApiUsage.operation,
                        ApiUsage.model,
                        ApiUsage.success,
                        func.count(ApiUsage.id).label("calls"),
                        func.coalesce(func.sum(ApiUsage.input_tokens), 0).label("input_tokens"),
                        func.coalesce(func.sum(ApiUsage.output_tokens), 0).label("output_tokens"),
                        func.coalesce(func.sum(ApiUsage.total_tokens), 0).label("total_tokens"),
                        func.coalesce(func.avg(ApiUsage.duration_ms), 0).label("avg_duration_ms"),
                    )
                    .where(ApiUsage.created_at >= since)
                    .group_by(
                        ApiUsage.provider,
                        ApiUsage.operation,
                        ApiUsage.model,
                        ApiUsage.success,
                    )
                    .order_by(ApiUsage.provider, ApiUsage.operation, ApiUsage.model)
                )
            )
            total_records = (
                session.scalar(select(func.count(ApiUsage.id)).where(ApiUsage.created_at >= since))
                or 0
            )
            recent = list(
                session.scalars(
                    select(ApiUsage)
                    .where(ApiUsage.created_at >= since)
                    .order_by(ApiUsage.created_at.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            return render(
                request,
                "admin/usage.html",
                user=admin,
                days=days,
                page=page,
                page_size=page_size,
                total_records=total_records,
                max_page=max(1, (total_records + page_size - 1) // page_size),
                totals=totals,
                recent=recent,
            )

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        request.session.clear()
        observe_auth_event("logout", "success")
        log_event(logger, logging.INFO, "logout", "User logged out")
        return RedirectResponse("/login", 303)

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request, q: str = "", sort: str = "name") -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            statement, selected_sort = collection_statement(user, q, sort)
            bottles = list(session.scalars(statement))
            all_bottles = list(session.scalars(collection_statement(user)[0]))
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
                sort=selected_sort,
            )

    @app.get("/collection/compact", response_class=HTMLResponse)
    def compact_collection(request: Request, q: str = "") -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottles = list(session.scalars(collection_statement(user, q)[0]))
            share_url = request.session.pop("new_collection_share_url", None)
            return render(
                request,
                "compact.html",
                user=user,
                bottles=bottles,
                bottle_count=sum(bottle.quantity for bottle in bottles),
                q=q,
                share_url=share_url,
            )

    @app.post("/collection/share")
    async def share_collection(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            raw_token = secrets.token_urlsafe(32)
            user.collection_share_token_hash = token_digest(raw_token)
            user.collection_shared_at = datetime.now(UTC)
            session.commit()
        request.session["new_collection_share_url"] = (
            f"{app.state.settings.public_base_url}/shared/{raw_token}"
        )
        return RedirectResponse("/collection/compact", 303)

    @app.post("/collection/share/disable")
    async def disable_collection_share(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            user.collection_share_token_hash = None
            user.collection_shared_at = None
            session.commit()
        request.session.pop("new_collection_share_url", None)
        return RedirectResponse("/collection/compact", 303)

    @app.get("/shared/{share_token}", response_class=HTMLResponse)
    def shared_collection(request: Request, share_token: str, q: str = "") -> Response:
        with app.state.database.session_factory() as session:
            owner = shared_collection_user(session, share_token)
            if not owner:
                return protect_shared_response(Response(status_code=404))
            bottles = list(session.scalars(collection_statement(owner, q)[0]))
            response = render(
                request,
                "shared_collection.html",
                owner=owner,
                bottles=bottles,
                bottle_count=sum(bottle.quantity for bottle in bottles),
                q=q,
                share_token=share_token,
            )
            return protect_shared_response(response)

    @app.get("/shared/{share_token}/media/{photo_name}")
    def shared_collection_photo(share_token: str, photo_name: str) -> Response:
        with app.state.database.session_factory() as session:
            owner = shared_collection_user(session, share_token)
            if not owner:
                return protect_shared_response(Response(status_code=404))
            bottle = session.scalar(
                select(Bottle).where(
                    Bottle.owner_id == owner.id,
                    Bottle.photo_name == photo_name,
                    Bottle.status != "Empty",
                    Bottle.on_shopping_list.is_(False),
                )
            )
            path = app.state.settings.data_dir / "uploads" / photo_name
            if not bottle or not path.is_file():
                return protect_shared_response(Response(status_code=404))
            return protect_shared_response(FileResponse(path, media_type="image/jpeg"))

    @app.get("/shopping-list", response_class=HTMLResponse)
    def shopping_list(request: Request) -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottles = list(
                session.scalars(
                    select(Bottle)
                    .where(
                        Bottle.owner_id == user.id,
                        or_(Bottle.on_shopping_list.is_(True), Bottle.status == "Empty"),
                    )
                    .order_by(func.lower(Bottle.name), Bottle.created_at.desc())
                )
            )
            return render(request, "shopping_list.html", user=user, bottles=bottles)

    @app.post("/shopping-list")
    async def add_shopping_item(request: Request) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        name = str(form.get("name", "")).strip()
        if not name:
            return RedirectResponse("/shopping-list?error=name", 303)
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = Bottle(
                owner_id=user.id,
                name=name,
                brand=str(form.get("brand", "")).strip(),
                notes=str(form.get("notes", "")).strip(),
                status="Empty",
                fill_level=0,
                on_shopping_list=True,
            )
            upload = selected_upload(form, "camera_photo", "photo")
            if upload:
                bottle.photo_name = await save_photo(
                    upload,
                    app.state.settings.data_dir / "uploads",
                    app.state.settings.max_upload_mb,
                )
            session.add(bottle)
            session.commit()
        return RedirectResponse("/shopping-list", 303)

    @app.post("/shopping-list/{bottle_id}/photo")
    async def update_shopping_photo(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            upload = selected_upload(form, "photo")
            if bottle and is_shopping_item(bottle) and upload:
                old_photo = bottle.photo_name
                bottle.photo_name = await save_photo(
                    upload,
                    app.state.settings.data_dir / "uploads",
                    app.state.settings.max_upload_mb,
                )
                if old_photo:
                    (app.state.settings.data_dir / "uploads" / old_photo).unlink(missing_ok=True)
                session.commit()
        return RedirectResponse("/shopping-list", 303)

    @app.post("/shopping-list/{bottle_id}/purchased")
    async def purchase_shopping_item(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if bottle and is_shopping_item(bottle):
                bottle.on_shopping_list = False
                bottle.status = "Unopened"
                bottle.fill_level = 100
                session.commit()
        return RedirectResponse("/", 303)

    @app.post("/shopping-list/{bottle_id}/delete")
    async def delete_shopping_item(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if bottle and is_shopping_item(bottle):
                remove_bottle_photo(bottle, app.state.settings.data_dir / "uploads")
                session.delete(bottle)
                session.commit()
        return RedirectResponse("/shopping-list", 303)

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
            with usage_context(app.state.usage_recorder, user.id):
                analysis, analysis_status = await analyze_bottle(
                    app.state.settings.data_dir / "uploads" / photo_name, app.state.settings
                )
            analysis_status = normalized_analysis_status(analysis_status)
            bottle = Bottle(
                owner_id=user.id,
                photo_name=photo_name,
                analysis_status=analysis_status,
            )
            apply_analysis(bottle, analysis)
            bottle.purchase_price = parse_float(purchase_price)
            bottle.quantity = parse_int(quantity, 1, 1, 99)
            if bottle.name and bottle.name != "Untitled bottle":
                enrichment, enrichment_status = await enrich_bottle_by_name(
                    bottle, app.state.settings, allow_provider=False
                )
                apply_analysis(bottle, enrichment)
                if enrichment:
                    bottle.analysis_status = normalized_analysis_status(enrichment_status)
                user_price_applied = await apply_user_purchase_price(
                    session, bottle, app.state.qdrant_price_index
                )
                if not user_price_applied:
                    with usage_context(app.state.usage_recorder, user.id):
                        price_status = await refresh_prices(
                            session,
                            bottle,
                            app.state.settings,
                            price_index=app.state.qdrant_price_index,
                        )
                else:
                    price_status = "user_price"
                if price_status == "complete":
                    bottle.analysis_status = price_status
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
            saved_bottle_id = bottle.id
            previous_status = bottle.status
            previous_prices = {"msrp": bottle.msrp}
            update_bottle_from_form(bottle, form)
            if previous_status != "Empty" and bottle.status == "Empty":
                empty_action = str(form.get("empty_action", ""))
                if empty_action == "remove":
                    remove_bottle_photo(bottle, app.state.settings.data_dir / "uploads")
                    session.delete(bottle)
                    session.commit()
                    return RedirectResponse("/", 303)
                if empty_action == "shopping":
                    bottle.on_shopping_list = True
                    bottle.fill_level = 0
                else:
                    session.rollback()
                    edit_path = app.url_path_for("bottle_edit", bottle_id=str(saved_bottle_id))
                    return RedirectResponse(f"{edit_path}?empty=1", 303)
            elif bottle.status != "Empty":
                bottle.on_shopping_list = False
            current_prices = {"msrp": bottle.msrp}
            for source in list(bottle.price_sources):
                if source.kind == "msrp" and previous_prices["msrp"] != current_prices["msrp"]:
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
            on_shopping_list = bottle.on_shopping_list
            session.commit()
        if on_shopping_list:
            return RedirectResponse("/shopping-list", 303)
        detail_path = app.url_path_for("bottle_detail", bottle_id=str(saved_bottle_id))
        return RedirectResponse(detail_path, 303)

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
            saved_bottle_id = bottle.id
            update_bottle_from_form(bottle, form)
            user_price_applied = await apply_user_purchase_price(
                session, bottle, app.state.qdrant_price_index
            )
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
                if user_price_applied:
                    analysis, analysis_status = {}, "complete"
                else:
                    with usage_context(app.state.usage_recorder, user.id):
                        analysis, analysis_status = (
                            {},
                            await refresh_prices(
                                session,
                                bottle,
                                app.state.settings,
                                force=True,
                                price_index=app.state.qdrant_price_index,
                            ),
                        )
            elif mode == "name":
                with usage_context(app.state.usage_recorder, user.id):
                    analysis, analysis_status = await enrich_bottle_by_name(
                        bottle, app.state.settings
                    )
            elif bottle.photo_name:
                with usage_context(app.state.usage_recorder, user.id):
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
            if mode in {"name", "photo"} and bottle.name and not user_price_applied:
                with usage_context(app.state.usage_recorder, user.id):
                    price_status = await refresh_prices(
                        session,
                        bottle,
                        app.state.settings,
                        price_index=app.state.qdrant_price_index,
                    )
                if price_status == "complete":
                    analysis_status = price_status
            analysis_status = normalized_analysis_status(analysis_status)
            bottle.analysis_status = analysis_status
            session.commit()
        edit_path = app.url_path_for("bottle_edit", bottle_id=str(saved_bottle_id))
        return RedirectResponse(f"{edit_path}{analysis_redirect_query(analysis_status)}", 303)

    @app.post("/bottles/{bottle_id}/delete")
    async def delete_bottle(request: Request, bottle_id: int) -> Response:
        form = await request.form()
        verify_csrf(request, str(form.get("csrf_token", "")))
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            bottle = owned_bottle(session, user, bottle_id)
            if bottle:
                remove_bottle_photo(bottle, app.state.settings.data_dir / "uploads")
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

    @app.get("/avatars/{avatar_name}")
    def avatar(request: Request, avatar_name: str) -> Response:
        with app.state.database.session_factory() as session:
            user = require_verified_user(request, session)
            avatar_root = (app.state.settings.data_dir / "avatars").resolve()
            path = (avatar_root / avatar_name).resolve()
            if user.avatar_name != avatar_name or path.parent != avatar_root or not path.is_file():
                return Response(status_code=404)
            return FileResponse(
                path,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=86400"},
            )


app = create_app()
