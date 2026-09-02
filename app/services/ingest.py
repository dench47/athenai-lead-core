"""Приём события: валидация -> нормализация -> журнал -> дедуп -> карточка.

Дедупликация построена по стратегии «вставка вперёд»: мы не ищем карточку
запросом, а просто вставляем её. Если такое обращение уже зарегистрировано,
UNIQUE(tenant_id, external_event_id) отклонит вставку, и событие честно
фиксируется как дубль. Это исключает гонки по определению — между проверкой
и вставкой нет промежутка, в который мог бы пролезть параллельный запрос.

Все обращения к БД идут через ORM (объекты и session.add/commit);
SQL-строки в модуле не собираются вовсе.
"""

import hashlib
from dataclasses import dataclass

import structlog
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import CaseStatus, ConsentStatus, EventOutcome, LeadSource
from app.models import DEFAULT_TENANT_ID, LeadCase, LeadEvent
from app.normalize import normalize_avito, normalize_telegram, normalize_website
from app.schemas import AvitoMessage, NormalizedLead, TelegramUpdate, WebsiteForm

log = structlog.get_logger("ingest")

_SOURCE_ADAPTERS: dict[LeadSource, tuple[type, object]] = {
    LeadSource.TELEGRAM: (TelegramUpdate, normalize_telegram),
    LeadSource.WEBSITE: (WebsiteForm, normalize_website),
    LeadSource.AVITO: (AvitoMessage, normalize_avito),
}


@dataclass(slots=True)
class IngestResult:
    outcome: EventOutcome
    case: LeadCase | None = None
    detail: str | None = None


def ingest_raw_event(
    session: Session, source: LeadSource, raw: dict, raw_body: str
) -> IngestResult:
    """Полный цикл приёма одного события (без HTTP-обвязки)."""
    schema, normalizer = _SOURCE_ADAPTERS[source]

    try:
        payload = TypeAdapter(schema).validate_python(raw)
    except ValidationError as exc:
        summary = str(exc.errors()[:5])
        _journal(
            session, source, _synthetic_event_id(raw_body), raw,
            EventOutcome.INVALID, summary,
        )
        return IngestResult(EventOutcome.INVALID, detail=summary)

    lead: NormalizedLead = normalizer(payload)
    case = _build_case(lead)
    session.add(case)

    try:
        _journal(session, source, lead.external_event_id, raw, EventOutcome.ACCEPTED)
    except IntegrityError:
        # Карточка с таким (tenant_id, external_event_id) уже есть:
        # повторная доставка, а не ошибка. Возврат не делаем — журнал
        # восстанавливаем отдельной транзакцией ниже.
        session.rollback()
        _journal(session, source, lead.external_event_id, raw, EventOutcome.DUPLICATE)
        log.info("event_duplicate", source=source.value,
                 external_event_id=lead.external_event_id)
        return IngestResult(EventOutcome.DUPLICATE)

    log.info("event_accepted", source=source.value,
             external_event_id=lead.external_event_id)
    return IngestResult(EventOutcome.ACCEPTED, case=case)


def _synthetic_event_id(raw_body: str) -> str:
    """Идентификатор для битого payload: валидировать нечего, но журнал
    должен зафиксировать и такие попытки."""
    digest = hashlib.sha256(raw_body.encode("utf-8", errors="replace")).hexdigest()
    return "-".join(("invalid", digest[:16]))


def _build_case(lead: NormalizedLead) -> LeadCase:
    return LeadCase(
        tenant_id=DEFAULT_TENANT_ID,
        external_event_id=lead.external_event_id,
        source=lead.source,
        received_at=lead.received_at,
        contact_name=lead.contact_name,
        phone=lead.phone,
        email=lead.email,
        body_text=lead.body_text,
        consent_status=lead.consent_status,
        status=(
            CaseStatus.OPT_OUT
            if lead.consent_status == ConsentStatus.OPT_OUT
            else CaseStatus.NEW
        ),
    )


def _journal(
    session: Session,
    source: LeadSource,
    external_event_id: str,
    raw: dict,
    outcome: EventOutcome,
    error_detail: str | None = None,
) -> None:
    session.add(
        LeadEvent(
            tenant_id=DEFAULT_TENANT_ID,
            source=source,
            external_event_id=external_event_id,
            payload=raw if isinstance(raw, dict) else {"raw": str(raw)[:2000]},
            outcome=outcome,
            error_detail=error_detail,
        )
    )
    session.commit()
