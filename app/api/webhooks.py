"""Вебхуки трёх источников. Авторизация: X-Webhook-Token на каждый источник."""

import json

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import verify_webhook_token
from app.config import get_settings
from app.db import SessionLocal
from app.enums import EventOutcome, LeadSource
from app.schemas import IngestResponse
from app.services.ingest import ingest_raw_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram", response_model=IngestResponse)
async def webhook_telegram(
    request: Request, x_webhook_token: str | None = Header(default=None)
) -> IngestResponse:
    settings = get_settings()
    verify_webhook_token(settings.webhook_token_telegram, x_webhook_token)
    return await _handle(request, LeadSource.TELEGRAM)


@router.post("/website", response_model=IngestResponse)
async def webhook_website(
    request: Request, x_webhook_token: str | None = Header(default=None)
) -> IngestResponse:
    settings = get_settings()
    verify_webhook_token(settings.webhook_token_website, x_webhook_token)
    return await _handle(request, LeadSource.WEBSITE)


@router.post("/avito", response_model=IngestResponse)
async def webhook_avito(
    request: Request, x_webhook_token: str | None = Header(default=None)
) -> IngestResponse:
    settings = get_settings()
    verify_webhook_token(settings.webhook_token_avito, x_webhook_token)
    return await _handle(request, LeadSource.AVITO)


async def _handle(request: Request, source: LeadSource) -> IngestResponse:
    # Читаем сырой body сами: даже битый JSON должен попасть в журнал,
    # а не умереть с 500 до какой-либо фиксации.
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    try:
        raw = json.loads(raw_body)
        if not isinstance(raw, dict):
            raise ValueError("payload must be a JSON object")
    except ValueError:
        raw = {}

    # БД-работа — в threadpool, чтобы не блокировать event loop.
    result = await run_in_threadpool(_ingest_sync, source, raw, raw_body)

    if result.outcome == EventOutcome.INVALID:
        raise HTTPException(
            status_code=422,
            detail={"status": "invalid", "detail": result.detail},
        )
    return IngestResponse(
        status=result.outcome.value,
        case_id=str(result.case.id) if result.case else None,
        external_event_id=result.case.external_event_id if result.case else None,
    )


def _ingest_sync(source: LeadSource, raw: dict, raw_body: str):
    with SessionLocal() as session:
        return ingest_raw_event(session, source, raw, raw_body)
