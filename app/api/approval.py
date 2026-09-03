"""Approval-эндпоинты: слово человека — закон.

Правила из задания:
- ни одно внешнее действие не выполняется автоматически;
- симуляция отправки возможна только после явного одобрения (approved);
- решения человека фиксируются в audit_log.

Смена решения тоже предусмотрена: reject переводит кейс в rejected
(терминальный статус: ни синка, ни отправки).
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import SessionLocal
from app.enums import CaseStatus, SyncStatus
from app.models import AuditLog, CrmSyncJob, LeadCase

router = APIRouter(prefix="/cases", tags=["approval"])


def _get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(_get_session)]


class DecisionRequest(BaseModel):
    """Необязательный комментарий менеджера к решению."""

    reason: str | None = None


def _load_case(session: Session, case_id: str) -> LeadCase:
    try:
        parsed = uuid.UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
    case = session.get(LeadCase, parsed)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


def _audit(session: Session, case: LeadCase, action: str, details: dict) -> None:
    session.add(
        AuditLog(case_id=case.id, actor="manager", action=action, details=details)
    )


@router.post("/{case_id}/approve", dependencies=[Depends(require_admin)])
def approve_case(case_id: str, session: SessionDep,
                 request: DecisionRequest | None = None) -> dict:
    case = _load_case(session, case_id)
    prev_status = case.status

    allowed = prev_status in (CaseStatus.AWAITING_APPROVAL, CaseStatus.MANUAL_REVIEW)
    has_artifacts = case.qualification is not None and case.draft is not None
    if not allowed or (prev_status == CaseStatus.MANUAL_REVIEW and not has_artifacts):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approve forbidden from status " + prev_status.value,
                "hint": (
                    "одобрить можно кейс в awaiting_approval или "
                    "manual_review с готовым черновиком"
                ),
            },
        )

    case.status = CaseStatus.APPROVED
    case.manual_review_reason = None
    # Задача синка создаётся один раз, с фиксированным ключом идемпотентности.
    session.add(
        CrmSyncJob(
            case_id=case.id,
            idempotency_key="deal:" + str(case.id),
            status=SyncStatus.PENDING,
        )
    )
    _audit(
        session,
        case,
        "approved",
        {
            "from_status": prev_status.value,
            "reason": request.reason if request else None,
        },
    )
    session.commit()
    return {"status": "approved", "case_id": str(case.id)}


@router.post("/{case_id}/reject", dependencies=[Depends(require_admin)])
def reject_case(case_id: str, session: SessionDep,
                request: DecisionRequest | None = None) -> dict:
    case = _load_case(session, case_id)
    if case.status not in (CaseStatus.AWAITING_APPROVAL, CaseStatus.MANUAL_REVIEW):
        raise HTTPException(
            status_code=409,
            detail={"error": "reject forbidden from status " + case.status.value},
        )
    reason = (request.reason if request else None) or "Отклонено менеджером"
    case.status = CaseStatus.REJECTED
    case.manual_review_reason = reason
    _audit(session, case, "rejected", {"reason": reason})
    session.commit()
    return {"status": "rejected", "case_id": str(case.id), "reason": reason}


@router.post("/{case_id}/send/simulate", dependencies=[Depends(require_admin)])
def simulate_send(case_id: str, session: SessionDep) -> dict:
    """Симуляция отправки черновика клиенту — только после одобрения.

    Реальной отправки не существует by design: это единственная «дверь»
    к внешнему действию, она открыта человеку и всё фиксирует в аудите.
    """
    case = _load_case(session, case_id)
    if case.status not in (CaseStatus.APPROVED, CaseStatus.CRM_SYNCED):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "send forbidden from status " + case.status.value,
                "hint": "внешнее действие возможно только после approve",
            },
        )
    draft = case.draft
    _audit(
        session,
        case,
        "send_simulated",
        {"channel": case.source.value, "reply_length": len(draft.reply_text)},
    )
    session.commit()
    return {
        "status": "send_simulated",
        "case_id": str(case.id),
        "channel": case.source.value,
        "would_send": draft.reply_text,
    }
