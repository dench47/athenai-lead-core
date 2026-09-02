"""Точка входа REST API (FastAPI)."""

from fastapi import FastAPI

from app import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="ATHENAI Lead Recovery & Sales CRM Core",
        version=__version__,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
