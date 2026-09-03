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

from app.crm.client import CRMClient, build_default_client
from app.crm.sync import sync_due_jobs
from app.enums import CaseStatus, Urgency
from app.guardrails import GUARDRAIL_CHECKS, check_draft
from app.models import AuditLog, Draft, LeadCase, Qualification
from app.pii import mask_text
from app.providers import (
    Drafter,
    ProviderUnavailableError,
    QualificationInvalidError,
    QualificationProvider,
    get_drafter,
    get_qualifier,
)
from app.schemas import DraftInput, DraftResult, LeadForQualification, QualificationResult

log = structlog.get_logger("pipeline")

BATCH_SIZE = 10

_FOLLOW_UP_BY_URGENCY = {
    Urgency.HIGH: timedelta(hours=1),
    Urgency.MEDIUM: timedelta(hours=4),
    Urgency.LOW: timedelta(hours=24),
}


def run_once(
    session: Session,
    provider: QualificationProvider | None = None,
    drafter: Drafter | None = None,
    crm_client: CRMClient | None = None,
) -> dict[str, int]:
    """Один проход конвейера: квалификация -> черновики -> синк с CRM."""
    if provider is None:
        provider = get_qualifier()
    if drafter is None:
        drafter = get_drafter()
    if crm_client is None:
        crm_client = build_default_client()

    stats = {
        "claimed": 0,
        "qualified": 0,
        "manual_review": 0,
        "postponed": 0,
        "errors": 0,
        "drafted": 0,
        "draft_manual_review": 0,
    }

    # Фаза 1: оценка новых обращений.
    for case in _claim_cases(session, CaseStatus.NEW):
        stats["claimed"] += 1
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

    # Фаза 2: черновики для оценённых кейсов.
    for case in _claim_cases(session, CaseStatus.QUALIFIED):
        try:
            draft_outcome = _draft_case(session, case, drafter)
        except ProviderUnavailableError:
            session.rollback()
            stats["postponed"] += 1
            log.warning("drafting_postponed", case_id=str(case.id))
            continue
        except Exception:
            session.rollback()
            stats["errors"] += 1
            log.exception("drafting_failed", case_id=str(case.id))
            continue
        stats[draft_outcome] += 1

    # Фаза 3: идемпотентный синк одобренных кейсов с CRM.
    stats.update(sync_due_jobs(session, crm_client))
    return stats


def _claim_cases(session: Session, status: CaseStatus) -> list[LeadCase]:
    """Забрать партию кейсов в заданном статусе.

    FOR UPDATE SKIP LOCKED: параллельные воркеры не дерутся за одни кейсы.
    """
    return list(
        session.execute(
            select(LeadCase)
            .where(LeadCase.status == status)
            .order_by(LeadCase.received_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )


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


def _draft_case(session: Session, case: LeadCase, drafter: Drafter) -> str:
    """Черновик для оценённого кейса + guardrails.

    Возвращает 'drafted' или 'draft_manual_review'.
    """
    qualification = case.qualification
    draft_input = DraftInput(
        source=case.source,
        need=qualification.need,
        urgency=qualification.urgency,
        budget_explicit=qualification.budget_explicit,
        body_text_masked=mask_text(case.body_text),
    )

    try:
        raw = drafter.compose(draft_input)
        result = DraftResult.model_validate(raw.model_dump())
    except (ValidationError, QualificationInvalidError) as exc:
        case.status = CaseStatus.MANUAL_REVIEW
        case.manual_review_reason = ("Генератор черновика: " + str(exc))[:1000]
        session.add(
            AuditLog(
                case_id=case.id,
                actor="drafter:" + drafter.name,
                action="draft_invalid",
                details={"detail": str(exc)[:500]},
            )
        )
        session.commit()
        return "draft_manual_review"

    violations = check_draft(result)
    session.add(
        Draft(
            case_id=case.id,
            reply_text=result.reply_text,
            clarifying_question=result.clarifying_question,
            next_action=result.next_action,
            guardrail_report={
                "violations": violations,
                "checks": list(GUARDRAIL_CHECKS),
            },
        )
    )

    if violations:
        # Требование задания: ИИ не называет цены/скидки/сроки/гарантии.
        # Нарушение — кейс человеку, черновик сохранён как улика.
        case.status = CaseStatus.MANUAL_REVIEW
        case.manual_review_reason = ("Guardrail: " + "; ".join(violations))[:1000]
        session.add(
            AuditLog(
                case_id=case.id,
                actor="drafter:" + drafter.name,
                action="draft_guardrail_violation",
                details={"violations": violations[:10]},
            )
        )
        session.commit()
        log.info("draft_guardrail_violation", case_id=str(case.id))
        return "draft_manual_review"

    case.status = CaseStatus.DRAFT_READY
    session.commit()

    case.status = CaseStatus.AWAITING_APPROVAL
    session.add(
        AuditLog(
            case_id=case.id,
            actor="drafter:" + drafter.name,
            action="awaiting_approval",
            details={"checks_passed": list(GUARDRAIL_CHECKS)},
        )
    )
    session.commit()
    log.info("draft_ready", case_id=str(case.id))
    return "drafted"
