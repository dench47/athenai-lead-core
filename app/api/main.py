"""Точка входа REST API (FastAPI)."""

from fastapi import FastAPI

from app import __version__
from app.api import approval, cases, webhooks
from app.logging_setup import configure_logging

configure_logging("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ATHENAI Lead Recovery & Sales CRM Core",
        version=__version__,
    )
    app.include_router(webhooks.router)
    app.include_router(cases.router)
    app.include_router(approval.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
