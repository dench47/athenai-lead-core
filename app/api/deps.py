"""Общие зависимости API: авторизация вебхуков и админ-эндпоинтов."""

import hmac

from fastapi import Header, HTTPException

from app.config import get_settings


def verify_webhook_token(source_token: str, provided: str | None) -> None:
    """Сравнение токенов без утечки по времени (constant-time)."""
    if provided is None or not hmac.compare_digest(provided, source_token):
        raise HTTPException(status_code=401, detail="invalid webhook token")


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if x_admin_token is None or not hmac.compare_digest(
        x_admin_token, settings.admin_token
    ):
        raise HTTPException(status_code=401, detail="invalid admin token")
