"""Загрузка 30 синтетических лидов через настоящие вебхуки.

    docker compose run --rm api python -m scripts.seed

Лиды идут тем же путём, что и боевые: HTTP -> валидация -> нормализация ->
дедуп -> конвейер (квалификация + черновики). После отправки прогоняем
конвейер до успокоения, чтобы верификация видела финальные статусы.
"""

import json
import time
from pathlib import Path

import httpx
import structlog

from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import configure_logging
from app.schemas import IngestResponse
from app.worker.pipeline import run_once

configure_logging("seed")
log = structlog.get_logger("seed")

SEED_FILE = Path("seed/leads.json")


def _api_url(path: str) -> str:
    # compose run видит сервис api по внутреннему DNS-имени.
    return "/".join(("http://api:8000", path))


def _headers(source: str) -> dict:
    settings = get_settings()
    tokens = {
        "telegram": settings.webhook_token_telegram,
        "website": settings.webhook_token_website,
        "avito": settings.webhook_token_avito,
    }
    return {"X-Webhook-Token": tokens[source]}


def _prepare_payload(source: str, payload: dict) -> dict:
    """Свежие даты в событиях — чтобы метрики времени выглядели правдоподобно."""
    payload = dict(payload)
    if source == "telegram" and "message" in payload:
        payload["message"] = {**payload["message"], "date": int(time.time())}
    return payload


def main() -> None:
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    leads = data["leads"]
    resend_ids = data["resend"]
    by_id = {lead["id"]: lead for lead in leads}

    accepted = duplicated = invalid = 0
    with httpx.Client(timeout=10.0) as client:
        for lead in leads:
            response = client.post(
                "/".join((_api_url("webhooks"), lead["source"])),
                json=_prepare_payload(lead["source"], lead["payload"]),
                headers=_headers(lead["source"]),
            )
            if response.status_code == 200:
                status = IngestResponse.model_validate(response.json()).status
            else:
                status = "invalid"
            if status == "accepted":
                accepted += 1
            elif status == "duplicate":
                duplicated += 1
            else:
                invalid += 1
            print(f"{lead['id']:<9} {lead['category']:<11} -> {status}")

        for lead_id in resend_ids:
            lead = by_id[lead_id]
            response = client.post(
                "/".join((_api_url("webhooks"), lead["source"])),
                json=_prepare_payload(lead["source"], lead["payload"]),
                headers=_headers(lead["source"]),
            )
            status = (
                response.json().get("status", "invalid")
                if response.status_code == 200
                else "invalid"
            )
            if status == "duplicate":
                duplicated += 1
            elif status == "accepted":
                accepted += 1
            else:
                invalid += 1
            print(f"{lead_id:<9} {'повтор':<11} -> {status}")

    # Прогоняем конвейер, пока есть работа (квалификация + черновики).
    for _ in range(20):
        with SessionLocal() as session:
            stats = run_once(session)
        if stats["claimed"] == 0 and stats["drafted"] == 0:
            break

    print(
        f"\nИтого событий: {len(leads) + len(resend_ids)}"
        f" | принято: {accepted} | дублей: {duplicated} | невалидных: {invalid}"
    )
    print("Проверка маршрутов: python -m scripts.verify_routes")


if __name__ == "__main__":
    main()
