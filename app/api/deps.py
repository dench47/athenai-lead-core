"""Общие зависимости API: авторизация и сессия БД."""

import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal


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


def _get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(_get_session)]
