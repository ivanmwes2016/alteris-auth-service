import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.v1 import api_router
from app.core.config import get_settings
from app.services.notifications import admin_recipients

log = logging.getLogger(__name__)

settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version="1.0.0", debug=settings.DEBUG)

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.FRONTEND_URL,
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "https://www.app.skolarly.net",
            "https://app.skolarly.net",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(api_router)

    if not admin_recipients():
        log.warning(
            "ADMIN_NOTIFY_EMAILS is not set: new schools will wait for approval "
            "without anyone being emailed"
        )

    return app


app = create_app()

# ── AWS Lambda handler (via API Gateway HTTP API or REST API) ─────────────────
# Mangum translates APIGW events → ASGI → FastAPI
handler = Mangum(app, lifespan="off")
