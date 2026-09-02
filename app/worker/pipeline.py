"""Конвейер воркера: новые кейсы -> квалификация -> статус.

Ключевая гарантия: провайдер (mock или LLM) видит только маскированный
текст без телефона и e-mail, а его ответ обязан пройти строгую схему —
всё, что мимо схемы, уводит кейс на ручную проверку, а не в работу.
"""

from datetime import UTC, datetime, timedelta

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CaseStatus, Urgency
from app.models import AuditLog, LeadCase, Qualification
from app.pii import mask_text
from app.providers import (
    ProviderUnavailableError,
    QualificationInvalidError,
    QualificationProvider,
    get_qualifier,
)
from app.schemas import LeadForQualification, QualificationResult

log = structlog.get_logger("pipeline")

BATCH_SIZE = 10

_FOLLOW_UP_BY_URGENCY = {
    Urgency.HIGH: timedelta(hours=1),
    Urgency.MEDIUM: timedelta(hours=4),
    Urgency.LOW: timedelta(hours=24),
}


def run_once(
    session: Session, provider: QualificationProvider | None = None
) -> dict[str, int]:
    """Один проход конвейера: забрать партию новых кейсов и оценить."""
    if provider is None:
        provider = get_qualifier()

    stats = {"claimed": 0, "qualified": 0, "manual_review": 0, "postponed": 0, "errors": 0}
    cases = (
        session.execute(
            select(LeadCase)
            .where(LeadCase.status == CaseStatus.NEW)
            .order_by(LeadCase.received_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    stats["claimed"] = len(cases)

    for case in cases:
        try:
            outcome = _qualify_case(session, case, provider)
        except ProviderUnavailableError:
            # API недоступен: кейс остаётся в new и будет повторён.
            session.rollback()
            stats["postponed"] += 1
            log.warning("qualification_postponed", case_id=str(case.id))
            continue
        except Exception:
            session.rollback()
            stats["errors"] += 1
            log.exception("qualification_failed", case_id=str(case.id))
            continue
        stats[outcome] += 1
    return stats


def _qualify_case(
    session: Session, case: LeadCase, provider: QualificationProvider
) -> str:
    lead_input = LeadForQualification(
        source=case.source,
        body_text_masked=mask_text(case.body_text),
    )

    try:
        raw_result = provider.qualify(lead_input)
        result = QualificationResult.model_validate(raw_result.model_dump())
    except (ValidationError, QualificationInvalidError) as exc:
        # Обязательный сценарий задания: невалидный ответ модели
        # переводит обращение в manual_review, а не в работу.
        _save_manual_review(session, case, provider.name, detail=str(exc))
        return "manual_review"

    if result.manual_review:
        _save_manual_review(
            session, case, provider.name, detail=result.reasons[0], result=result
        )
        return "manual_review"

    _save_qualified(session, case, provider.name, result)
    return "qualified"


def _save_qualified(
    session: Session, case: LeadCase, provider_name: str, result: QualificationResult
) -> None:
    case.status = CaseStatus.QUALIFIED
    case.manual_review_reason = None
    case.follow_up_due_at = datetime.now(tz=UTC) + _FOLLOW_UP_BY_URGENCY[result.urgency]
    session.add(_qualification_row(case, provider_name, result))
    session.add(
        AuditLog(
            case_id=case.id,
            actor="qualifier:" + provider_name,
            action="qualification_done",
            details={
                "status": CaseStatus.QUALIFIED.value,
                "quality_score": result.quality_score,
                "urgency": result.urgency.value,
                "budget_explicit": result.budget_explicit,
            },
        )
    )
    session.commit()
    log.info(
        "case_qualified",
        case_id=str(case.id),
        quality_score=result.quality_score,
        urgency=result.urgency.value,
    )


def _save_manual_review(
    session: Session,
    case: LeadCase,
    provider_name: str,
    detail: str,
    result: QualificationResult | None = None,
) -> None:
    case.status = CaseStatus.MANUAL_REVIEW
    case.manual_review_reason = detail[:1000]
    case.follow_up_due_at = datetime.now(tz=UTC) + timedelta(hours=2)
    if result is not None:
        session.add(_qualification_row(case, provider_name, result))
    session.add(
        AuditLog(
            case_id=case.id,
            actor="qualifier:" + provider_name,
            action="qualification_manual_review",
            details={"reason": detail[:500]},
        )
    )
    session.commit()
    log.info("case_manual_review", case_id=str(case.id))


def _qualification_row(
    case: LeadCase, provider_name: str, result: QualificationResult
) -> Qualification:
    return Qualification(
        case_id=case.id,
        provider=provider_name,
        need=result.need,
        urgency=result.urgency,
        budget=result.budget,
        budget_explicit=result.budget_explicit,
        quality_score=result.quality_score,
        reasons=list(result.reasons),
        confidence=result.confidence,
        manual_review=result.manual_review,
    )
