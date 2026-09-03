"""Read-only список кейсов с маскированным контактом.

Управление статусами (approve/reject, симуляция отправки) — отдельный
модуль approval-эндпоинтов; здесь только наблюдение.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import SessionLocal
from app.models import AuditLog, LeadCase
from app.pii import mask_email, mask_phone, mask_text

router = APIRouter(prefix="/cases", tags=["cases"])


def _get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(_get_session)]


@router.get("", dependencies=[Depends(require_admin)])
def list_cases(session: SessionDep) -> list[dict]:
    rows = (
        session.execute(
            select(LeadCase).order_by(LeadCase.created_at.desc()).limit(200)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(c.id),
            "external_event_id": c.external_event_id,
            "source": c.source.value,
            "status": c.status.value,
            "consent": c.consent_status.value,
            "received_at": c.received_at.isoformat(),
            "contact_name": c.contact_name,
            "phone": mask_phone(c.phone),
            "email": mask_email(c.email),
            "manual_review_reason": c.manual_review_reason,
            "follow_up_due_at": (
                c.follow_up_due_at.isoformat() if c.follow_up_due_at else None
            ),
        }
        for c in rows
    ]


@router.get("/{case_id}", dependencies=[Depends(require_admin)])
def case_details(case_id: str, session: SessionDep) -> dict:
    """Полная карточка кейса: квалификация, черновик, задача синка, аудит.

    PII маскируется и здесь — менеджеру для решения маска достаточна,
    полный контакт лежит в CRM после синка.
    """
    try:
        parsed = uuid.UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
    case = session.get(LeadCase, parsed)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    qualification = case.qualification
    draft = case.draft
    sync_job = case.crm_sync_job
    audit = (
        session.execute(
            select(AuditLog)
            .where(AuditLog.case_id == case.id)
            .order_by(AuditLog.created_at)
        )
        .scalars()
        .all()
    )
    return {
        "id": str(case.id),
        "external_event_id": case.external_event_id,
        "source": case.source.value,
        "status": case.status.value,
        "consent": case.consent_status.value,
        "received_at": case.received_at.isoformat(),
        "contact": {
            "name": case.contact_name,
            "phone": mask_phone(case.phone),
            "email": mask_email(case.email),
        },
        "body_text_masked": mask_text(case.body_text),
        "manual_review_reason": case.manual_review_reason,
        "follow_up_due_at": (
            case.follow_up_due_at.isoformat() if case.follow_up_due_at else None
        ),
        "qualification": (
            {
                "provider": qualification.provider,
                "need": qualification.need,
                "urgency": qualification.urgency.value,
                "budget": float(qualification.budget) if qualification.budget else None,
                "budget_explicit": qualification.budget_explicit,
                "quality_score": qualification.quality_score,
                "reasons": qualification.reasons,
                "confidence": qualification.confidence,
            }
            if qualification
            else None
        ),
        "draft": (
            {
                "reply_text": draft.reply_text,
                "clarifying_question": draft.clarifying_question,
                "next_action": draft.next_action,
                "guardrail_report": draft.guardrail_report,
            }
            if draft
            else None
        ),
        "crm_sync": (
            {
                "status": sync_job.status.value,
                "attempts": sync_job.attempts,
                "deal_ref": sync_job.crm_deal_ref,
                "last_error": sync_job.last_error,
            }
            if sync_job
            else None
        ),
        "audit": [
            {
                "actor": entry.actor,
                "action": entry.action,
                "details": entry.details,
                "at": entry.created_at.isoformat(),
            }
            for entry in audit
        ],
    }
